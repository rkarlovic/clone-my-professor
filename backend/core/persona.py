"""The teacher's persona: how the clone talks to students (instruction layer 2 of 3).

Structured settings plus one free-text field, rendered into a short Croatian instruction block
that the answer prompt places *after* the platform rules ("Upute nastavnika (vrijede samo ako
se ne kose s pravilima iznad)"), so rules 1-6 always win: a persona can change tone, length and
the kind of help, never what the clone may answer from or that it cites.

Lifecycle: draft (editable, previewable) -> checking (a job answers a sample of the course's
check questions and the generic forward questions with it) -> active (the chat uses it; the
previous one is retired) or rejected (too few forwarded or answered; the report says which).
"""

import random
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.approval import active_version
from core.checks import valid_questions
from core.checkset import GENERIC_ABSTAIN
from core.db import app_engine, kb_scope
from core.models import Document, KnowledgeBase, PersonaConfig
from core.notify import notify_teachers
from core.settings import get_settings
from llm.prompts import DONT_KNOW

MAX_ANSWER_SHOWN = 600


class PersonaSettings(BaseModel):
    address: Literal["vi", "ti"] = "vi"
    answer_length: Literal["short", "medium", "long"] = "medium"
    # Never a full solution (rule 4); this only sets how far the help goes.
    exercise_help: Literal["hints", "guided", "concepts"] = "hints"
    examples: bool = True
    terminology: str = Field("", max_length=300)
    # Topics the clone does not answer: it says so and forwards to the teacher.
    forward_topics: list[str] = Field(default_factory=list, max_length=20)


FREE_TEXT_MAX = 2000

_ADDRESS = {
    "vi": "Studentu se obraćaj s „Vi” (persiranje).",
    "ti": "Studentu se obraćaj s „ti”.",
}
_LENGTH = {
    "short": "Odgovaraj kratko: dvije do četiri rečenice.",
    "medium": "Odgovaraj srednje opširno: jedan do dva kratka odlomka.",
    "long": "Kad tema to traži, odgovaraj opširnije, u više odlomaka.",
}
_EXERCISES = {
    "hints": (
        "Kod zadataka i vježbi daj samo smjernice i potpitanja koja studenta vode do rješenja."
    ),
    "guided": "Kod zadataka i vježbi opiši korake pristupa, ali ne i konačno rješenje.",
    "concepts": "Kod zadataka i vježbi objasni samo pojmove koji su potrebni, ne i postupak.",
}


def render(settings: PersonaSettings, free_text: str = "") -> str:
    """The persona as instruction lines (Croatian, the platform language)."""
    lines = [
        _ADDRESS[settings.address],
        _LENGTH[settings.answer_length],
        _EXERCISES[settings.exercise_help],
        "Kad pomažu razumijevanju, koristi primjere i usporedbe iz izvora."
        if settings.examples
        else "Ne dodaj usporedbe ni primjere osim onih iz izvora; drži se objašnjenja.",
    ]
    if settings.terminology.strip():
        lines.append(f"Nazivlje: {settings.terminology.strip()}")
    topics = [t.strip() for t in settings.forward_topics if t.strip()]
    if topics:
        joined = "; ".join(topics)
        lines.append(f"Na pitanja o ovim temama napiši samo {DONT_KNOW}: {joined}.")
    if free_text.strip():
        lines.append("Dodatno: " + free_text.strip())
    return "\n".join(f"- {line}" for line in lines)


def persona_text(p: PersonaConfig) -> str:
    return render(PersonaSettings(**p.settings), p.free_text)


class PersonaError(ValueError):
    """The step is not allowed in the persona's state; the message says why."""


def active_persona(session: Session, kb_id: uuid.UUID) -> PersonaConfig | None:
    return session.scalars(
        select(PersonaConfig).where(PersonaConfig.kb_id == kb_id, PersonaConfig.status == "active")
    ).first()


def save_draft(
    session: Session,
    kb_id: uuid.UUID,
    teacher_id: uuid.UUID,
    settings: PersonaSettings,
    free_text: str,
) -> PersonaConfig:
    """Update the course's open draft, or start a new draft version."""
    if len(free_text) > FREE_TEXT_MAX:
        raise PersonaError(f"the free text is longer than {FREE_TEXT_MAX} characters")
    session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id == kb_id).with_for_update())
    draft = session.scalars(
        select(PersonaConfig).where(PersonaConfig.kb_id == kb_id, PersonaConfig.status == "draft")
    ).first()
    if draft is None:
        latest = session.scalar(
            select(func.max(PersonaConfig.version)).where(PersonaConfig.kb_id == kb_id)
        )
        draft = PersonaConfig(kb_id=kb_id, version=(latest or 0) + 1, status="draft")
        session.add(draft)
    draft.settings = settings.model_dump()
    draft.free_text = free_text.strip()
    draft.created_by = teacher_id
    session.flush()
    return draft


