"""Prompt texts, model-neutral (rule 8). A model that needs a different wording gets a per-model
variant in models.yaml `prompt_overrides` (keyed by LiteLLM model string, then prompt name);
code never branches on model names.

Assembly order (CLAUDE.md "Instruction layers", higher wins): platform rules > teacher persona >
student adaptation. The platform rules are fixed; teachers cannot edit them.
"""

import hashlib

from llm.config import ModelConfig, Role, get_model_config

# The marker a model writes when the sources do not answer the question. Plain text, so every
# model can produce it without provider-specific structured output.
DONT_KNOW = "NE_ZNAM"

PROMPTS: dict[str, str] = {
    # Platform rules (CLAUDE.md rules 1, 2, 4) for the `answer` role.
    "answer_system": f"""Ti si AI asistent nastavnika na kolegiju „{{subject}}".
Pomažeš studentima učiti iz nastavnikovih materijala.

Pravila koja uvijek vrijede:
1. Odgovaraj samo na temelju numeriranih izvora koje dobiješ uz pitanje.
   Ne dodaj ništa iz svog općeg znanja.
2. Iza svake tvrdnje navedi broj izvora iz kojeg je, u uglatim zagradama, npr. [1] ili [2][3].
3. Ako izvori ne sadrže odgovor na pitanje, ne pokušavaj odgovoriti: napiši samo {DONT_KNOW}
4. Pitanja o ocjenama, rokovima, ispitima i obavezama studenta nisu za tebe: napiši samo {DONT_KNOW}
5. Ne daj gotova rješenja zadaća ni vježbi: objasni pojmove i pristup, a rješenje prepusti studentu.
6. Odgovaraj na hrvatskom jeziku, jasno i sažeto, kao nastavnik koji objašnjava studentu.""",
    # Appended after the platform rules when the teacher has a persona (build step 3).
    "persona_header": "Upute nastavnika (vrijede samo ako se ne kose s pravilima iznad):",
    "answer_user": """Izvori:

{sources}

Pitanje studenta: {question}""",
    # Scope check for the `fast` role: one word, before retrieval.
    "scope_check": """Ti razvrstavaš pitanja studenata za AI asistenta kolegija „{subject}".
Teme kolegija: {topics}

Pitanje studenta: {question}

Odgovori jednom riječju:
GRADIVO – pitanje o sadržaju ovog kolegija (pojmovi, objašnjenja, primjeri, zadaci iz tema kolegija)
ADMIN – pitanje o ocjenama, bodovima, rokovima, datumima ispita, upisu,
  prisutnosti ili obavezama studenta
DRUGO – pitanje koje nema veze s ovim kolegijem (drugi predmet, osobno, opći razgovor)""",
    # Evaluation only (`judge` role): the answer is judged against the slides it cites, never
    # against a model-written reference answer (CLAUDE.md "Evaluation strategy").
    "judge": """Procjenjuješ odgovor AI asistenta studentu.

Izvori koje odgovor citira:

{sources}

Pitanje studenta: {question}

Odgovor asistenta:
{answer}

Odgovori točno u dva retka:
UTEMELJENO: DA ili NE (DA samo ako je sve što odgovor tvrdi rečeno u izvorima iznad)
ODGOVARA: DA ili NE (DA ako odgovor odgovara na ono što je student pitao)""",
}


def prompt(name: str, role: Role, config: ModelConfig | None = None) -> str:
    """The prompt `name` for the model serving `role`: its override if it has one."""
    cfg = config or get_model_config()
    return cfg.prompt_overrides.get(cfg.role(role).model, {}).get(name, PROMPTS[name])


def prompt_version(names: list[str], role: Role, config: ModelConfig | None = None) -> str:
    """Short hash of the prompts used, recorded on traces and eval reports."""
    text = "\x00".join(prompt(n, role, config) for n in names)
    return hashlib.sha256(text.encode()).hexdigest()[:12]
