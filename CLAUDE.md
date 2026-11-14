# CLAUDE.md — Clone My Professor (CMP)

## What CMP is
A platform where a teacher builds an AI "clone" that helps their students learn, 24/7, using **only the teacher's approved materials** and following the teacher's pedagogical instructions. Students reach it from their learning platform (Moodle/Merlin first, later Teams and Google Classroom) or from CMP's own web app. Primary language of content and users: **Croatian**.

Current phase: **prototype → pilot** (1–2 subjects, 50–150 students, one semester). Text only. Voice and avatar come later.

## Non-negotiable rules (apply to every change)
1. The clone answers **only from approved sources** of the active knowledge base version. Every answer carries **citations** (document + page/slide).
2. If retrieval is insufficient (below relevance threshold, no citable source, or question about grades/deadlines/exams), the clone says it doesn't know and **forwards the question to the teacher's queue**.
3. **Subjects never mix.** Retrieval is filtered by the active `kb_version_id` on the server side and enforced by Postgres row-level security. Never rely on prompt instructions for separation.
4. The clone **does not hand out full solutions** to exercises/homework; chunks tagged `content_type = solution` are excluded from retrieval by default.
5. Nothing enters a live knowledge base without **teacher approval**. New or changed material always becomes a draft version.
6. The UI always discloses that the student is talking to an AI (EU AI Act transparency).
7. CMP must **not grade students, decide access, or formally steer progression** (keeps it outside the AI Act high-risk category). Flag any feature that drifts toward this.
8. **CMP is model-agnostic.** All model calls go through the LiteLLM layer, and which model serves which role is set in `config/models.yaml`, never in code. Never import a provider SDK in the core, and never depend on a feature only one provider has (see "Model layer").
9. Personal data stays in the EU; no provider may train on our data; store only what is needed (LTI claims minimised, no audio recordings, transcripts only).
10. Any change to retrieval, prompts, persona handling or the model config must pass the **golden question set** before merge.

## Architecture
One shared pipeline; per-teacher-and-subject data and configuration.

```
LMS (Moodle/Merlin, Teams, Classroom, …) ─► connector ─► CMP core
                                                          ├─ ingestion: Docling → chunking → embeddings → pgvector
                                                          ├─ retrieval: dense (+ sparse) → rerank → parent sections
                                                          ├─ prompt assembly: platform rules > teacher persona > student adaptation
                                                          ├─ model layer: LiteLLM → open models (vLLM / Ollama); commercial optional via config
                                                          ├─ teacher question queue + analytics
                                                          ├─ background jobs: Procrastinate (Postgres queue)
                                                          └─ Langfuse tracing (kb_version + persona version + model id on every trace)
```

### Instruction layers (prompt assembly order, higher wins)
1. **Platform rules** – fixed, not editable by teachers (rules 1–6 above).
2. **Teacher persona** – structured fields (ti/Vi, answer length, help level for exercises, examples/analogies, language/terminology, out-of-scope topics, escalation rules) + one free-text field. Versioned; teacher previews before activation; activation reruns that subject's golden set and is blocked if grounding/abstention drops below threshold.
3. **Student adaptation** – later phase, minimal.

