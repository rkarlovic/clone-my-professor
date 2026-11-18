"""Traces to Langfuse (self-hosted, inside the installation): one trace per answered question,
with every model call in it (role, exact model id, prompt and reply, tokens, time).

Sent as OpenTelemetry (OTLP/HTTP JSON) to Langfuse's `/api/public/otel/v1/traces`, with the
attribute names Langfuse reads (`langfuse.*`) next to the OpenTelemetry GenAI ones (`gen_ai.*`):
no Langfuse SDK, and any OTLP collector could receive the same spans. Langfuse 4's older
ingestion API is deprecated, so it is not used.

Off unless LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set. Sending happens in
a background thread and never slows down or breaks an answer; failures are only logged.

Rule 9: a trace holds the student's question and the answer (student text). It goes only to
the installation's own Langfuse; no student reference is sent. Langfuse keeps it until deleted
there (set a data-retention period in the Langfuse project to match RETENTION_DAYS).
"""

import base64
import json
import logging
import secrets
import threading
from datetime import datetime
from typing import Any

import httpx

from core.settings import get_settings
from llm.recording import Call

log = logging.getLogger(__name__)


def enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_host and s.langfuse_public_key and s.langfuse_secret_key)


def _ns(t: datetime) -> str:
    return str(int(t.timestamp() * 1_000_000_000))


def _value(v: Any) -> dict:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return {"arrayValue": {"values": [{"stringValue": x} for x in v]}}
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False, default=str)
    return {"stringValue": v}


def _attrs(d: dict[str, Any]) -> list[dict]:
    return [{"key": k, "value": _value(v)} for k, v in d.items() if v is not None]


def _span(trace_id, span_id, parent, name, start, end, attrs, error=None) -> dict:
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": _ns(start),
        "endTimeUnixNano": _ns(end),
        "attributes": _attrs(attrs),
        "status": {"code": 2, "message": error} if error else {"code": 1},
    }
    if parent:
        span["parentSpanId"] = parent
    return span


def build(
    name: str,
    started: datetime,
    ended: datetime,
    *,
    input: Any,
    output: Any,
    metadata: dict[str, Any],
    tags: list[str],
    calls: list[Call],
) -> dict:
    """The OTLP/JSON body for one trace: a root span and one child span per model call."""
    trace_id, root = secrets.token_hex(16), secrets.token_hex(8)
    root_attrs = {
        "langfuse.trace.name": name,
        "langfuse.trace.input": input,
        "langfuse.trace.output": output,
        "langfuse.trace.tags": tags,
        "langfuse.observation.input": input,
        "langfuse.observation.output": output,
    } | {f"langfuse.trace.metadata.{k}": v for k, v in metadata.items()}
    spans = [_span(trace_id, root, None, name, started, ended, root_attrs)]
    for c in calls:
        attrs = {
            "langfuse.observation.type": "generation" if c.kind == "chat" else "span",
            "langfuse.observation.model.name": c.model,
            "langfuse.observation.input": c.input,
            "langfuse.observation.output": c.output,
            "langfuse.observation.model.parameters": c.params or None,
            "langfuse.observation.metadata.role": c.role,
            "gen_ai.operation.name": c.kind,
            "gen_ai.request.model": c.model,
            "gen_ai.usage.input_tokens": c.usage.get("input"),
            "gen_ai.usage.output_tokens": c.usage.get("output"),
        }
        spans.append(
            _span(
                trace_id,
                secrets.token_hex(8),
                root,
                c.role,
                c.started,
                c.ended or c.started,
                attrs,
                c.error,
            )
        )
    resource = _attrs({"service.name": "cmp", "deployment.environment": get_settings().auth_mode})
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource},
                "scopeSpans": [{"scope": {"name": "cmp"}, "spans": spans}],
            }
        ]
    }


def _post(body: dict) -> None:
    s = get_settings()
    auth = base64.b64encode(f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()).decode()
    try:
        r = httpx.post(
            f"{s.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            json=body,
            headers={"Authorization": f"Basic {auth}"},
            timeout=10,
        )
        if r.status_code >= 300:
            log.warning("langfuse refused a trace: HTTP %s %s", r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        log.warning("langfuse not reachable: %s", type(e).__name__)


def send(name: str, started: datetime, ended: datetime, **kw: Any) -> None:
    """Send one trace in the background (no-op when Langfuse is not configured)."""
    if not enabled():
        return
    try:
        body = build(name, started, ended, **kw)
    except Exception:  # a trace must never break an answer
        log.exception("could not build a trace")
        return
    threading.Thread(target=_post, args=(body,), daemon=True).start()
