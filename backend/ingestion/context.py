"""Short situating context for a chunk, written by the `context` role at indexing time.
Prepended to the embedded text only; the stored citation text stays the original."""

import hashlib

from llm.client import complete

# Model-neutral prompt; per-model variants belong in models.yaml `prompt_overrides`.
CONTEXT_PROMPT = """Ovo je isječak iz nastavnih materijala kolegija.
Mjesto u materijalima: {heading_path}

<isjecak>
{text}
</isjecak>

U jednoj ili dvije rečenice na hrvatskom jeziku opiši o čemu je ovaj isječak i kako se
uklapa u temu predavanja, tako da ga je lakše pronaći pretraživanjem.
Odgovori samo tim rečenicama."""
# Measured on the first subject (2026-11-14): a stricter variant ("what the slide states" + one
# student question, with an example) fixed h11 in dense search but lost h06/h17, and was not
# better in any mode (best mode 0.89 vs 0.95 hard recall@5). This prompt stays until a larger
# golden set can tell prompt variants apart. See CLAUDE.md, "Context and hybrid experiments".


def prompt_version() -> str:
    """Short hash of the prompt, recorded in ingest reports so runs stay comparable."""
    return hashlib.sha256(CONTEXT_PROMPT.encode()).hexdigest()[:12]


def chunk_context(heading_path: str, text: str) -> str | None:
    """None when the model returns nothing: the chunk is then embedded without context
    rather than with an empty line."""
    prompt = CONTEXT_PROMPT.format(heading_path=heading_path, text=text[:6000])
    return complete("context", [{"role": "user", "content": prompt}]).strip() or None
