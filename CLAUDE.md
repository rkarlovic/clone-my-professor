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

## Conventions
- Keep the core platform-independent; no LMS-specific code outside `connectors/`.
- No model-specific code outside `llm/`; no model names outside `config/models.yaml`.
- Every trace/log records `kb_version_id`, `persona_config.version` and model id.
- Secrets (LMS tokens, API keys) only via environment/secret store, never in code or the DB in plain text.
- Request least-privilege scopes from every platform.
- Long-running work (ingestion, polling, eval) always runs as a Procrastinate job, never inside a request.

## Commands
_Assumes the layout above; update this section whenever the setup changes._
```bash
# full dev stack in containers (api, worker, frontend, postgres, ollama, langfuse, moodle)
docker compose -f infra/docker-compose.dev.yml up -d --build
docker compose -f infra/docker-compose.dev.yml logs -f api worker

# or: only supporting services in containers, backend/frontend run locally (commands below)
docker compose -f infra/docker-compose.dev.yml up -d postgres ollama langfuse moodle

# production-like stack (vLLM on GPU, proxy with HTTPS)
docker compose -f infra/docker-compose.prod.yml up -d

# backend
cd backend
uv sync                                  # install dependencies
uv run alembic upgrade head              # apply migrations
uv run fastapi dev app/main.py           # API with reload
uv run procrastinate --app=jobs.app worker   # background worker
uv run pytest                            # tests
uv run ruff check . && uv run ruff format .  # lint + format

# evaluation (golden set for one subject)
uv run python -m eval.run --subject <subject_id>

# frontend
cd frontend
npm install
npm run dev
npm test
npm run build
```

## Open questions
- Which open model wins the `answer` role (Qwen 3.5 vs Mistral Small 4 vs Gemma 4) — decided by golden-set results on Croatian material.
- GPU hosting for the pilot: own hardware vs rented EU cloud GPU (one 24 GB GPU fits the ~30B class). Measure parallel conversations per GPU at acceptable latency.
- `judge` role: an open model may be too weak to grade reliably. The golden set holds no personal data, so a stronger (possibly commercial) judge for evaluation only is an option — decide explicitly.
- Whether the pilot institution accepts open models of non-EU origin (Qwen) when self-hosted in the EU.
- Pilot institution, subject(s) and teacher(s); SRCE contact for Merlin.
- Business model: who pays (institution licence is the working assumption).
