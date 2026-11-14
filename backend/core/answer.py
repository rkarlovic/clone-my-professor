"""Answer a student's question from one kb_version, or forward it to the teacher.

    scope check (`fast`) -> retrieval -> score floor -> grounded answer (`answer`) -> citation check

Rules (CLAUDE.md): answers come only from the version's sources and cite them (1); anything the
clone cannot ground, and every admin question, is forwarded (2); retrieval is scoped to the
version by RLS inside `search` (3); solution chunks are excluded there by default (4).
Forwarding is returned, not stored: the caller decides whether to queue it (`core.queue`), so
evaluation runs do not fill the teacher's queue.
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import app_engine, kb_scope
from core.models import Chunk, ForwardReason, KbVersion, KnowledgeBase
from llm.client import complete, trace_metadata
from llm.config import get_model_config
from llm.prompts import DONT_KNOW, prompt, prompt_version
from retrieval.search import Hit, search

# What the student sees when the clone does not answer. Gender-neutral on purpose.
FORWARD_MESSAGES = {
    ForwardReason.admin: (
        "Na pitanja o ocjenama, rokovima i ispitima odgovara nastavnik. "
        "Pitanje je proslijeđeno nastavniku."
    ),
    ForwardReason.out_of_scope: (
        "To pitanje nije iz gradiva ovog kolegija, pa na njega ne mogu odgovoriti. "
        "Pitanje je proslijeđeno nastavniku."
    ),
    ForwardReason.low_score: (
        "U materijalima kolegija ne nalazim odgovor na to pitanje. "
        "Pitanje je proslijeđeno nastavniku."
    ),
    ForwardReason.no_source: (
        "U materijalima kolegija ne nalazim pouzdan odgovor na to pitanje. "
        "Pitanje je proslijeđeno nastavniku."
    ),
}
SCOPE_LABELS = {"GRADIVO": None, "ADMIN": ForwardReason.admin, "DRUGO": ForwardReason.out_of_scope}
# Safety net for rule 2, independent of the model: words that in a student's question mean
# grades, points or deadlines. Kept to unmistakable ones (not "ispit", "upis", "prolaz", which
# are also programming words). Croatian, the platform language; other languages add theirs.
# On the first subject the 4B labelled "Koliko bodova trebam skupiti za četvorku?" GRADIVO.
ADMIN_TERMS = re.compile(
    r"\b(ocjen\w*|bodov\w*|kolokvij\w*|dvojk\w*|trojk\w*|četvork\w*|petic\w*|rok\w* za\b|"
    r"rokov\w*|ispitn\w* rok\w*|datum\w* ispita)",
    re.IGNORECASE,
)
MAX_SOURCE_CHARS = 4000  # per source; a parent section can be several slides long
_CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


@dataclass
class Source:
    """One numbered source the model sees: a retrieved section with every page it is on."""

    n: int
    file_name: str
    pages: list[int]
    text: str
    score: float

    @property
    def citation(self) -> str:
        if not self.pages:
            return self.file_name
        return f"{self.file_name}, str. {', '.join(map(str, self.pages))}"


@dataclass
class Answer:
    question: str
    text: str  # what the student sees: the answer, or the forward message
    forwarded: ForwardReason | None
    sources: list[Source] = field(default_factory=list)  # what the model saw
    cited: list[Source] = field(default_factory=list)  # what the answer cites
    raw: str | None = None  # model output before the citation check
    scope: str | None = None  # the scope check's label
    trace: dict = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)


def answer_question(
    kb_version_id: uuid.UUID,
    question: str,
    *,
    k: int = 5,
    rerank_candidates: int | None = None,
    hybrid: bool = False,
    persona_text: str | None = None,
    persona_version: int | None = None,
) -> Answer:
    cfg = get_model_config()
    subject, topics = _subject(kb_version_id)
    result = Answer(question=question, text="", forwarded=None)
    result.trace = {
        "kb_version_id": str(kb_version_id),
        "persona_version": persona_version,
        "model_config_version": cfg.version,
        "models": {"fast": trace_metadata("fast"), "answer": trace_metadata("answer")},
        "prompt_version": prompt_version(
            ["answer_system", "persona_header", "answer_user"], "answer"
        ),
        "scope_prompt_version": prompt_version(["scope_check"], "fast"),
        "retrieval": {"k": k, "rerank_candidates": rerank_candidates, "hybrid": hybrid},
    }

    started = time.time()
    result.scope, reason = scope_check(subject, topics, question)
    result.seconds["scope"] = round(time.time() - started, 1)
    if reason:
        return _forward(result, reason)

    started = time.time()
    hits = search(kb_version_id, question, k, rerank_candidates=rerank_candidates, hybrid=hybrid)
    result.seconds["retrieval"] = round(time.time() - started, 1)
    floor = _score_floor(rerank_candidates, hybrid)
    if not hits or (floor is not None and hits[0].score < floor):
        return _forward(result, ForwardReason.low_score)
    result.sources = build_sources(hits)

    system = prompt("answer_system", "answer").format(subject=subject)
    if persona_text:
        system += "\n\n" + prompt("persona_header", "answer") + "\n" + persona_text.strip()
    user = prompt("answer_user", "answer").format(
        sources="\n\n".join(f"[{s.n}] ({s.citation})\n{s.text}" for s in result.sources),
        question=question,
    )
    started = time.time()
    result.raw = complete(
        "answer", [{"role": "system", "content": system}, {"role": "user", "content": user}]
    ).strip()
    result.seconds["answer"] = round(time.time() - started, 1)

    text, cited = check_citations(result.raw, result.sources)
    if text is None:
        return _forward(result, ForwardReason.no_source)
    result.text, result.cited = text, cited
    return result


def scope_check(subject: str, topics: list[str], question: str) -> tuple[str, ForwardReason | None]:
    """The `fast` role's label for the question and the forward reason it implies. An unclear
    reply lets the question through: retrieval and the citation check still guard the answer.
    Questions with an unmistakable admin word are forwarded without asking the model."""
    if ADMIN_TERMS.search(question):
        return "ADMIN_TERM", ForwardReason.admin
    reply = complete(
        "fast",
        [
            {
                "role": "user",
                "content": prompt("scope_check", "fast").format(
                    subject=subject,
                    topics="\n".join(f"- {t}" for t in topics),
                    question=question,
                ),
            }
        ],
    )
    words = re.findall(r"[A-ZČĆŠŽĐ]{4,}", reply.upper())
    label = next((w for w in words if w in SCOPE_LABELS), "?")
    return label, SCOPE_LABELS.get(label)


def build_sources(hits: list[Hit]) -> list[Source]:
    """Number the hits for the prompt. Hits that share a parent section become one source, so
    the model does not read the same section twice; its pages are the union of theirs."""
    sources: list[Source] = []
    by_text: dict[str, Source] = {}
    for h in hits:
        key = h.context_text
        if key in by_text:
            src = by_text[key]
            src.pages += [p for p in h.pages if p not in src.pages]
            continue
        src = Source(
            n=len(sources) + 1,
            file_name=h.file_name,
            pages=list(h.pages),
            text=key[:MAX_SOURCE_CHARS],
            score=h.score,
        )
        sources.append(src)
        by_text[key] = src
    return sources


def check_citations(raw: str, sources: list[Source]) -> tuple[str | None, list[Source]]:
    """(answer text, cited sources), or (None, []) when the reply must not reach the student:
    the model said it does not know (even next to a partial answer), or cited no real source.
    Citation numbers that point at no source are removed from the text."""
    if not raw or DONT_KNOW in raw.upper().replace(" ", "_"):
        return None, []
    by_n = {s.n: s for s in sources}
    cited: dict[int, Source] = {}

    def keep_valid(m: re.Match) -> str:
        valid = [int(n) for n in re.split(r"\s*[,;]\s*", m.group(1)) if int(n) in by_n]
        for n in valid:
            cited.setdefault(n, by_n[n])
        return "".join(f"[{n}]" for n in valid)

    text = _CITATION.sub(keep_valid, raw).strip()
    if not cited:
        return None, []
    return text, sorted(cited.values(), key=lambda s: s.n)


def _forward(result: Answer, reason: ForwardReason) -> Answer:
    result.forwarded = reason
    result.text = FORWARD_MESSAGES[reason]
    result.cited = []
    return result


def _score_floor(rerank_candidates: int | None, hybrid: bool) -> float | None:
    """The floor on the scale of the score the hits carry. Fused (hybrid, not reranked) scores
    are rank-based and have no meaningful floor."""
    cfg = get_model_config()
    if rerank_candidates:
        return cfg.role("reranker").min_score
    return None if hybrid else cfg.role("embedding").min_score


@lru_cache(maxsize=64)
def _subject(kb_version_id: uuid.UUID) -> tuple[str, list[str]]:
    """The subject's display name and its lecture labels (for the scope check)."""
    with Session(app_engine()) as s:
        name = s.scalar(
            select(KnowledgeBase.subject_name)
            .join(KbVersion, KbVersion.kb_id == KnowledgeBase.id)
            .where(KbVersion.id == kb_version_id)
        )
    if name is None:
        raise LookupError(f"no kb_version {kb_version_id}")
    with kb_scope(kb_version_id) as s:
        topics = s.scalars(
            select(Chunk.week_topic).distinct().where(Chunk.week_topic.is_not(None))
        ).all()
    return name.replace("_", " "), sorted(topics)
