"""Tracing: model calls are recorded only inside `recording()`, and one answer becomes one
OTLP trace for Langfuse (root span + a generation per chat call), sent only when configured."""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core import tracing
from core.settings import get_settings
from llm import client
from llm.recording import recording


def _fake_completion(**kw):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Odgovor [1]."))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=8, total_tokens=128),
    )


def test_calls_are_recorded_only_while_recording(monkeypatch) -> None:
    monkeypatch.setattr(client.litellm, "completion", _fake_completion)
    client.complete("answer", [{"role": "user", "content": "Što je klasa?"}])  # not recorded
    with recording() as calls:
        assert client.complete("answer", [{"role": "user", "content": "Što je klasa?"}])
    assert len(calls) == 1
    c = calls[0]
    assert c.role == "answer" and c.kind == "chat" and c.output == "Odgovor [1]."
    assert c.usage == {"input": 120, "output": 8, "total": 128} and c.ended >= c.started


def test_one_answer_is_one_trace(monkeypatch) -> None:
    monkeypatch.setattr(client.litellm, "completion", _fake_completion)
    with recording() as calls:
        client.complete("fast", [{"role": "user", "content": "x"}])
        client.complete("answer", [{"role": "user", "content": "y"}])
    now = datetime.now(UTC)
    body = tracing.build(
        "answer",
        now - timedelta(seconds=3),
        now,
        input="Što je klasa?",
        output="Odgovor [1].",
        metadata={"kb_version_id": "v-1", "persona_version": 2, "model_config_version": "dev-1"},
        tags=["chat"],
        calls=calls,
    )
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, *children = spans
    assert len(children) == 2 and {s["traceId"] for s in spans} == {root["traceId"]}
    assert all(s["parentSpanId"] == root["spanId"] for s in children)
    attrs = {a["key"]: a["value"] for a in root["attributes"]}
    assert attrs["langfuse.trace.metadata.kb_version_id"] == {"stringValue": "v-1"}
    assert attrs["langfuse.trace.metadata.persona_version"] == {"intValue": "2"}
    assert attrs["langfuse.trace.tags"]["arrayValue"]["values"] == [{"stringValue": "chat"}]
    gen = {a["key"]: a["value"] for a in children[1]["attributes"]}
    assert gen["langfuse.observation.type"] == {"stringValue": "generation"}
    assert gen["gen_ai.usage.input_tokens"] == {"intValue": "120"}


def test_sent_only_when_configured(monkeypatch) -> None:
    posted = []
    monkeypatch.setattr(tracing, "_post", posted.append)

    class NoThread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(tracing.threading, "Thread", NoThread)
    s = get_settings()
    now = datetime.now(UTC)
    kw = dict(input="q", output="a", metadata={}, tags=[], calls=[])
    monkeypatch.setattr(s, "langfuse_host", None)
    tracing.send("answer", now, now, **kw)
    assert posted == []
    monkeypatch.setattr(s, "langfuse_host", "http://langfuse.test")
    monkeypatch.setattr(s, "langfuse_public_key", "pk-lf-1")
    monkeypatch.setattr(s, "langfuse_secret_key", "sk-lf-1")
    tracing.send("answer", now, now, **kw)
    assert len(posted) == 1


def test_post_uses_basic_auth_and_never_raises(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "langfuse_host", "http://langfuse.test/")
    monkeypatch.setattr(s, "langfuse_public_key", "pk")
    monkeypatch.setattr(s, "langfuse_secret_key", "sk")
    seen = {}

    def post(url, json, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"])
        raise tracing.httpx.ConnectError("down")

    monkeypatch.setattr(tracing.httpx, "post", post)
    tracing._post({"resourceSpans": []})  # logs, does not raise
    assert seen["url"] == "http://langfuse.test/api/public/otel/v1/traces"
    assert seen["auth"] == "Basic " + base64.b64encode(b"pk:sk").decode()


@pytest.mark.parametrize(
    "value,expected", [(True, "boolValue"), (1.5, "doubleValue"), ({"a": 1}, "stringValue")]
)
def test_attribute_values(value, expected) -> None:
    assert expected in tracing._value(value)
