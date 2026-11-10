"""Short situating context for a chunk, written by the `context` role at indexing time.
Prepended to the embedded text only; the stored citation text stays the original."""

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


def chunk_context(heading_path: str, text: str) -> str:
    prompt = CONTEXT_PROMPT.format(heading_path=heading_path, text=text[:6000])
    return complete("context", [{"role": "user", "content": prompt}]).strip()
