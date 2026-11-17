"""Server-sent events for a slow answer: the AI notice first (rule 6), `stage` events while the
pipeline works, then one `answer` event once every check has passed (rule 1: no tokens are
streamed before the answer check), or `error`. Used by the student chat and the teacher's
persona preview.
"""

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator

from fastapi.responses import StreamingResponse

from core.chat import AI_NOTICE

log = logging.getLogger(__name__)
KEEP_ALIVE_SECONDS = 15
UNAVAILABLE = "Asistent trenutačno nije dostupan. Pokušajte ponovno za nekoliko minuta."


class UserFacingError(Exception):
    """An error whose message may be shown as is (anything else becomes UNAVAILABLE)."""


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def stream_answer(run: Callable[[Callable[[str], None]], dict]) -> StreamingResponse:
    """`run(on_stage)` does the work in its own thread (to the end, even if the client leaves)
    and returns the `answer` event's data."""
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            events.put(("answer", run(lambda st: events.put(("stage", st)))))
        except UserFacingError as e:
            events.put(("error", str(e)))
        except Exception:
            log.exception("answer failed")
            events.put(("error", UNAVAILABLE))

    threading.Thread(target=work, daemon=True).start()

    def stream() -> Iterator[str]:
        yield sse("notice", {"ai_notice": AI_NOTICE})
        while True:
            try:
                kind, value = events.get(timeout=KEEP_ALIVE_SECONDS)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if kind == "stage":
                yield sse("stage", {"stage": value})
                continue
            yield sse(kind, value if kind == "answer" else {"detail": value})
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
