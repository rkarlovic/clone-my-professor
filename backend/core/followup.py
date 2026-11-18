"""Chat follow-ups: "a što je s drugim?" right after an answer only makes sense with that answer.

The answer pipeline (core/answer.py) answers single questions; that is how it was built and
measured, and its checks assume it. So instead of passing the conversation on, a follow-up is
rewritten (`fast` role) into a question that stands on its own, and that is what gets answered.
Both are stored (the student's words and the rewritten question); the teacher's queue gets the
rewritten one, which is readable without the conversation.

Only short questions shortly after an earlier exchange are sent to the model; anything else is
used as asked (a long question almost always stands on its own, and every model call costs time).
A rewrite that is empty, very long or several lines is not trusted: the question is used as asked.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from llm.client import complete
from llm.prompts import prompt

WINDOW = timedelta(minutes=30)  # an older exchange is not "the conversation" any more
MAX_WORDS = 15  # longer questions are used as asked
TURNS = 2  # earlier exchanges shown to the model
ANSWER_CHARS = 600  # of each earlier answer


def needs_rewrite(question: str, last_at: datetime | None, now: datetime | None = None) -> bool:
    if last_at is None:
        return False
    now = now or datetime.now(UTC)
    return now - last_at <= WINDOW and len(question.split()) <= MAX_WORDS


def _history(exchanges: list[tuple[str, str]]) -> str:
    lines = []
    for q, a in exchanges[-TURNS:]:
        a = re.sub(r"\s*\[\d+\]", "", a)  # citation markers mean nothing without the sources
        lines += [f"Student: {q}", f"Asistent: {a[:ANSWER_CHARS]}"]
    return "\n".join(lines)


def rewrite(
    subject: str,
    exchanges: list[tuple[str, str]],
    question: str,
    complete_fn: Callable[..., str] = complete,
) -> str | None:
    """The question rewritten to stand on its own, or None to use it as asked."""
    reply = complete_fn(
        "fast",
        [
            {
                "role": "user",
                "content": prompt("followup_rewrite", "fast").format(
                    subject=subject, history=_history(exchanges), question=question
                ),
            }
        ],
    )
    lines = [line.strip() for line in reply.strip().splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    text = lines[0].strip().strip("\"„“”'").strip()
    text = re.sub(r"^(novo pitanje|pitanje)\s*:\s*", "", text, flags=re.IGNORECASE)
    if not text or len(text) > 3 * max(len(question), 40) or text.lower() == question.lower():
        return None
    return text