def start_activation(session: Session, persona: PersonaConfig) -> PersonaConfig:
    """Draft -> checking; the caller queues the check job."""
    session.execute(
        select(KnowledgeBase.id).where(KnowledgeBase.id == persona.kb_id).with_for_update()
    )
    session.refresh(persona)
    if persona.status != "draft":
        raise PersonaError(f"persona {persona.version} is {persona.status}, not a draft")
    if active_version(session, persona.kb_id) is None:
        raise PersonaError("publish the course materials first: the check needs them")
    busy = session.scalars(
        select(PersonaConfig).where(
            PersonaConfig.kb_id == persona.kb_id, PersonaConfig.status == "checking"
        )
    ).first()
    if busy:
        raise PersonaError(f"persona {busy.version} is still being checked")
    persona.status, persona.approved_at = "checking", datetime.now(UTC)
    session.flush()
    return persona


def check_sample(session: Session, kb_id: uuid.UUID, version_id: uuid.UUID, n: int, seed: int = 0):
    """Up to n of the course's check questions valid for the version, spread over files."""
    with kb_scope(version_id) as s:
        docs = dict(s.execute(select(Document.file_name, Document.file_hash)).all())
    pool = valid_questions(session, kb_id, docs)
    if len(pool) <= n:
        return pool
    step = len(pool) / n
    rng = random.Random(seed)
    return [pool[min(len(pool) - 1, int(i * step + rng.random() * step))] for i in range(n)]


def run_persona_check(persona_id: uuid.UUID, *, answer_fn: Callable | None = None) -> dict:
    """Answer the sample with the persona and decide. Platform rules stay in force (rule 2):
    the generic questions must still be forwarded; the course's own questions still answered."""
    from core.answer import answer_question

    answer_fn = answer_fn or answer_question
    cfg = get_settings()
    started = time.time()
    with Session(app_engine()) as s:
        persona = s.get(PersonaConfig, persona_id)
        version = active_version(s, persona.kb_id)
        if version is None:
            raise PersonaError("the course has no published version")
        text, number = persona_text(persona), persona.version
        sample = [
            (q.question, q.file_name, q.page)
            for q in check_sample(s, persona.kb_id, version.id, cfg.persona_check_questions)
        ]
    items, answered, cites_expected, forwarded = [], 0, 0, 0
    for question, file_name, page in sample:
        a = answer_fn(version.id, question, persona_text=text, persona_version=number)
        ok = a.forwarded is None
        answered += ok
        hit = any(c.file_name == file_name and page in c.pages for c in a.cited)
        cites_expected += hit
        items.append(_item("check", question, a, expected=f"{file_name}, str. {page}", hit=hit))
    for kind, question in GENERIC_ABSTAIN:
        a = answer_fn(version.id, question, persona_text=text, persona_version=number)
        forwarded += a.forwarded is not None
        items.append(_item(kind, question, a))

    n_check, n_generic = len(sample), len(GENERIC_ABSTAIN)
    answered_rate = answered / n_check if n_check else None
    forwarded_rate = forwarded / n_generic
    reasons = []
    if forwarded_rate < cfg.persona_min_forwarded:
        reasons.append(
            f"proslijeđeno {forwarded} od {n_generic} pitanja koja asistent ne smije odgovoriti"
        )
    if answered_rate is not None and answered_rate < cfg.persona_min_answered:
        reasons.append(f"odgovoreno {answered} od {n_check} pitanja iz materijala")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "check_questions": n_check,
        "answered": answered,
        "answered_rate": round(answered_rate, 3) if answered_rate is not None else None,
        "cites_expected": cites_expected,
        "generic": n_generic,
        "forwarded": forwarded,
        "forwarded_rate": round(forwarded_rate, 3),
        "thresholds": {
            "min_forwarded": cfg.persona_min_forwarded,
            "min_answered": cfg.persona_min_answered,
        },
        "kb_version": version.version,
        "items": items,
        "seconds": round(time.time() - started, 1),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _item(kind: str, question: str, a, expected: str | None = None, hit: bool = False) -> dict:
    return {
        "kind": kind,
        "question": question,
        "forwarded": a.forwarded.value if a.forwarded else None,
        "answer": a.text[:MAX_ANSWER_SHOWN],
        "expected": expected,
        "cites_expected": hit,
    }


def check_persona(persona_id: uuid.UUID, **kwargs) -> dict:
    """The job body: run the check, then activate (retiring the previous persona) or reject."""
    try:
        report = run_persona_check(persona_id, **kwargs)
    except Exception as exc:
        report = {"passed": False, "reasons": [f"provjera nije uspjela ({type(exc).__name__})"]}
        _finish(persona_id, report)
        raise
    _finish(persona_id, report)
    return report


def _finish(persona_id: uuid.UUID, report: dict) -> None:
    with Session(app_engine()) as s, s.begin():
        persona = s.get(PersonaConfig, persona_id)
        s.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.id == persona.kb_id).with_for_update()
        )
        persona.check_report = report
        data = {"version": persona.version, "reasons": report.get("reasons", [])}
        if not report.get("passed"):
            persona.status = "rejected"
            notify_teachers(s, persona.kb_id, "persona_rejected", data)
            return
        notify_teachers(s, persona.kb_id, "persona_active", data)
        current = active_persona(s, persona.kb_id)
        if current is not None and current.id != persona.id:
            current.status = "retired"
            s.flush()  # free the one-active slot
        persona.status, persona.activated_at = "active", datetime.now(UTC)
