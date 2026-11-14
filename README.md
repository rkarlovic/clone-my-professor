# Clone My Professor (CMP)

A platform where a teacher builds an AI "clone" that helps their students learn around the clock,
using **only the teacher's approved materials** and following the teacher's teaching instructions.
Students reach it from their learning platform (Moodle/Merlin first, later Teams and Google
Classroom) or from CMP's own web app. Content and users are primarily **Croatian**.

> **Status:** early prototype — build step 1 of 6 (retrieval spike) measured, step 2 (answers)
> started. There is no chat UI yet; what works today is parsing course material, indexing it,
> measuring how well search finds the right slide, and command-line answers with citations that
> forward what they can't ground to a teacher queue.

## Contents

- [Principles](#principles)
- [What works so far](#what-works-so-far)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Retrieval spike workflow](#retrieval-spike-workflow)
- [Models](#models)
- [Repository layout](#repository-layout)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

## Principles

The full rule set lives in [CLAUDE.md](CLAUDE.md); the short version:

- The clone answers **only from approved sources**, and every answer cites document + page/slide.
- If it can't find a citable source (or the question is about grades, deadlines or exams), it says
  so and **forwards the question to the teacher**.
- **Subjects never mix** — separation is enforced by Postgres row-level security, not by prompts.
- It does not hand out full solutions to homework, and nothing goes live without teacher approval.
- It always discloses that the student is talking to an AI, and it never grades students.
- **Model-agnostic, open models first:** all model calls go through LiteLLM, and which model
  serves which role is set in [config/models.yaml](config/models.yaml), never in code.
- Personal data stays in the EU.

## What works so far

| Area | State |
|---|---|
| Dev infrastructure | Docker Compose: Postgres 18 + pgvector, Ollama (optional NVIDIA GPU), Infinity reranker (optional profile) |
| Database | Alembic migration for knowledge bases, versions, documents and chunks; row-level security per `kb_version` with a non-owner app role |
| Model layer | LiteLLM wrapper that resolves models by **role** from `config/models.yaml` |
| Ingestion | Docling parsing (cached), slide-aware chunking, lecture labels from PDF bookmarks, repeated slides indexed once and cited on every page they appear on, BGE-M3 embeddings into pgvector; each run creates a new **draft** version |
| Retrieval | Dense vector search filtered by `kb_version`, optional reranking, optional hybrid with BGE-M3 sparse vectors (experiment) |
| Answers | Scope check (`fast`), answer with numbered citations (`answer`), forwarding to a teacher queue when the question is admin/off-topic or the reply cites no source |
| Evaluation | Golden question set per subject; recall@k and MRR split by easy/hard, plus top scores for questions the clone should decline; answer evaluation (answered, cites the expected slide, forwarded, optional judge) |
| Tests | pytest: RLS isolation, model config, chunking, citations, answer checks, teacher queue |

**First results** (subject *Suvremene tehnike programiranja*, 945 pages, 50 draft golden questions):
recall@5 is **1.00** on easy questions and **0.84** on hard ones with plain dense search, and
**0.95** on hard ones with a context sentence per chunk (`--context`) plus hybrid candidates and
reranking (see CLAUDE.md for the full comparison and timings). Questions the clone should decline
score in the same range as weak answerable ones, so a score threshold alone won't be enough for
abstention — step 2 needs an explicit out-of-scope check.

Not built yet: chat UI, teacher dashboard,
LMS connectors, background jobs, Langfuse tracing.

## Architecture

```
LMS (Moodle/Merlin, Teams, Classroom, …) ─► connector ─► CMP core
                                                          ├─ ingestion: Docling → chunking → embeddings → pgvector
                                                          ├─ retrieval: dense (+ sparse) → rerank → parent sections
                                                          ├─ prompt assembly: platform rules > teacher persona > student adaptation
                                                          ├─ model layer: LiteLLM → vLLM (pilot) / Ollama (dev)
                                                          ├─ teacher question queue + analytics
                                                          ├─ background jobs: Procrastinate (Postgres queue)
                                                          └─ Langfuse tracing
```

Tech stack: Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL + pgvector, Docling,
BGE-M3, bge-reranker-v2-m3, LiteLLM, vLLM/Ollama, Procrastinate; React + Vite + TypeScript for the
frontend (later). See [CLAUDE.md](CLAUDE.md) for the reasoning behind each choice.

## Getting started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/). On Windows it needs WSL2:
  hardware virtualization enabled in the BIOS and the "Virtual Machine Platform" Windows feature
  (`wsl.exe --install --no-distribution` in an admin shell, then restart).
- [uv](https://docs.astral.sh/uv/) for Python (it installs Python 3.12 itself)
- Windows: a current Visual C++ runtime (`winget upgrade Microsoft.VCRedist.2015+.x64`). With an
  old one, `import torch` fails with `WinError 126` on `shm.dll` and ingestion stops.
- Optional: an NVIDIA GPU with the NVIDIA Container Toolkit for faster Ollama

### Setup

Run from the repository root:

```bash
cp .env.example .env          # then change the passwords in .env
docker compose -f infra/docker-compose.dev.yml --env-file .env up -d
docker compose -f infra/docker-compose.dev.yml --env-file .env exec ollama ollama pull bge-m3:567m
```

Variants:

- NVIDIA GPU: add `-f infra/docker-compose.dev.gpu.yml` after the first `-f`.
- Reranker: add `--profile rerank`.
- macOS: containers get no GPU, so run the native Ollama app instead and start only Postgres
  (`... up -d postgres`). `OLLAMA_API_BASE` stays the same.

Then the backend, which runs on the host:

```bash
cd backend
uv sync                        # dependencies
uv sync --group ingest         # + Docling (large, pulls torch) — needed for parsing
uv run alembic upgrade head    # create the schema
uv run python -m scripts.smoke # end-to-end check: embed → pgvector under RLS → search
```

> On Windows, keep `127.0.0.1` in the connection URLs — `localhost` resolves to IPv6 first and
> each connection stalls for several seconds.

## Retrieval spike workflow

1. Put a subject's PDFs in `data/<subject>/` (git-ignored — course material is never committed).
2. Ingest it. This parses (cached in `data/.cache/`), chunks, embeds and stores a new draft
   version, and writes a report to `eval/reports/<subject>/`:
   ```bash
   uv run python -m scripts.ingest --subject <subject>
   ```
   Options: `--context` (adds a short context per chunk with the `context` model; ~11 s per chunk
   on a CPU laptop, so hours for a large subject), `--ocr` (for
   scans), `--min-tokens N`, `--max-tokens N`, `--duplicate-similarity F` (default 0.85) or
   `--keep-duplicates`. Repeated slides are indexed once; the report's `duplicates` lists each
   folded page and the page it is indexed under.
3. Try a question and see the top 5 hits:
   ```bash
   uv run python -m scripts.ask --subject <subject> "Što je rekurzija?"
   ```
4. Write a golden set in `eval/sets/<subject>.yaml` (format: [eval/sets/_template.yaml](eval/sets/_template.yaml))
   and measure recall@5 and MRR:
   ```bash
   uv run python -m scripts.evaluate --subject <subject>
   ```
   `ask` and `evaluate` also take `--version N`, `--rerank 20` and `--hybrid`; `evaluate` takes
   `--set PATH` for another question set of the same subject. Sets made from someone else's text
   (e.g. a teacher's own exam questions) go in `eval/sets/local/`, which is git-ignored. Hybrid needs
   sparse vectors first: `uv run python -m scripts.sparse --subject <subject>` (experiment).
   Ingest reports record time per step; eval reports record their runtime.
5. Get the clone's answer (needs `ollama pull qwen3.5:9b` and `qwen3.5:4b`), and evaluate answers
   on the golden set (~1.5 min per question with the judge on a CPU laptop; `--ids`/`--limit` for small runs):
   ```bash
   uv run python -m scripts.ask --subject <subject> --answer "Zašto mi se zamrzne prozor?"
   uv run python -m scripts.evaluate_answers --subject <subject> --judge
   ```
   Questions the clone forwards go to the teacher queue (`teacher_question`); `--no-queue` skips that.

## Models

Code asks for a **role**; [config/models.yaml](config/models.yaml) says which model serves it.
Switching a model means editing that file and re-running the golden set — no code change.

| Role | Dev model (Ollama) | Purpose |
|---|---|---|
| `embedding` | BGE-M3 | vectors for search |
| `reranker` | bge-reranker-v2-m3 (Infinity) | reorders search candidates |
| `sparse` | BGE-M3 (transformers, experiment) | keyword-style vectors for hybrid search |
| `answer` | Qwen 3.5 9B | student answers (step 2) |
| `fast` | Qwen 3.5 4B | query rewriting, out-of-scope check |
| `context` | Qwen 3.5 4B | chunk context at indexing |
| `vision` | Qwen3-VL 8B | image/diagram captions |
| `judge` | Qwen 3.5 9B | evaluation |

The dev chat models are placeholders that fit an 11 GB GPU. The real choice for `answer` is made on
golden-set results from the vLLM setup used in the pilot, never on Ollama's quantized models.

## Repository layout

```
backend/
  app/          FastAPI app (only /health for now)
  core/         settings, DB access with RLS scope, models, knowledge-base versions, answers, queue
  ingestion/    Docling parsing, chunking, context, ingest pipeline
  retrieval/    search (+ rerank)
  llm/          LiteLLM wrapper, role resolution, prompts, tokenizer
  connectors/   LMS-specific code (empty for now)
  jobs/         Procrastinate (empty for now)
  migrations/   Alembic
  scripts/      smoke, parse, ingest, sparse, ask, evaluate, evaluate_answers
  tests/
config/         models.yaml
eval/           golden sets (sets/) and run reports (reports/, git-ignored)
infra/          Docker Compose files, Postgres init
data/           course material per subject (git-ignored)
```

## Development

```bash
cd backend
uv run pytest                                  # tests; DB tests skip if Postgres is down
uv run ruff check . && uv run ruff format .    # lint + format
uv run fastapi dev app/main.py                 # API with reload
```

Conventions (details in [CLAUDE.md](CLAUDE.md)):

- No model names outside `config/models.yaml`, no provider SDKs, no LMS-specific code outside
  `connectors/`.
- KB content is read and written only inside `core.db.kb_scope(kb_version_id)`; everything except
  migrations connects as the app role, so row-level security always applies.
- Any change to retrieval, prompts, persona handling or model config must pass the golden set.
- No secrets in code, images or Compose files — use `.env` (git-ignored).

To stop everything: `docker compose -f infra/docker-compose.dev.yml --env-file .env down`
(data volumes are kept).

## Roadmap

1. **Retrieval spike** — parsing, chunking, embeddings, search, recall@5 on a golden set *(measured; experiments left)*
2. **Answers** — model roles, citations, abstention + teacher queue; compare 2–3 open models *(in progress)*
3. **Product** — teacher persona, approval workflow, versioning, question queue, chat UI, direct upload
4. **Moodle connector** — LTI 1.3 + Web Services polling, then SRCE/Merlin
5. Teams and Google Classroom connectors
6. Later: student profile, voice, avatar

## License

Proprietary — all rights reserved. See [LICENSE](LICENSE).
