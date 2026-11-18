"""Records the model calls made while answering one question, so they can be traced together
(core/tracing.py sends them to Langfuse). Nothing is recorded outside `recording()`.

A context variable, so parallel answers (threads, the chat's worker threads) keep their own lists.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class Call:
    role: str
    model: str
    kind: str  # chat | embedding | rerank
    started: datetime
    ended: datetime | None = None
    input: Any = None
    output: Any = None
    usage: dict[str, int] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


_calls: ContextVar[list[Call] | None] = ContextVar("cmp_model_calls", default=None)


@contextmanager
def recording() -> Iterator[list[Call]]:
    calls: list[Call] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


@contextmanager
def record(role: str, model: str, kind: str, input: Any, params: dict | None = None):
    """Wrap one model call; yields the Call (to fill in output and usage), or None if off."""
    calls = _calls.get()
    if calls is None:
        yield None
        return
    call = Call(role, model, kind, datetime.now(UTC), input=input, params=dict(params or {}))
    started = time.monotonic()
    try:
        yield call
    except Exception as e:
        call.error = type(e).__name__
        raise
    finally:
        call.ended = call.started + timedelta(seconds=time.monotonic() - started)
        calls.append(call)


def usage_of(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out = {}
    for ours, theirs in (
        ("input", "prompt_tokens"),
        ("output", "completion_tokens"),
        ("total", "total_tokens"),
    ):
        value = getattr(u, theirs, None) if not isinstance(u, dict) else u.get(theirs)
        if isinstance(value, int):
            out[ours] = value
    return out