## Model layer (LLM-agnostic, open models first)
- Every model is addressed by **role**, not by name. Roles: `answer`, `fast` (classification, query rewriting, out-of-scope check), `context` (chunk context at indexing), `vision` (image/diagram captions), `judge` (evaluation). Embeddings and reranker are configured the same way.
- `config/models.yaml` maps each role to a LiteLLM model string + parameters. Switching a model = editing config and re-running the golden set; no code change.
- **Two layers:** LiteLLM is the gateway the code talks to (routing, fallbacks, rate limits, cost tracking); an inference server runs the model. The core never talks to an inference server directly.
- **Open models first.** Inference servers: **vLLM** for pilot and production (continuous batching for many parallel users, automatic prefix caching, multi-GPU, OpenAI-compatible API) on an EU GPU host; **Ollama** for local development only. llama.cpp directly only for CPU-only demos; SGLang is an acceptable drop-in alternative to vLLM via config. Candidates for `answer`: Qwen 3.5, Mistral Small 4, Gemma 4 — chosen by golden-set results, not by benchmarks. Commercial models (EU region) may be added later purely as config entries.
- **Model decisions are made only on golden-set results from the vLLM setup** that serves students. Ollama runs quantized GGUF variants that can answer differently; never compare or tune on Ollama output.
- Use only the OpenAI-compatible chat schema through LiteLLM. Prompts must work across models: no provider-specific tool formats or prompt syntax in the core. Provider-specific optimisations (e.g., prompt caching) only behind a config flag, with identical behaviour when off. (vLLM's automatic prefix caching needs no code.)
- Keep system prompts and the platform-rule text model-neutral; if a model needs a variant, store it as a per-model override in config, never as `if model == ...` in code.
- Every trace records the exact model id and config version, so results stay comparable across switches.

## Traffic and scaling
Two kinds of traffic, handled differently:
- **Chat (live, high volume):** student → FastAPI → retrieval (Postgres) → LiteLLM → model; the answer streams back via Server-Sent Events. **Chat never goes through the job queue.** Each message is stored as a row (history, analytics, teacher queue) — Postgres handles this comfortably (a 10,000-student university at peak ≈ 10–20 inserts/s).
- **Background jobs (slow, low volume):** ingestion, LMS polling, golden-set runs, version activation → Procrastinate queue in Postgres. Even 1,000 courses polled 4×/hour ≈ 1 job/s.
- **The real bottleneck is LLM generation (GPU), not the database.** Peak load is handled in the model layer: vLLM batching, more model instances, and LiteLLM load balancing, rate limits and fallbacks. Measure in the pilot how many parallel conversations one GPU serves at acceptable latency; that figure drives hosting cost per institution.
- Code creates jobs **only through the interface in `jobs/`**, so the queue could be replaced (e.g., Redis/RabbitMQ) without touching the rest. Consider that only for sustained thousands of jobs/s, real-time fan-out to many clients, or event streaming between many services.
- API servers stay stateless (all state in Postgres), so more FastAPI instances can be added behind a load balancer.

## Containers and deployment
Everything runs in **Docker containers** (OCI images, so Podman/Kubernetes also work). Universities must be able to run CMP on their own infrastructure from the same images.
- **Services:** `api` (FastAPI) and `worker` (Procrastinate) share **one backend image** with different start commands; `frontend` (static React build behind Caddy/nginx); `proxy` (Caddy or Traefik, **HTTPS mandatory** — LTI and microphone access require it); `postgres` (+ pgvector, persistent volume, backups); `vllm` (official image, GPU via NVIDIA Container Toolkit); `langfuse`. Dev only: `ollama`, `moodle`.
- **Two Compose files:** `infra/docker-compose.dev.yml` (Ollama, test Moodle, source mounted with live reload) and `infra/docker-compose.prod.yml` (vLLM, proxy, no dev tools).
- **Model weights are never baked into images**; they live in a mounted volume (download once, switch models without rebuilding).
- **No secrets in images or Compose files**; use environment variables / `.env` (git-ignored) or a secret store.
- **Pin every image version** (no `latest`); run containers as a non-root user; multi-stage Dockerfiles for small images.
- Docker Compose is sufficient for the pilot and single-institution servers. Kubernetes only later (large institutions or a multi-institution cloud service); nothing in the code may assume a specific orchestrator.

## Tech stack (decided)
- Backend: **Python 3.12, FastAPI**, SQLAlchemy 2 + Alembic, Pydantic
- Frontend: **React + Vite + TypeScript** (student chat + teacher dashboard; must embed in LTI iframes, Teams tabs, Classroom add-ons)
- Background jobs: **Procrastinate** (Postgres-based queue) — ingestion, LMS polling (every 15–30 min), golden-set runs, version activation
- DB: **PostgreSQL + pgvector**, row-level security per knowledge base. Qdrant only if hybrid retrieval quality later demands it.
- Parsing: **Docling** (OCR for scans; `vision` role for image/diagram captions)
- Embeddings: **BGE-M3** (dense; sparse vectors as the hybrid/keyword option). Same embedding model for all subjects.
- Reranker: **bge-reranker-v2-m3**
- Model gateway: **LiteLLM**; serving: **vLLM** (pilot), **Ollama** (dev)
- Observability: **Langfuse** (self-hosted). Evaluation: **Ragas** / **promptfoo**.
- Tooling: **uv** (Python packages), **ruff** (lint + format), **pytest**, **npm** + **Vitest** (frontend), **Docker** + **Docker Compose** (all services, dev and prod)
- FAISS/Chroma: only for throwaway experiments, never in the product.

## Repository layout
```
backend/
  app/            FastAPI app, API routes, auth, LTI endpoints
  core/           domain logic: knowledge bases, versions, approval, persona, question queue
  ingestion/      Docling parsing, chunking, embeddings
  retrieval/      search, rerank, parent expansion, citation building
  llm/            LiteLLM wrapper, role resolution, prompt assembly
  connectors/     moodle/, teams/, classroom/, upload/  (only place for LMS-specific code)
  jobs/           Procrastinate app and tasks
  migrations/     Alembic
  tests/
frontend/         React + Vite + TS
eval/             golden sets per subject (eval/sets/<subject>.yaml), eval runner, reports
config/           models.yaml, settings
infra/            docker-compose.dev.yml, docker-compose.prod.yml, proxy config
backend/Dockerfile, frontend/Dockerfile   (multi-stage, pinned base images)
```

## Chunking recipe
1. Parse with Docling, keep heading hierarchy.
2. Split by structure: slides = one chunk each (+ speaker notes; merge "x (1/3)" continuations); text by headings, then recursively by paragraph if too long (10–15 % overlap only here); merge tiny fragments.
3. Never split atomic units: definitions/theorems, worked examples, tables (as Markdown), formulas with explanation, code.
4. Prepend heading path (`Subject › Chapter › Section`) + short context from the `context` model to the text that is embedded; store clean original text separately for citations.
5. Parent–child: embed paragraph/slide-level chunks (~300–800 tokens, measured with the embedding model's tokenizer), return parent section to the LLM.
6. Metadata on every chunk: kb_version_id, document_id, page/slide, heading_path, week/topic, content_type (definition | example | exercise | solution | other).
7. Choose settings by **recall@5 on the golden set**, not intuition. For small subjects also test full-context (no RAG).

## Data model (core tables)
```
teacher            (id, name, institution_id, consent_voice, consent_avatar)
knowledge_base     (id, teacher_id, subject_name, status)          -- one per teacher + subject, survives academic years
kb_version         (id, kb_id, version, status)                    -- draft | approved | active; activate atomically
document           (id, kb_version_id, source_platform, external_id, file_name, file_hash, modified_at, approved_at)
chunk              (id, kb_version_id, document_id, parent_id, text, context, heading_path,
                    content_type, page_or_slide, embedding vector(1024), sparse_embedding)
persona_config     (id, kb_id, version, settings jsonb, free_text, approved_at)
platform_context_map (platform, external_course_id, kb_id)         -- many LMS courses → one KB
teacher_question   (id, kb_id, student_ref, question, reason, status, answer)
```
Cross-subject material only via explicit teacher-approved links, never by merging indexes.

## Connectors
Thin translators; all logic (approval, versions, chunking, persona) lives in the core. Common interface:
```
authenticate/launch → user (external id, role), course (external id)
list_courses(teacher) · list_materials(course) · download(material)
get_roster(course) [optional] · send_result(...) [optional, later]
```
- **Moodle/Merlin:** LTI 1.3 for launch/roster/grades; **Moodle Web Services** (`core_course_get_contents` + token file download) for materials. Change detection = **polling job** (every 15–30 min, full comparison by modified time/hash). A future webhook plugin only triggers the same "check course" job. Merlin access requires SRCE approval.
- **Teams:** Teams app + Microsoft Graph; **delta queries** on the team's SharePoint library; Graph notifications only as a trigger (subscriptions expire). Entra ID admin consent, read-only.
- **Google Classroom:** Classroom API (materials/coursework) + Drive API (files, `changes` list); export native Docs/Slides to PDF/DOCX/PPTX before Docling. Workspace admin consent, read-only scopes.
- **Direct upload** in CMP's own web app (SSO via Microsoft/Google) works for every institution and is built first.
- Detected changes → draft version → teacher notification → approval → atomic activation. Per-course rule: "always ask me" (default) or "auto-add from selected sections".

## Build order
1. **Retrieval spike** (scripts, no UI): one real subject + 50–100 golden questions; parsing, chunking, embeddings, search; measure recall@5.
2. **Answers:** model layer with roles + `models.yaml`, citations, abstention + teacher queue; compare 2–3 open models on the golden set.
3. **Product:** teacher persona settings, approval workflow, versioning, question queue, chat UI, direct upload.
4. **Moodle connector** (local Moodle in Docker: LTI 1.3 + Web Services polling) → then SRCE/Merlin.
5. Teams connector → Google Classroom connector.
6. Later: student profile, voice (STT → same RAG → TTS), avatar as a separate A/B experiment.

## Current status
_Update this section when a build step's state changes._

**Step 1 (retrieval spike): pipeline built and measured; experiments and teacher review pending.**

Done:
- Dev stack: Postgres 18 + pgvector 0.8.6, Ollama (optional NVIDIA GPU override), Infinity
  reranker (`rerank` profile; amd64 image only). Migration `0001` with `knowledge_base`,
  `kb_version`, `document`, `chunk`; FORCE RLS per `kb_version_id`; app role is neither owner nor
  superuser. Migration `0002` adds `chunk.also_on_pages`. Tests (RLS isolation, model config,
  chunking, citations) pass.
- Model layer: roles resolved from `config/models.yaml` (dev profile, Ollama). Only `embedding`
  (BGE-M3), `reranker`, `context` (qwen3.5 via Ollama) and the experimental `sparse` role have
  been used; the chat models are placeholders that fit an 11 GB GPU and have not been pulled.
- Ingestion: Docling → slide-level chunks → BGE-M3 → pgvector, every run a new draft version with
  a report in `eval/reports/<subject>/`. Retrieval: dense search filtered by `kb_version_id`,
  optional rerank.
- First subject `Suvremene_tehnike_programiranja` (1 PDF, 945 pages). Local DB (rebuilt on a new
  machine 2026-11-12, older versions are gone): v1 = 766 embedded chunks without duplicate
  folding; v2 = 758 embedded chunks, 67 parents, 8 duplicate pages folded; **v3 = current**,
  same chunks as v2 plus a `context` sentence per chunk (`--context`); v4 = a rejected context
  prompt variant (see below), kept only for comparison. v2–v4 also have sparse vectors
  (`scripts.sparse`).
- Golden set `eval/sets/Suvremene_tehnike_programiranja.yaml`: 50 questions — 25 easy (worded like
  the slides), 25 hard (paraphrases etc.), of which 6 are out_of_scope/admin and must be forwarded
  to the teacher. **Draft written by Claude from the slide text; not yet reviewed by the teacher.**
  Sources re-checked against the PDF on 2026-11-13 (also by Claude): many questions gained
  slides that also answer them, h09's sources were replaced; notes tagged `[review 2026-11-13]`
  list open questions for the teacher (outdated slides 313 and 365; h24/h25 forward or answer).
  Reference answers are not evaluated yet (step 1 scores sources only).
- Baseline on v2 (dense only, k=5): recall@5 **1.00 easy / 0.84 hard** (MRR 1.00 / 0.60);
  real misses h03, h09, h14 (h11, h13 were false misses of the old answer key). Abstain
  questions' top scores (0.47–0.64) overlap the weakest answerable top score (0.50).
- Duplicate folding (v2) left the scores unchanged: in v1 only e16 had two copies of one slide
  in its top 5, and it was already a hit. It frees top-5 slots and makes citations complete.
- `--rerank 20` on v2 (2026-11-14): recall@5 unchanged (1.00 easy / 0.84 hard), MRR easy
  1.00 → 0.98, hard 0.60 → **0.68**. It fixes h09 (278, dense rank 8, → 1) and moves h06, h08,
  h17 to rank 1, but **breaks h11**: the reranker puts slide 57 first (0.70), a code-style
  slide whose snippet literally contains "Interface1" and "// tijelo", and ranks outdated slide
  313 above the real answers 285/286 (0.03 vs 0.005), so the clone would answer h11 wrongly.
  Rerank scores do not separate abstain questions either (h22 0.035 vs answerable h18 0.0016;
  h24 0.78 because slide 480 does state the deadline). Cost on this CPU laptop: ~24 s per
  question (1,000 pairs in 20 min) — fine for evaluation, not for live chat without a GPU.
  **Not made the default**: the gain is real but it trades one miss for another.
- Context and hybrid experiments (2026-11-14), recall@5 / MRR, k=5, 19 hard answerable questions
  (easy recall@5 is 1.00 everywhere):

  | version, mode            | easy MRR | hard recall@5 | hard MRR | hard misses      |
  |--------------------------|----------|---------------|----------|------------------|
  | v2 dense                 | 1.00     | 0.84          | 0.60     | h03 h09 h14      |
  | v2 dense + rerank 20     | 0.98     | 0.84          | 0.68     | h03 h11 h14      |
  | v2 hybrid                | 0.95     | 0.68          | 0.52     | h02 h03 h04 h09 h11 h14 |
  | v3 dense                 | 0.92     | 0.89          | 0.70     | h11 h14          |
  | v3 dense + rerank 20     | 1.00     | 0.89          | 0.77     | h11 h14          |
  | v3 hybrid                | 0.90     | 0.79          | 0.59     | h03 h11 h14 h17  |
  | **v3 hybrid + rerank 20**| **1.00** | **0.95**      | **0.79** | h11              |

  - **`--context` helps**: it fixes h03 (the "zamrzne" wording gap) and h09 (English question),
    because the sentence says what a slide is about in other words. It costs easy MRR in dense
    search (1.00 → 0.92), which reranking restores. The sentences are wordy (most end with
    "Materijal se direktno uklapa u temu…"); a tighter `context` prompt is worth a try.
  - **Sparse as an equal partner in rank fusion hurts** (v2 0.84 → 0.68, v3 0.89 → 0.79): literal
    token overlap pulls in slides that share words but not meaning. As a **candidate pool for the
    reranker** it helps by one question: h14's slide 378 is in the fused top 20 but not the dense
    top 20. One question of 19 is weak evidence; re-check on a second subject before relying on it.
  - h11 stays a miss in every mode: slide 57 (literal "Interface1" / "// tijelo") and outdated
    slide 313 outrank 285/286; slide 285's context sentence mostly talks about static methods.
  - Abstain questions are still not separable by any score (v3 rerank: h24 0.85 because slide 480
    states the deadline; lowest answerable 0.0098 vs h22 0.074).
  - **A stricter context prompt (v4) was tried and rejected.** It asked for what the slide states
    (with English terms) plus one question a student would ask, guided by a short example from
    an unrelated subject. The sentences read better and the questions often match student wording
    (slide 742: "Kako spriječiti zamrzavanje grafičkog sučelja…"), but the scores did not improve:

    | v4 mode               | easy MRR | hard recall@5 | hard MRR | hard misses           |
    |-----------------------|----------|---------------|----------|-----------------------|
    | dense                 | 0.94     | 0.84          | 0.71     | h06 h14 h17           |
    | dense + rerank 20     | 0.98     | 0.89          | 0.77     | h11 h14               |
    | hybrid                | 0.89     | 0.74          | 0.58     | h03 h09 h11 h14 h17 (+ easy e14) |
    | hybrid + rerank 20    | 0.98     | 0.89          | 0.76     | h11 h12               |

    Dense v4 is the first mode to fix h11, but loses h06 and h17. The old prompt stays. **Lesson:**
    with 19 hard questions one question moves recall@5 by 0.05, and every v3/v4 difference is one
    or two questions that trade places, so this golden set cannot tell prompt variants apart.
    Prompt tuning waits for more questions or a second subject. Ingest reports now record
    `prompt_version` (hash of the prompt) next to the model id.
- Timings on this CPU laptop (ThinkPad T14s Gen 6 AMD, no GPU; Ollama in Docker), for planning:
  - `--context` ingest of 758 chunks: **8,612 s (2 h 23 min)**, almost all of it the `context`
    step, ~10.8 s per chunk (qwen3.5 with thinking off; with thinking on it was ~150 s/chunk).
    Parse is cached; dense embedding is a few minutes. Ingest reports now record per-step times
    under `timings`, and eval reports their total `seconds`. The v4 prompt (longer answers):
    9,188 s (2 h 33 min) = context 8,802 s (~11.6 s per chunk) + embedding 339 s + parse/store 7 s.
  - `scripts.sparse` backfill: 444 s for v2, 554 s for v3 (longer texts with context), ~0.6–0.7 s
    per chunk on CPU.
  - Golden-set eval (50 questions): dense 4 s; hybrid 25 s (incl. sparse model load); rerank 20
    ~16–17 min (~20 s per question) — evaluation only, not live chat without a GPU.

Decisions made in step 1:
- Chunking: one chunk per slide; repeated boilerplate slides dropped (seen ≥ 3×); slides under
  25 tokens merged into a neighbour, citation goes to the page with the most content;
  continuation slides grouped under one parent; children over 800 tokens split by paragraph with
  12 % overlap. Tokens counted with the BGE-M3 tokenizer.
- **Duplicate pages are folded, not dropped** (`fold_duplicates`): a page that repeats an
  earlier one (a lecture included twice, a slide reused in a later lecture) is indexed once under
  its first occurrence; the other pages go to `chunk.also_on_pages`, citations name all of them
  and `scripts.evaluate` counts a hit on any copy. Match = same words ignoring order and
  diacritics (word-set overlap ≥ 0.85), **identical numbers**, similar title (compared only if
  both copies have one). Numbers are what separate look-alikes that are different content:
  lecture title slides ("10." vs "12."), build-up slides "(2)"/"(3)", homework 7 vs 10. 0.85
  tolerates an OCR slip; 0.8 already merged two lectures' title slides, and a real edit
  (310/334, `private` vs package access, 0.82) must stay apart. The ingest report lists every
  fold under `duplicates`.
- The "(notes)" copy of lecture 02a (pages 77–94) is mostly scanned images: with OCR off Docling
  gets no text (or OCR noise from the PDF layer), so it is not double-indexed.
- Section labels come from the **PDF outline (bookmarks)**, normalised generically
  (`section_names`: strip extension/page range, leading `NN_YYYY`, trailing parentheses, parts
  shared by most titles). Never fit label parsing to one teacher's naming convention — that caused
  the wrong-lecture-label bug in v1.
- Code blocks take their text from the PDF text layer (pypdfium2), because Docling loses line
  breaks in code. Pages are built from the Docling document in one pass (per-page Markdown export
  was far too slow).
- Docling OCR is off by default (`--ocr` for scans); parse results are cached by file hash in
  `data/.cache/` (a CPU parse of 945 pages takes ~18 min).
- `content_type = solution` only for solutions to homework/exercises; solved lecture examples
  are `example` and stay retrievable. The heuristic is regex-based for the spike.
- **A retrieval-score threshold alone cannot decide abstention** (scores overlap); step 2 needs
  an explicit out-of-scope/admin check (`fast` role) in addition to the threshold.
- The `context` role runs with `reasoning_effort: none` (OpenAI-schema parameter through
  LiteLLM): a thinking model otherwise spends minutes per chunk on reasoning nobody reads.
- The reranker reads heading path + context sentence + text, the same text that was embedded.
- **BGE-M3 sparse is an experiment and a narrow, documented exception to rule 8**: LiteLLM has no
  sparse-embedding API, so `llm/sparse.py` loads the model with `transformers` (the model id lives
  only in `models.yaml`, role `sparse`, kind `sparse`; needs `uv sync --group ingest`). It is
  opt-in (`--hybrid`), off by default, and gets removed if a second subject doesn't confirm it.
- A vLLM pilot profile will be a separate models file selected with `CMP_MODELS_CONFIG`; the
  current `config/models.yaml` is dev-only.
- Windows dev: use `127.0.0.1`, not `localhost` (IPv6 stall); scripts reconfigure stdout to UTF-8.
  A fresh machine needs WSL2 with the "Virtual Machine Platform" feature for Docker Desktop, and a
  current Visual C++ runtime (`winget upgrade Microsoft.VCRedist.2015+.x64`): an old one (14.30)
  makes `import torch` fail with WinError 126 on `shm.dll`.
- Repo workflow: commit directly to `main`; no AI co-author/attribution lines in commits or PRs.

Next:
1. Teacher reviews the golden set: `expected_sources` first, then reference answers, the
   `[review 2026-11-13]` notes and the h24/h25 decision.
2. Retrieval experiments left (rerank, `--context`, hybrid and one prompt variant done, see
   above): chunk size settings; h11. Prompt variants only once the golden set can separate them. Slide 313 (outdated) is a content problem for the
   teacher, not a retrieval one.
3. Ingest a second subject to check the pipeline generalises (labels, chunking, RLS separation)
   and to confirm or drop the `--context` and hybrid-as-rerank-pool gains.
4. Then step 2: pull the dev chat models, answer generation with citations, abstention
   (threshold + `fast` check) and teacher queue, `eval.run` for grounding/abstention.

## Conventions
- Keep the core platform-independent; no LMS-specific code outside `connectors/`.
- No model-specific code outside `llm/`; no model names outside `config/models.yaml`.
- Every trace/log records `kb_version_id`, `persona_config.version` and model id.
- Secrets (LMS tokens, API keys) only via environment/secret store, never in code or the DB in plain text.
- Request least-privilege scopes from every platform.
- Long-running work (ingestion, polling, eval) always runs as a Procrastinate job, never inside a request.

## Commands
_Update this section whenever the setup changes._

**Current state (build step 1, see "Current status"):** the dev compose file has `postgres` (pgvector, PG 18), `ollama`
and, under the `rerank` profile, `infinity` (bge-reranker-v2-m3, CPU). The backend runs on the
host. `api`/`worker`/`frontend`/`langfuse`/`moodle` containers, the prod compose file and the
Dockerfiles arrive in later build steps.
```bash
# one-time: create .env (git-ignored) from the template and change the passwords
cp .env.example .env

# dev containers (run from the repo root)
docker compose -f infra/docker-compose.dev.yml --env-file .env up -d
#   with an NVIDIA GPU for Ollama, add: -f infra/docker-compose.dev.gpu.yml
#   with the reranker, add:            --profile rerank
#   macOS: containers get no GPU (no Metal), so run the native Ollama app instead and start
#   only Postgres: ... up -d postgres   (OLLAMA_API_BASE=http://127.0.0.1:11434 stays the same)
docker compose -f infra/docker-compose.dev.yml --env-file .env exec ollama ollama pull bge-m3:567m

# backend
cd backend
uv sync                                  # install dependencies
uv sync --group ingest                   # + Docling (pulls torch; needed for parsing)
uv run alembic upgrade head              # apply migrations (runs as the owner role)
uv run python -m scripts.smoke           # embed -> pgvector under RLS -> search
# retrieval spike — material lives in data/<subject>/ (git-ignored)
uv run python -m scripts.parse  --subject <folder>          # Docling parse only (cached in data/.cache/)
uv run python -m scripts.ingest --subject <folder>          # new draft kb_version; report in eval/reports/
#   options: --context (`context` role per chunk) --ocr (scans) --min-tokens N --max-tokens N
#            --duplicate-similarity F (default 0.85) | --keep-duplicates (no folding)
#   --context takes ~11 s per chunk on a CPU laptop (2 h 23 min for 758 chunks)
uv run python -m scripts.sparse --subject <folder>         # experiment: BGE-M3 sparse vectors for a version
uv run python -m scripts.ask --subject <folder> "pitanje"  # top-5 hits; --version N, --rerank 20, --hybrid
uv run python -m scripts.evaluate --subject <folder>       # recall@5 + MRR on eval/sets/<folder>.yaml
#   per difficulty (easy/hard); abstain questions report their top score;
#   --version N, -k, --rerank 20, --hybrid (needs scripts.sparse first)
uv run pytest                            # tests (DB tests skip if Postgres is down)
uv run ruff check . && uv run ruff format .  # lint + format
uv run fastapi dev app/main.py           # API with reload (only /health for now)
```

Planned (later build steps):
```bash
docker compose -f infra/docker-compose.dev.yml up -d --build      # + api, worker, frontend, langfuse, moodle
docker compose -f infra/docker-compose.prod.yml up -d             # vLLM on GPU, proxy with HTTPS
uv run procrastinate --app=jobs.app worker                         # background worker
uv run python -m eval.run --subject <subject_id>                   # full golden-set eval (answers, grounding, abstention)
cd frontend && npm install && npm run dev                          # npm test / npm run build
```

Database roles: migrations connect as the owner (`DATABASE_OWNER_URL`); everything else connects
as the app role (`DATABASE_URL`), which is not owner or superuser, so row-level security applies.
KB content is read/written only inside `core.db.kb_scope(kb_version_id)`.

## Open questions
- Which open model wins the `answer` role (Qwen 3.5 vs Mistral Small 4 vs Gemma 4) — decided by golden-set results on Croatian material.
- GPU hosting for the pilot: own hardware vs rented EU cloud GPU (one 24 GB GPU fits the ~30B class). Measure parallel conversations per GPU at acceptable latency.
- `judge` role: an open model may be too weak to grade reliably. The golden set holds no personal data, so a stronger (possibly commercial) judge for evaluation only is an option — decide explicitly.
- Whether the pilot institution accepts open models of non-EU origin (Qwen) when self-hosted in the EU.
- Pilot institution, subject(s) and teacher(s); SRCE contact for Merlin.
- Business model: who pays (institution licence is the working assumption).
