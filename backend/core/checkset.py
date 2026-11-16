"""Auto-generated check set for one subject (CLAUDE.md "Evaluation strategy").

At ingest the system picks slides spread over the material and has a model write, for each, one
question phrased like a student (casual, paraphrased, sometimes English or without diacritics,
so it does not copy the slide's wording). The slide is the question's expected source: nobody
marks anything. There are no model-written reference answers; answers are judged against the
cited slide. Generic abstention questions (deadlines, grades, unrelated topics) are added: the
same for every subject.

The set is made from the installation's own material, so it stays local (eval/sets/local/,
git-ignored) and is never sent anywhere. A teacher may delete bad questions; doing nothing is fine.
"""

import random
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from core.db import kb_scope
from core.models import Chunk, ContentType, Document
from llm.client import complete
from llm.prompts import prompt

# The marker the model writes for a slide no student would ask about (title, agenda, contact).
SKIP = "PRESKOČI"

# Question styles, used in turn. `no_diacritics` is a casual question with the diacritics
# removed in code (more reliable than asking a model to misspell).
STYLES: dict[str, str] = {
    "casual": "neformalno, kako bi student pitao kolegu ili asistenta u porukama",
    "symptom": (
        "opiši situaciju ili problem na koji je student naišao (npr. što mu se dogodilo dok je "
        "nešto radio), a ne naziv pojma"
    ),
    "english": "na engleskom jeziku, kako bi pitao student na razmjeni",
    "paraphrase": "svojim riječima, bez stručnog naziva koji stoji u isječku, ako ga ima",
    "no_diacritics": "neformalno i kratko, kako bi student brzo utipkao na mobitelu",
}

# Generic questions the clone must forward to the teacher, the same for every subject.
# Croatian only, like ADMIN_TERMS in core/answer.py; another language adds its own list.
# Several deliberately avoid the ADMIN_TERMS keywords, so the scope check is tested too.
GENERIC_ABSTAIN: list[tuple[str, str]] = [
    ("admin", "Kad je rok za predaju seminara?"),
    ("admin", "Koliko bodova treba za prolaz na ispitu?"),
    ("admin", "Mogu li dobiti potpis ako sam izostao s tri predavanja?"),
    ("admin", "Kad su ispitni rokovi u ljetnom semestru?"),
    ("admin", "Hoće li nam profesor priznati kolokvij iz prošle godine?"),
    ("admin", "jel se mora dolazit na vjezbe"),
    ("admin", "Is the final exam oral or written?"),
    ("out_of_scope", "Možeš li mi preporučiti dobar recept za sarmu?"),
    ("out_of_scope", "Tko je osvojio Svjetsko prvenstvo u nogometu 2018.?"),
    ("out_of_scope", "Koji je najbolji način za putovanje od Zagreba do Splita?"),
    ("out_of_scope", "Kakvo će vrijeme biti sutra u Puli?"),
    ("out_of_scope", "Napiši mi pjesmu o moru."),
]

MIN_WORDS = 30  # shorter slides (titles, agendas) rarely answer a student question
_DIACRITICS = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")


def strip_diacritics(text: str) -> str:
    return text.translate(_DIACRITICS)


@dataclass
class Slide:
    file_name: str
    page: int
    heading_path: str
    text: str


def eligible_slides(kb_version_id: uuid.UUID) -> list[Slide]:
    """Embedded chunks long enough to ask about, in material order. Solutions are excluded,
    like in retrieval (rule 4), and so are exercises: a question written from a homework task
    asks for its solution, which tests rule 4, not whether search finds the slide."""
    with kb_scope(kb_version_id) as s:
        rows = s.execute(
            select(Document.file_name, Chunk.page_or_slide, Chunk.heading_path, Chunk.text)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.embedding.is_not(None))
            .where(Chunk.content_type.not_in([ContentType.solution, ContentType.exercise]))
        ).all()
    slides = [
        Slide(f, p, h or "", t)
        for f, p, h, t in rows
        if p is not None and len(t.split()) >= MIN_WORDS
    ]
    return sorted(slides, key=lambda x: (x.file_name, x.page))


def spread(items: list, n: int, rng: random.Random) -> list[list]:
    """Split `items` (in material order) into n consecutive bins, each shuffled: the first item
    of each bin is its pick, the rest are fallbacks when the model skips it."""
    n = min(n, len(items))
    bounds = [round(i * len(items) / n) for i in range(n + 1)]
    bins = [items[bounds[i] : bounds[i + 1]] for i in range(n)]
    for b in bins:
        rng.shuffle(b)
    return bins


def clean_question(reply: str) -> str | None:
    """The model's question, or None for a skip or an empty/unusable reply."""
    text = reply.strip()
    text = text.splitlines()[0].strip() if text else ""
    text = re.sub(r"^(pitanje|question)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip('"„“”*').strip()
    if not text or SKIP in text.upper() or len(text.split()) < 3:
        return None
    return text


def write_question(subject: str, slide: Slide, style: str) -> str | None:
    content = prompt("checkset_question", "answer").format(
        subject=subject,
        heading_path=slide.heading_path,
        text=slide.text[:4000],
        style=STYLES[style],
    )
    q = clean_question(complete("answer", [{"role": "user", "content": content}]))
    if q and style == "no_diacritics":
        q = strip_diacritics(q).lower()
    return q


def generate(
    kb_version_id: uuid.UUID,
    subject: str,
    n: int,
    seed: int = 0,
    tries: int = 3,
    progress=None,
) -> list[dict]:
    """n slide questions spread over the material, then the generic abstention questions.
    Question dicts are in the golden-set format (scripts.evaluate.Question)."""
    rng = random.Random(seed)
    styles = list(STYLES)
    questions: list[dict] = []
    for i, candidates in enumerate(spread(eligible_slides(kb_version_id), n, rng)):
        style = styles[i % len(styles)]
        for slide in candidates[:tries]:
            q = write_question(subject, slide, style)
            if q:
                questions.append(
                    {
                        "id": f"c{len(questions) + 1:03d}",
                        "question": q,
                        "type": "answerable",
                        "difficulty": "check",
                        "expected_sources": [
                            {"document": slide.file_name, "page_or_slide": slide.page}
                        ],
                        "notes": f"style: {style}",
                    }
                )
                break
        if progress:
            progress(i + 1, questions[-1] if questions else None)
    for j, (kind, text) in enumerate(GENERIC_ABSTAIN, 1):
        questions.append(
            {
                "id": f"g{j:02d}",
                "question": text,
                "type": kind,
                "difficulty": "check",
                "expected_sources": [],
                "notes": "generic",
            }
        )
    return questions
