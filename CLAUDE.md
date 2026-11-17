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
10. Any change to retrieval, prompts, persona handling or the model config must pass the **internal benchmark** (golden question sets, see "Evaluation strategy") before merge.

## Architecture
One shared pipeline; per-teacher-and-subject data and configuration.

```
LMS (Moodle/Merlin, Teams, Classroom, …) ─► connector ─► CMP core
                                                          ├─ ingestion: Docling → chunking → embeddings → pgvector
                                                          ├─ retrieval: dense → rerank → parent sections
                                                          ├─ prompt assembly: platform rules > teacher persona > student adaptation
                                                          ├─ model layer: LiteLLM → open models (vLLM / Ollama); commercial optional via config
                                                          ├─ teacher question queue + analytics
                                                          ├─ background jobs: Procrastinate (Postgres queue)
                                                          └─ Langfuse tracing (kb_version + persona version + model id on every trace)
```

### Instruction layers (prompt assembly order, higher wins)
1. **Platform rules** – fixed, not editable by teachers (rules 1–6 above).
2. **Teacher persona** – structured fields (ti/Vi, answer length, help level for exercises, examples/analogies, language/terminology, out-of-scope topics, escalation rules) + one free-text field. Versioned; teacher previews before activation; activation reruns that subject's **local check set** (auto-generated + collected questions, see "Evaluation strategy") and is blocked if grounding/abstention drops below threshold. Teachers never have to write test questions.
3. **Student adaptation** – later phase, minimal.

## Model layer (LLM-agnostic, open models first)
- Every model is addressed by **role**, not by name. Roles: `answer`, `fast` (classification, query rewriting, out-of-scope check), `check` (live answer check before the student sees an answer), `context` (chunk context at indexing), `vision` (image/diagram captions), `judge` (evaluation). Embeddings and reranker are configured the same way.
- `config/models.yaml` maps each role to a LiteLLM model string + parameters. Switching a model = editing config and re-running the golden set; no code change.
- **Two layers:** LiteLLM is the gateway the code talks to (routing, fallbacks, rate limits, cost tracking); an inference server runs the model. The core never talks to an inference server directly.
- **Open models first.** Inference servers: **vLLM** for pilot and production (continuous batching for many parallel users, automatic prefix caching, multi-GPU, OpenAI-compatible API) on an EU GPU host; **Ollama** for local development only. llama.cpp directly only for CPU-only demos; SGLang is an acceptable drop-in alternative to vLLM via config. Candidates for `answer`: Qwen 3.5, Mistral Small 4, Gemma 4 — chosen by golden-set results, not by benchmarks. Commercial models (EU region) may be added later purely as config entries.
- **Model decisions are made only on golden-set results from the vLLM setup** that serves students. Ollama runs quantized GGUF variants that can answer differently; never compare or tune on Ollama output.
- Use only the OpenAI-compatible chat schema through LiteLLM. Prompts must work across models: no provider-specific tool formats or prompt syntax in the core. Provider-specific optimisations (e.g., prompt caching) only behind a config flag, with identical behaviour when off. (vLLM's automatic prefix caching needs no code.)
- Keep system prompts and the platform-rule text model-neutral; if a model needs a variant, store it as a per-model override in config, never as `if model == ...` in code.
- Every trace records the exact model id and config version, so results stay comparable across switches.

## Evaluation strategy
A test set is a **sample, not coverage**: 50–100 questions spread over a subject's material
(lectures, scripts, exercises, file types) show whether the clone works, whether it has 200 or
20,000 pages. Three kinds of test set, decided 2026-11-14:

| Set | Material | Built by | Ships with CMP? | Purpose |
|---|---|---|---|---|
| **Internal benchmark** | pilot material used with permission | us | **no** — only the defaults it chooses | choose models, chunking, prompts, rerank/hybrid for everyone (rule 10) |
| **Auto-generated check** | each installation's own | the system, at ingest | no, created locally | safety net per subject, persona activation gate |
| **Collected real questions** | each installation's own | gathered during use | no, stays local | grows into each subject's real test set |

- **Internal benchmark:** real student-style questions with person-checked answer slides (and
  reference answers), across several subjects and material types (slide decks PDF/PPTX, text
  and articles, scans, code-heavy, maths/formulas, tables) — the "golden middle", never tuned to
  one subject. The material is someone else's: it **never goes into the repository** (`data/` is
  git-ignored) and is never redistributed, because permission can be withdrawn. If it is, the
  material is deleted; defaults already chosen stay. Today's only set
  (`Suvremene_tehnike_programiranja`) is its first subject. One subject is not enough: v3/v4
  differences were one or two questions trading places.
- **Auto-generated check:** at ingest the `fast`/`answer` model picks slides spread over the
  material and writes, for each, a question **phrased like a student** (casual, paraphrased,
  sometimes English or without diacritics — otherwise it copies slide wording and scores look too
  good). The slide it was written from is `expected_sources`; nobody marks anything. **No
  model-written reference answers** (grading a model against a model's answer is circular):
  answers are judged against the cited slide itself (grounding + does it answer the question).
  Plus **generic abstention questions** that need no subject knowledge (deadlines, grades,
  another subject) — the same for every subject. The teacher may delete bad questions; doing
  nothing is fine.
- **Collected real questions:** forwarded questions the teacher answers in the queue (the teacher
  naturally points to the source, so a question + source comes from normal work) and answers
  students mark 👎. They contain student text, so they stay inside the installation and are
  minimised like any personal data (rule 9).
- Teachers never have to write a test set. A hand-written set stays optional for teachers who
  want one.
- Local checks run on the institution's own server; nothing is sent to us.

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
- Embeddings: **BGE-M3** (dense). Same embedding model for all subjects. (Its sparse vectors
  were tried for hybrid search and dropped on 2026-11-17, see "Decisions made in step 1".)
- Reranker: **bge-reranker-v2-m3**
- Model gateway: **LiteLLM**; serving: **vLLM** (pilot), **Ollama** (dev)
- Observability: **Langfuse** (self-hosted). Evaluation: **Ragas** / **promptfoo**.
- Tooling: **uv** (Python packages), **ruff** (lint + format), **pytest**, **npm** + **Vitest** (frontend; Node runs in Docker in dev), **Docker** + **Docker Compose** (all services, dev and prod)
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
eval/             internal benchmark sets (eval/sets/<subject>.yaml; sets made from someone else's
                  text, e.g. a teacher's own questions, in eval/sets/local/, git-ignored),
                  eval runner, reports (git-ignored)
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
                    content_type, page_or_slide, embedding vector(1024), also_on_pages)
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
2. **Answers:** model layer with roles + `models.yaml`, citations, abstention + teacher queue; compare 2–3 open models on the internal benchmark. Auto-generated check sets (questions from slides, judged against the slide) can be built here too, since they need the `judge` role.
3. **Product:** teacher persona settings, approval workflow, versioning, question queue, chat UI, direct upload.
4. **Moodle connector** (local Moodle in Docker: LTI 1.3 + Web Services polling) → then SRCE/Merlin.
5. Teams connector → Google Classroom connector.
6. Later: student profile, voice (STT → same RAG → TTS), avatar as a separate A/B experiment.

## Current status
_Update this section when a build step's state changes._

**Step 1 (retrieval spike): pipeline built and measured; experiments and teacher review pending.**
**Step 2 part A (answers): started 2026-11-14, see the step 2 block below.**
**Step 3 (product): started 2026-11-17 with direct upload, see the step 3 block below.**

Done:
- Dev stack: Postgres 18 + pgvector 0.8.6, Ollama (optional NVIDIA GPU override), Infinity
  reranker (`rerank` profile; amd64 image only). Migration `0001` with `knowledge_base`,
  `kb_version`, `document`, `chunk`; FORCE RLS per `kb_version_id`; app role is neither owner nor
  superuser. Migration `0002` adds `chunk.also_on_pages`. Tests (RLS isolation, model config,
  chunking, citations) pass.
- Model layer: roles resolved from `config/models.yaml` (dev profile, Ollama). Only `embedding`
  (BGE-M3), `reranker`, `context` (qwen3.5 via Ollama) have been used; the chat models are placeholders that fit an 11 GB GPU and have not been pulled.
- Ingestion: Docling → slide-level chunks → BGE-M3 → pgvector, every run a new draft version with
  a report in `eval/reports/<subject>/`. Retrieval: dense search filtered by `kb_version_id`,
  optional rerank.
- First subject `Suvremene_tehnike_programiranja` (1 PDF, 945 pages). Local DB (rebuilt on a new
  machine 2026-11-12, older versions are gone): v1 = 766 embedded chunks without duplicate
  folding; v2 = 758 embedded chunks, 67 parents, 8 duplicate pages folded; **v3 = current**,
  same chunks as v2 plus a `context` sentence per chunk (`--context`); v4 = a rejected context
  prompt variant (see below), kept only for comparison. v2–v4 also have sparse vectors
  (`scripts.sparse`). `ask`/`evaluate` default to the newest version (v4), so pass
  `--version 3` for the current best.
- Golden set `eval/sets/Suvremene_tehnike_programiranja.yaml`: 50 questions — 25 easy (worded like
  the slides), 25 hard (paraphrases etc.), of which 6 are out_of_scope/admin and must be forwarded
  to the teacher. **Draft written by Claude from the slide text; not yet reviewed by the teacher.**
  **Decision (2026-11-14): work with this AI-reviewed set for now; the teacher reviews it later**
  (before the pilot and before any model choice is final). Scores until then are provisional: a
  teacher's source changes can move individual questions. Provisional defaults until the
  teacher decides: h24/h25 are forwarded to the teacher (rule 2: deadlines go to the teacher),
  and slide 313 stays a partial source of h09 and a known wrong answer for h11.
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
    "Materijal se direktno uklapa u temu…"); a tighter prompt was tried as v4 and rejected (below).
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
- **Teacher-written question set (2026-11-14):** the teacher's 60 numbered oral-exam practice questions
  (2016-17, docx in `data/.questions/`) are the first human-written questions. They are the
  teacher's text, so the set lives in **`eval/sets/local/Suvremene_tehnike_programiranja.teacher.yaml`
  (git-ignored, never committed)**, run with `--set`, questions grouped as `exam`.
  `expected_sources` were marked by Claude from the slide text, a title index of every slide and
  keyword search, **not only from search results** (that would bias the set toward the retriever
  under test); not yet confirmed by the teacher. Many questions have several parts; their
  sources cover all parts and a hit on any counts. Ids t01–t60 follow the docx numbering; an
  unnumbered paragraph continues the question before it (35 + "(auto)boxing", 60 + "JUnit").
  The table below was measured on a first parse that split those two into separate questions
  (62); on the corrected 60, v3 dense is 0.98 / 0.89 (same single miss, t02); the other modes
  are to be re-run. Results (recall@5 / MRR, 62-question parse):

  | mode                  | v2          | v3              | v4          |
  |-----------------------|-------------|-----------------|-------------|
  | dense                 | 0.97 / 0.89 | 0.98 / 0.90     | 0.98 / 0.92 |
  | dense + rerank 20     |             | 0.98 / 0.91     |             |
  | hybrid                |             | 0.98 / 0.92     |             |
  | hybrid + rerank 20    |             | **1.00 / 0.91** |             |

  - Scores are high because exam questions use the course's own terms ("Enkapsulacija
    (učahurivanje), što je?"): they behave like the easy set, not like casual student questions.
    Their value is human wording and coverage of the whole course (all 14 lectures).
  - The only miss is t02 ("Što čine naredbe javac, a što java? Objektni ili byte kod što je?"):
    the answer is spread over 26/28/31 and "objektni" pulls in slides about objects (43, 53);
    hybrid + rerank finds it.
  - v3 hybrid + rerank 20 is the best mode on both sets. Unlike on the hard set, hybrid alone
    does not hurt here (literal terms are what sparse matches well).
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
- **BGE-M3 sparse (hybrid search) was an experiment and is removed (2026-11-17).** It was the
  one model call outside LiteLLM (no sparse-embedding API; `llm/sparse.py` loaded the model
  with `transformers`), kept only until a second subject confirmed it. It did not: on
  `Rad_s_darovitom_djecom` hybrid lost to dense in every mode. Code, the `sparse` role, the
  `--hybrid` flags and `scripts.sparse` are gone; migration `0005` drops
  `chunk.sparse_embedding`. The hybrid numbers in this file are history. A keyword option
  comes back only through a server LiteLLM can call, and only if a benchmark shows a gain.
- A vLLM pilot profile will be a separate models file selected with `CMP_MODELS_CONFIG`; the
  current `config/models.yaml` is dev-only.
- Windows dev: use `127.0.0.1`, not `localhost` (IPv6 stall); scripts reconfigure stdout to UTF-8.
  A fresh machine needs WSL2 with the "Virtual Machine Platform" feature for Docker Desktop, and a
  current Visual C++ runtime (`winget upgrade Microsoft.VCRedist.2015+.x64`): an old one (14.30)
  makes `import torch` fail with WinError 126 on `shm.dll`.
- Repo workflow: commit directly to `main`; no AI co-author/attribution lines in commits or PRs.

**Step 2 (answers), part A: pipeline built with a live answer check; evaluated 2026-11-14/15.**
- `core/answer.py` `answer_question`: scope check (`fast`: GRADIVO / ADMIN / DRUGO, given the
  subject name and lecture labels) → retrieval (same modes as step 1) → score floor → `answer`
  role with numbered sources `[1]…[n]` → citation check → answer check (`check`, see below).
  Forwarded with a reason (`admin`, `out_of_scope`, `low_score`, `no_source`, `failed_check`)
  and a fixed, gender-neutral message when:
  the scope check says ADMIN/DRUGO; the best score is under the role's `min_score`; the reply
  contains `NE_ZNAM` (even next to a partial answer); or it cites no real source (rule 1).
  Citation numbers pointing at no source are removed. Hits sharing a parent section become one
  source. An unclear scope label lets the question through (later checks still guard it).
- Prompts in `llm/prompts.py` (platform rules, persona header, scope check, judge),
  model-neutral, with per-model variants via `prompt_overrides`; their hashes go on every trace
  with `kb_version_id`, `persona_version` (None until step 3), model ids and config version.
- Teacher queue: migration `0003` `teacher_question` (kb_id, kb_version_id, opaque
  student_ref, question, reason, status, answer); `core/queue.py`. No RLS on it yet (step 3 adds
  teacher-level access). `scripts.ask --answer` queues forwarded questions (`--no-queue` to
  skip); `scripts.evaluate_answers` never queues.
- Score floor lives on the model in `models.yaml` (`min_score`): embedding 0.40 (a safety floor
  under the lowest answerable 0.50); **none for the reranker**: a valid casual question
  ("Zašto mi se zamrzne prozor…") had top rerank score 0.002, so any floor forwards good
  questions. Fused hybrid scores are rank-based and get no floor.
- Dev chat roles (`answer`, `fast`, `judge`) run with `reasoning_effort: none` on CPU; re-test
  with thinking on vLLM before a model choice.
- `scripts.evaluate_answers`: answered / cited_expected / retrieved_expected per difficulty,
  forwarded + reasons for abstain questions; `--judge` adds grounded / on_topic from the `judge`
  role, judged against the cited slides only (no reference answers); `--ids`, `--difficulty`,
  `--limit` for small runs.
- First observations (6 questions e01 e10 h03 h09 h20 h24, v3 dense, dev models):
  - easy and h03 answered, citing the expected slides; h24 forwarded `admin` by the scope check.
  - **The 4B scope check misses out-of-scope questions**: h20 (Flask REST API) was labelled
    GRADIVO; the answer model said `NE_ZNAM`, so it was still forwarded, as `no_source`.
  - **The 9B judge is lenient**: h09 ("Does Java support multiple inheritance?") began
    "Da, Java ne dopušta…" and added an unrelated multi-catch paragraph, yet was judged grounded
    and on topic. Judge results from the dev judge are not trustworthy (see "Open questions").
  - The 4B as `answer` (tried via a scratch config) added a wrong claim to a correct answer; the
    9B was stricter (said `NE_ZNAM` when dense search missed slide 742).
- **First full answer evaluation (2026-11-14)**: golden set, v3 dense k=5, dev models (9B answer
  + judge, 4B scope), config `dev-2026-11-14.4`, 50 questions in **4,703 s (78 min)**, no errors:

  | group | answered | cites expected slide | expected slide retrieved | judge: grounded | judge: on topic |
  |-------|----------|----------------------|--------------------------|-----------------|-----------------|
  | easy (25) | 0.96 | 0.96 | 0.96 | 0.92 | 1.00 |
  | hard (19) | 0.95 | 0.84 | 0.89 | 0.61 | 0.94 |

  Abstain (6): 5 forwarded (h24/h25 `admin` by the scope check; h20/h21/h23 `no_source`: the
  scope check labelled them GRADIVO, the answer model said `NE_ZNAM`), **h22 answered**.
  - **Answers that cite real slides but add outside knowledge** pass the citation check: h22
    (Stream API, not in the course; slide 523 only mentions `stream()`, the model filled in
    `filter`/`map`) and h11 (Java 8 default methods explained from general knowledge). Rule 1
    needs more than "cites a source": a grounding check in the live path, or a stricter prompt.
  - **Scope check false positive**: e24 "Što je imitiranje objekata (mocking) u testiranju?" was
    labelled ADMIN: the 4B reads *testiranje* (software testing) as exams.
  - The 9B sometimes treats the sources as the student's ("materijala koje ste naveli") and
    misreads words ("Javin programski okvir" → "javni"). Both are prompt work.
  - h14 (retrieval missed slide 378) was forwarded as `no_source` instead of guessed: correct.
  - **The dev judge is noisy both ways**: it passed h09's contradictory answer in the 6-question
    run, failed h02 although slide 617 says exactly what the answer says, and gave no verdict on
    h11. Treat judge numbers as rough until a person checks a sample of verdicts.
- **Prompt fixes, second run (2026-11-14)**, same setup (answer prompt `faff44ecb943`, scope
  prompt `fcaaa349520c`), 50 questions in **4,892 s (82 min)**, no errors:
  - Scope prompt now lists the lectures as a bullet list, says that course terms which sound like
    teaching organisation (software testing, exceptions) are GRADIVO, ADMIN = grades/points,
    deadlines, exam dates, enrolment, attendance, DRUGO = other subjects/languages and
    technologies not among the topics (web frameworks, databases); unsure between GRADIVO and
    DRUGO → GRADIVO. The 4B and the 9B gave the same labels on test questions, so the model size
    was not the problem.
  - **`ADMIN_TERMS` keyword net** in `core/answer.py` (ocjen-, bodov-, kolokvij-, dvojk-/…/petic-,
    "rok… za", rokovi, ispitni rok, datum ispita) forwards as `admin` without a model call: every
    prompt wording still labelled "Koliko bodova trebam skupiti za četvorku?" GRADIVO. It matches
    only h24/h25 among all 110 questions (golden + teacher sets). Croatian only; another language
    adds its own terms.
  - Answer prompt: the sources are excerpts from the teacher's materials that the student did
    not write and cannot see; no methods/classes/code/versions/terms not in the sources;
    `NE_ZNAM` also when a source only mentions a topic in passing; a partially covered question
    is answered in part with a note that the rest is not covered; names exactly as in the sources.

  | group | answered | cites expected slide | expected slide retrieved | judge: grounded | judge: on topic |
  |-------|----------|----------------------|--------------------------|-----------------|-----------------|
  | easy (25) | **1.00** | **1.00** | 1.00 | **0.96** | 1.00 |
  | hard (19) | 0.95 | 0.84 | 0.89 | 0.61 | 0.94 |

  Abstain (6): 5 forwarded, now with the right reason: h20 (Flask), h23 `out_of_scope` by the
  scope check (12 s instead of ~60 s), h24/h25 `admin` by `ADMIN_TERMS` (0 s); h21 (JDBC) still
  labelled GRADIVO and forwarded `no_source`. **h22 still counts as answered**, but no longer adds
  outside knowledge: it says the sources only mention `stream()` and do not cover `filter`/`map`.
  - e24 (mocking) fixed: GRADIVO, answered from 910/911. Hard numbers unchanged.
  - **New problem: refusals written as prose.** Rule 4 (answer the covered part) makes the 9B
    write "materijali ne pokrivaju…" with a citation instead of `NE_ZNAM`, so the citation check
    lets it through: h22, h11 (starts "Da, sučelje može imati metodu s tijelom", then says the
    sources say the opposite and quotes "prema pravilu 1 i 3"), h17 (starts "U izvorima … ne
    nalazi se", then answers in part). Next: a forward when the answer is mostly a refusal
    (a check or a stricter rule 4), and never mention the rules to the student.
  - "materijali koje ste naveli" no longer appears.
- **Prose-refusal fix, 8-question check (2026-11-14)**, answer prompt `4bb02ffc8a8b`, 779 s:
  rule 3 now says to write only `NE_ZNAM` when the sources do not answer the main question
  (without explaining what is missing), rule 4 allows a partial answer only when the main part is
  answered, with one closing sentence on what is not covered; rule 8 forbids mentioning the
  rules. `is_prose_refusal` in `core/answer.py` forwards a reply whose **first sentence** says
  the sources/materials do not contain the answer (needs both an izvor/materijal word and a
  negation such as "ne nalazi se", "ne pokrivaju"; Croatian, like `ADMIN_TERMS`).
  - h17 fixed: a clean answer from 787/789 (delegate, ComponentUI), judged grounded.
  - No rule mentions any more. Controls e24, h02, h09, h13 still answered with the expected slide.
  - **h22 and h11 are not fixed**: the model changed how it opens, so the first-sentence check
    never fired. h22 now opens with the ZIP `stream()` example and says the course does not
    explain `filter`/`map`; h11 still opens "Da, sučelje može imati metodu koja ima tijelo" and
    then says the slides say the opposite. h09 again opened "Da, Java ne dopušta…" and added an
    unrelated multi-catch paragraph. **Conclusion: prompt wording and phrase checks do not stop
    off-target or self-contradicting answers; a grounding check in the live path is needed**
    (the dev judge marked h22 off-topic and h11 ungrounded, but also failed correct h02/h13, so a
    9B judge is too noisy for that role on its own). Not yet run on the full set.
- **Live answer check (2026-11-15)**: new role `check` (dev: the 9B), prompt `answer_check`,
  forward reason `failed_check` (migration `0004`). After the citation check, the model writes
  one sentence on what the cited sources say about the question and one on the answer's main
  claim, then `ODGOVARA` (answers the question), `PROTURJEČI` (contradicts the sources or
  itself), `IZVAN` (adds terms/claims not in the sources). Asking for the two sentences first
  mattered: without them the 9B passed h22 and h11. A reply of bare DA/NE lines is read by
  position; an unreadable verdict lets the answer through. `--no-check` in `evaluate_answers`.
  - **Only `PROTURJEČI=DA` forwards.** Replayed on 92 stored answers (the two full runs and the
    8-question run, no answer-model calls), it said DA only for wrong answers (h11 every time, the
    first run's "Da, Java ne dopušta…" h09). `ODGOVARA=NE` also hit good answers (h03, h06, h13),
    whose summary sentence agreed with the answer, mostly answers ending with "materijali ne
    pokrivaju…". A note in the prompt that such a closing sentence is allowed fixed h13 only.
    `IZVAN` is noisy the same way. Both are kept on the trace, not used.
  - Full run with the first rule (forward on `ODGOVARA=NE` too), config `dev-2026-11-15.1`,
    50 questions in **6,550 s (109 min)**, no errors: abstain **6/6 forwarded** (h22 `no_source`:
    the answer model said `NE_ZNAM` this time), but h03, h06, h11, h13 forwarded by the check
    (hard answered 0.74). Replaying the final rule on that run's answers forwards only h11:

    | group | answered | cites expected slide | expected slide retrieved |
    |-------|----------|----------------------|--------------------------|
    | easy (25) | 1.00 | 1.00 | 1.00 |
    | hard (19) | **0.89** | **0.89** | 0.89 |

    Hard misses: h14 (retrieval missed 378, forwarded `no_source`) and h11 (forwarded by the
    check: retrieval misses 285/286, and the answer said "Da" against slide 313). Judge numbers
    from that run (easy grounded 0.92; hard 0.79 over the 14 it judged) are still rough.
  - **Full run with the final rule** (config `dev-2026-11-15.1`, 50 questions in **6,616 s
    (110 min)**, no errors) matches the replay exactly: easy 1.00 / 1.00 (grounded 0.96), hard
    answered and cites expected slide **0.89 / 0.89**, misses only h14 (`no_source`) and h11
    (`failed_check`, PROTURJEČI=DA). No good answer was forwarded by the check. Abstain **5/6**:
    h20/h23 `out_of_scope`, h24/h25 `admin`, h21 `no_source`; **h22 answered** in prose (one
    sentence: the sources mention `stream()` for ZIP entries, `filter`/`map` are not described,
    citing all five sources). Check verdict ODGOVARA=NE, PROTURJEČI=NE, so the final rule lets it
    through. Harmless (no outside knowledge) but not a forward; hard judge grounded 0.59 (noisy).
  - **h22 fix (2026-11-15): `is_mostly_refusal`.** `ODGOVARA=NE` now forwards (`failed_check`)
    when at least half of the answer's sentences say the sources/materials do not cover it
    (same izvor/materijal + negation words as `is_prose_refusal`, plus "nisu opisani/objašnjeni";
    citations stripped, fragments under 3 words ignored). h22's prose answers are one sentence,
    or one example sentence + one refusal; good answers that ODGOVARA=NE hit (h03, h06, h13)
    have one closing "materijali ne pokrivaju…" in 4–5 sentences. Applied to all 183 stored
    answers (verdicts replayed where a run had none): forwards h22 in all three prose versions
    and h11 (PROTURJEČI), no good answer; e11 (2 sentences, one a closing note) has ODGOVARA=DA.
    Croatian word lists, like `ADMIN_TERMS`.
  - **Full run with the h22 fix** (50 questions, **6,692 s (112 min)**, no errors): abstain
    **6/6 forwarded** for the first time with the final rule (h22 `failed_check`: ODGOVARA=NE
    on a mostly-refusal answer); easy 1.00 / 1.00; hard answered 0.89, cites expected **0.84**
    (misses h11 `failed_check`, h14 `no_source`, and h03 answered citing only 784
    (`invokeLater`) instead of 636/744: answer-model variation at temperature 0.2, retrieval
    still had the slides). No good answer forwarded. Judge grounded easy 0.80 (0.96 last run;
    e16 says Java boxes to `System.Object`, which slide 365 itself says (see the judge review);
    others are judge noise),
    hard 0.59. Run-to-run variance of one or two questions is normal on this set.
- **Scope check sweep, all 110 questions (golden + teacher), 4B `fast` (2026-11-15)**, ~10–13 s
  per question with model reload (19–24 min per sweep):
  - Current prompt: 3 wrong labels: h21 (JDBC) and h22 (Stream API) GRADIVO (both still
    forwarded later), and **t04 "C++ i Java-a u čemu se razlikuju?" DRUGO: a teacher's exam
    question wrongly forwarded** ("drugi programski jezik" in the DRUGO line).
  - Topic first ("TEMA: <lecture> or nijedna", then the label): worse, 4 wrong (h20–h23 all
    GRADIVO). The 4B writes "TEMA: nijedna" correctly for Flask/JDBC/Spring, then labels them
    GRADIVO anyway; and it writes "nijedna" for ~33 in-course questions too (e04, h01, t36
    upcast/downcast…), so code cannot decide from that line either. Lecture titles are too coarse
    for the 4B to map questions onto. Rejected.
  - One added line "a comparison of a course language/technology with another one is GRADIVO":
    t04 still DRUGO, JDBC still GRADIVO. Reverted.
  - **Conclusion: prompt wording for the 4B does not move these labels.** h21 is forwarded anyway
    (`no_source`), so its only cost is ~60 s and the reason label. The real problem is DRUGO
    forwarding a valid question (t04) with no later check. Options: DRUGO does not forward on its
    own (answer pipeline decides, as for h21); give the check slide-level evidence (top slide
    titles) instead of lecture titles; or a stronger `fast` model on vLLM.
- **DRUGO no longer forwards on its own (2026-11-15).** Only ADMIN (model or `ADMIN_TERMS`)
  forwards before retrieval (`SCOPE_FORWARDS_AT_ONCE`); a DRUGO question goes through retrieval,
  the answer model and both checks, and if any of them forwards it, the reason is
  `out_of_scope` (the label is kept as the reason, and on the trace). Live check: **t04 answered**
  from 29/176/264 (expected slides, check ODGOVARA=DA); h20 and h23 still forwarded
  `out_of_scope` (the answer model said `NE_ZNAM`), now after ~60 s instead of ~12 s; h21
  `no_source` as before (its label is GRADIVO). The safety for off-topic questions now rests on
  the answer model's `NE_ZNAM`, the citation check and the answer check, as it already did for
  every question the scope check labels GRADIVO.
- **Teacher's 60 questions, full answer run (2026-11-15)**, v3 dense k=5, `--judge`, all rules
  above, **9,993 s (2 h 47 min)**, no errors: answered **0.98**, cites expected slide **0.98**,
  judge grounded 0.88, on topic 0.98. Only miss t02 (javac/java, retrieval; `no_source`, the
  known dense miss). t04 labelled DRUGO and answered correctly. The check said ODGOVARA=NE only
  for **t25** (composition vs aggregation, UML): 3 sentences, two of them "izvori ne navode…",
  "materijali ne opisuju…", verbs the refusal list lacked, so it reached the student as a
  mostly-refusal. Added "ne navod-", "ne opisuj-"; replayed on every stored answer (golden runs
  + this one) it forwards t25 and nothing else new. Exam questions use the course's own terms,
  so these numbers behave like the easy set (see step 1).
- **Judge verdicts reviewed (2026-11-16, by Claude, not yet by a person).** 35 verdicts from
  the last golden run and the teacher run: every not-grounded/off-topic verdict (19) plus 8 random
  grounded ones per run, read against the cited slides. Details per question (quotes the
  teacher's slides, so git-ignored): `eval/reports/Suvremene_tehnike_programiranja/judge-review-2026-11-16.md`.
  - Judge "grounded": 16/16 right. Judge "not grounded": 9 right, 8 wrong, 2 borderline, so a
    negative is right about half the time. **6 of the 8 wrong negatives have a closing
    "materijali ne pokrivaju …" sentence that names topics not in the sources** (SpringLayout,
    SwingWorker, profiling); the judge treats those as claims.
  - Corrected grounding of the last golden run: easy ≈ 0.96 (judge 0.80), hard ≈ 0.82 (judge 0.59).
    Judge numbers understate grounding; use them only as a pointer to answers worth reading.
  - **Real errors the live check passed:** e11 presents C#'s `internal` as a Java modifier
    (wrong column of slide 176's comparison table); h03 advises invokeLater/invokeAndWait for the
    long job (wrong, outside knowledge); t56 misreads join(); t35 contradicts itself (from an
    ambiguous slide line); h09 opens "Da, ali …" for a "no". All had PROTURJEČI=NE.
  - Slide content issues for the teacher: 365 says Java boxes to `System.Object` (C#); 364's
    "nisu klase i nemaju svoje metode" is under the wrappers bullet but means primitives; 313
    outdated; 585 "kolekcija" vs 620 "nije kolekcija".
  - Next for answers: the closing "not covered" sentence should only name a part of the question
    (it is clutter for students and fools the judge); tell the judge such a sentence is not a
    claim; comparison tables need care (e11). (Both tried 2026-11-16, see below.)
  - Cost: ~42 s per answered question on this laptop (≈ 25–35 s model + reload).
- **Closing sentence and judge prompt (2026-11-16).** 29 of the last runs' answered questions
  (golden + teacher) had a closing "not covered" sentence; most named topics nobody asked about.
  - Answer prompt rule 4 (`702dba37bb04`): the closing sentence names only the part of the
    question not covered, in the question's words, no other topics/methods/classes, and is left
    out when the sources answer the whole question. Re-run on 17 of those questions (11 golden,
    6 teacher; 2,760 s): **9 lost the sentence** (e09 e21 h04 h09 h10 h15 h18 t06 t34), t60 now
    says only "profiling is not covered"; e16 e19 h02 h03 t23 t33 t38 still add a generic one
    ("detailed examples are not covered"), h03's names `new Thread()`/`ExecutorService` and h18
    lists Git setup steps without the refusal words. All 17 still answered citing the expected
    slide; h09 now opens "Ne, Java ne dopušta…". h03's wrong invokeLater advice remains (the
    check said ODGOVARA=NE, IZVAN=DA, not forwarded). Not yet run on the full sets.
  - Judge prompt (`fe9dabab2dd9`): a sentence saying what the materials do not cover is not a
    claim; rewording and obvious inferences are fine. Re-judged the 35 reviewed answers (33 with
    a clear manual verdict): **25/33 agree, same as the old prompt**; it now passes h04 and h10
    but fails e20, and still fails e21 and t60 for their closing sentences. No real error passed.
  - A reasoning-first judge (list claims without a source, then the verdict) was tried and
    **rejected**: 23/33, it passed 6 of the 9 real errors (h02 h03 h09 t10 t35 t56). Unlike the
    live check, writing reasons first made the 9B judge lenient.
  - **Conclusion: the 9B judge is not fixed by prompt wording.** Judge numbers stay a pointer to
    answers worth reading; a reliable judge needs a stronger model (see "Open questions").
  - **Full golden run with the new rule 4** (answer `702dba37bb04`, judge `fe9dabab2dd9`,
    50 questions in **6,586 s (110 min)**, no errors): easy 1.00 / 1.00 (judge grounded 0.80);
    hard answered **0.84**, cites expected **0.79** (was 0.89 / 0.84); abstain 5/6. Closing
    "not covered" sentences 11 → 9 of 42 answered: far less than the 17-question re-run showed.
    - **h01 regression from the rule:** a correct answer whose closing note the model labelled
      `**NE_ZNAM**:` was forwarded (`no_source`).
    - **h11 wrong answer reached the student**: "Da, sučelje može imati metodu koja ima tijelo"
      (retrieval missed 285/286 again), check ODGOVARA=NE, IZVAN=DA, PROTURJEČI=NE, so nothing
      forwarded it; last run PROTURJEČI=DA caught it. Run-to-run variance of the 9B check.
    - h09 opened "Da, Java ne dopušta…" again and was forwarded by PROTURJEČI (correct catch);
      h22 answered in one sentence about ZIP `stream()` (ODGOVARA=DA; harmless, off-target);
      h14 `no_source` as before.
    - On this set the rule's gain is within run-to-run variance and it caused h01.
      **Reverted (2026-11-16)**: answer prompt back to the earlier rule 4 (the answer prompt is
      identical to the one before this experiment); the judge note stays (harmless, same
      agreement). Revisit the closing sentence with a stronger model on vLLM or a bigger set.
- **Auto-generated check set, first version (2026-11-16/17).** `core/checkset.py` +
  `scripts.checkset` (prompt `checkset_question`, role `answer`): eligible = embedded chunks of
  ≥ 30 words, not `solution` or `exercise` (a homework slide yields "how do I build the
  calculator", which tests rule 4, not search); material split into n consecutive bins, one
  random slide per bin (seeded; the model may answer `PRESKOČI`, then the next slide in the
  bin, up to 3 tries). Styles in turn: casual, symptom, English, paraphrase, no_diacritics (a
  casual question with diacritics stripped in code). Questions ≤ 15 words, about one thing,
  not listing the slide's contents (the first trial copied slide bullet lists). Expected source
  = the slide it was written from; no reference answers. Plus 12 **generic abstention
  questions** (7 admin, several without `ADMIN_TERMS` words, one English; 5 everyday off-topic),
  the same for every subject, Croatian like `ADMIN_TERMS`. Output in the golden-set format,
  difficulty `check`, in `eval/sets/local/<subject>.check.yaml` (git-ignored: it paraphrases the
  installation's material); regenerating needs `--force` because a teacher may have deleted
  questions. Not yet hooked into ingest (step 3 runs it as a Procrastinate job after ingest).
  - First set for v3: 60 slide questions (slides 4–935) + 12 generic in **1,074 s (18 min,
    ~18 s per question with the 9B)**. Read through: ~5 poor (c002 nonsense; c031/c045/c058 say
    "in this model/example/test class"; c017/c051 vague): what a teacher would delete.
  - Retrieval, recall@5 / MRR (60 questions): dense **0.82 / 0.69**, hybrid 0.85 / 0.68,
    dense + rerank 20 0.90 / 0.82, **hybrid + rerank 20 0.92 / 0.85** (8 s / 80 s / 27 min /
    27 min). Same order of modes as the golden hard set (0.89 / 0.79 / 0.89 / 0.95) and similar
    level: the generated questions behave like hard, student-style questions, not like the
    easy set. Dense misses by style: symptom 4/12, paraphrase 3, casual 2, English 2,
    no_diacritics 0.
  - Many misses are the neighbouring slide (351 → 350, 657 → 658, 827 → 822/823, 913 → 912):
    with one expected slide per question and nobody marking, a neighbour that also answers
    counts as a miss, so check-set recall understates retrieval somewhat.
  - Generic questions' dense top scores 0.31–0.61 overlap the lowest answerable 0.48: again,
    a score floor alone cannot decide abstention.
  - **Answer run on the check set (2026-11-17)**, v3 dense k=5, `--judge`, all rules above,
    72 questions in **9,446 s (2 h 37 min)**, no errors:

    | group | answered | cites expected slide | expected slide retrieved | judge: grounded | judge: on topic |
    |-------|----------|----------------------|--------------------------|-----------------|-----------------|
    | check (60) | 0.95 | 0.72 | 0.82 | 0.67 | 0.97 |

    - Cites-expected is lower than retrieval because of how the set is built, not mainly because
      of errors: 10 of the 14 answered questions that miss the expected slide cite the
      **neighbouring** slide (e.g. 199 for 200, 912 for 913), which says the same thing. Counting
      neighbours, 0.88. When retrieval missed the slide, the answer model mostly answered from
      the neighbours instead of forwarding (8 of 11 retrieval misses answered).
    - Forwarded 3: c007/c053 `no_source`, c017 `failed_check`. c017 was one of the poor questions,
      and its answer mixed in map ordering from an unrelated slide, so the check was right to stop it.
    - Outside knowledge got past the check in c036: O(1)/O(n)/O(n²) for `ArrayList`/`LinkedList`
      `get`, with IZVAN=DA, which does not forward. c037 is long and says "vašim izvorima" again.
    - Generic abstain questions: **11/12 forwarded.** 5 by `ADMIN_TERMS`, g03 (signature after
      absences) by the scope check, g07 (English exam question) labelled DRUGO and forwarded
      `out_of_scope` after `NE_ZNAM`, and all 5 everyday off-topic questions `out_of_scope`.
    - **g06 "jel se mora dolazit na vjezbe" was answered:** "Da, dolazak na vježbe je obavezan",
      inferred from the timetable slide (2+1 h × 15 weeks). The scope check said GRADIVO, and
      `ADMIN_TERMS` has no attendance words. This is a rule 2 failure (attendance is the
      teacher's to answer), and the claim itself is not on the slide.
    - **Fix (2026-11-17): attendance words in `ADMIN_TERMS`**, only next to a class word
      ("dolaz-/prisutn-/izost- na/s … predavanj-/vježb-/vjezb-/nastav-/seminar-", "vježbe su
      obavezne", "obavezan dolazak", "dobiti potpis"): bare "prisutnost" (an element in a set)
      and "potpis" (method signature) are course terms. Over all 184 questions (golden, teacher,
      check) it matches only g03 and g06 in addition to the earlier admin questions; so g06 now
      forwards `admin` without a model call. Not re-run end to end (no model call is involved).
    - Judge numbers are as rough as before (the 9B judge; see the judge review).
- Timings on this CPU laptop: 9B answer 40–70 s, scope check 1–10 s, judge 10–45 s, plus ~10 s
  per call to reload the model (`keep_alive: 0`, see models.yaml); ~94 s per answered question
  in dense mode; the live check adds ~42 s. Expect ~110 min for 50 questions dense with the
  check and `--judge`, ~2 h 50 min for the
  teacher's 60, and ~40 s more per question with hybrid + rerank 20.

**Second subject `Rad_s_darovitom_djecom` (2026-11-17): retrieval measured, answers pending.**
- Material: 12 PPTX lectures + 2 long official PDF guidelines ("Okvir za poticanje…"), 586
  pages/slides → **419 chunks** (all ≤ 800 tokens). v1 = no context; v2 = `--context`
  (**5,924 s**, context 5,631 s ≈ 13.4 s per chunk, embedding 281 s). Sparse for both (349 s /
  427 s). 26 of 83 slides of the NTC lecture are pictures without text (a `vision` role job).
- Pipeline changes it needed (generic, no fitting to this course): a file without PDF
  bookmarks (all PPTX) takes its **file name as the section label** (`section_name`); empty
  bullet lines (`- -`) dropped in `clean_text`; a block over `max_tokens` with no paragraph
  breaks is split by sentence and, failing that (glossary pages without punctuation), into word
  windows (`_word_windows`). `scripts.evaluate` names the file next to the page in misses when a
  subject has several files (pages repeat across files). Separation checked: each subject's
  search returns only its own files.
- Question sets (all in `eval/sets/local/`, git-ignored; built from the teacher's files in
  `data/.questions/Rad_s_darovitom_djecom/`, student lists/results and files named after a
  student left out, rule 9):
  - `.teacher.yaml` (107): t01–t89 the teacher's 2025/26 study Q&A, x01–x13 questions from
    2014–16 written exams not already among them, i01–i05 Italian exam questions of another
    subject (docimologia) as `out_of_scope`. **28 are `not_in_materials`** (new type): course
    topics answered only in the handbook/readings, which are not in the material ("bez
    priručnika"); they must be forwarded. 74 answerable, difficulty `exam`;
    `expected_sources` marked by Claude from the slide text, not yet confirmed by the teacher.
  - `.students.yaml` (30): questions students wrote (`PITANJAstudenti.docx`), 28 answerable.
  - `.check.yaml`: auto-generated, 60 + 12 generic, from v2 in **1,280 s** (21 min).
- Retrieval, recall@5 / MRR, k=5:

  | mode                | teacher v1  | teacher v2  | check v1    | check v2    |
  |---------------------|-------------|-------------|-------------|-------------|
  | dense               | **0.89 / 0.75** | 0.84 / 0.71 | **0.83 / 0.64** | 0.78 / 0.67 |
  | hybrid              | 0.80 / 0.64 | 0.86 / 0.68 | 0.73 / 0.54 | 0.77 / 0.57 |
  | dense + rerank 20   | 0.88 / 0.74 | 0.85 / 0.72 |             |             |
  | hybrid + rerank 20  | 0.86 / 0.74 | 0.82 / 0.70 |             |             |

  Students set: recall@5 1.00 in all four dense/hybrid runs (MRR 0.90–1.00): students reuse the
  slides' wording, so it behaves like an easy set. Rerank runs took 2,294–3,400 s per 107
  questions (21–32 s per question) and were not run on the students or check sets.
  - **The Java course's gains are not confirmed.** `--context` hurts dense (0.89 → 0.84): the
    context sentence ends generically ("Tekst se direktno uklapa u temu predavanja o radu s
    darovitim djecom…"), and on a one-topic course that makes every chunk alike. Hybrid in rank
    fusion hurts (as on Java); as a rerank pool it does not help; rerank alone does not help.
    Best here: **v1 dense**, the default. `--context` stays opt-in; the sparse experiment
    is removed (see "Decisions made in step 1").
  - Teacher-set misses are mostly multi-part or broad questions and neighbouring slides
    (t25, t26, t39, t49, t55, t61, x01, x05, x10 on v1 dense). Check-set misses on v1 dense:
    10, none with a neighbour in the top 5; 7 of them are broad questions about the two Okvir
    PDFs, where many pages fit and one expected page is too strict. By style: symptom 4,
    casual/English/no_diacritics 2 each.
  - Abstention by score, again not separable: v1 dense, answerable top scores ≥ 0.52 but
    `not_in_materials` / Italian up to 0.71; on the check set the generic questions (max 0.54)
    sit just under the answerable minimum (0.56). Rerank gives a `not_in_materials` question
    (t33) 0.99.
- **Answer sample (2026-11-17)**, v1 dense k=5, `--judge`, all step-2 rules, 40 questions
  (seeded: 15 answerable + 10 `not_in_materials` + the 5 Italian from the teacher set; 6 slide +
  4 generic from the check set) in **5,441 s** (4,210 + 1,231; ~2.3 min per question), no errors.
  - **3 of the 10 `not_in_materials` labels were wrong**: the material does answer t05 (Okvir
    darovite pp. 67–69, cooperation with parents), t51 (25 % underachievers, "3 Uvod" slide 14)
    and x11 (NTC activity list, slide 71). The clone answered all three correctly from those
    pages. Relabelled `answerable`.
  - **The other 25 labels re-checked** (Claude, keyword search over every page's text, then
    reading the candidate pages): 2 more relabelled `answerable`: t42 (slide 9:24, rad na
    projektu) and t56 (Okvir darovite pp. 15–16, teškoće u socijalnim odnosima; partial). 23
    stay `not_in_materials`: Vygotsky, Piaget's stages, assimilation/accommodation, active
    learning, experiments, critical-thinking phases, Rajović's claims, computers, NTC exercise
    descriptions (the NTC slides list names only or are pictures), x06's feeling processes
    (slides 6:70–72 cover only the thinking factors). Borderline, noted for the teacher: t06
    (Okvir mentions the team and educators only in passing), t54 (the slides are images, so
    with a `vision` caption it may become answerable). Teacher set now **79 answerable, 23
    `not_in_materials`, 5 Italian**; v1 dense **0.90 / 0.75** (the retrieval table above used
    the first 74).
  - Answerable: **18/18 answered citing an expected slide** (15 exam + the 3 relabelled); judge
    grounded 0.80 (noisy as before).
  - Handbook-only (7 left): **5 forwarded** (`no_source`); **x03** answered from a creativity
    slide (6:63, "do 6-7. godine") and attributed it to Rajović, which the slide does not say;
    **x13** named a real NTC game from slide 70's list and added a description not on the slide
    ("cilj je raditi s asocijacijama"). Check verdict ODGOVARA=DA for both: the 9B check does not
    notice a claim that goes beyond a list slide.
  - Italian (other subject, docimologia): **3 forwarded** (`no_source`), **i01 and i05
    answered** — in Croatian, from this course's own pages on *vrednovanje* (assessment), which
    the Okvir PDFs cover: i01 lists Okvir assessment content as "functions of assessment"
    (check IZVAN=DA, ODGOVARA=NE, not mostly a refusal, so not forwarded); i05 answers
    "distortions of assessment" with slide 8:24's over/underestimated children. The scope check
    labelled all five GRADIVO: the topic overlaps this course, so this is not a language problem.
  - Check set: slide questions 5/6 answered (c051 forwarded `no_source`, retrieval had slide
    33); cites the expected slide 2/6, but the other answers cite slides on the same topic
    (c010 synapse slide 25 for 43; c053 Okvir p. 33 for 46), judge grounded 6/6. Generic
    abstention 4/4 forwarded (g04 `admin`, g09/g11 `out_of_scope`, g12 `no_source`).
  - Rule 2 on this course: 12 of 16 must-forward questions in the sample forwarded (the 4 generic,
    5 of 7 handbook-only, 3 of 5 Italian). The misses are answers stretched from a nearby page,
    the same failure as h22/h11 on the Java course, not invented topics.

**Step 3 (product), part 1: direct upload with ingestion as a background job (2026-11-17).**
- Teacher API (`app/courses.py`): `POST /api/courses` (new course = knowledge base),
  `GET /api/courses`, `POST /api/courses/{id}/versions` (multipart `files`: PDF/PPTX/DOCX),
  `GET /api/courses/{id}/versions[/{n}]`, `POST …/versions/{n}/retry` (failed only). A course
  is visible only to its teacher (404 otherwise).
- **Sign-in is a dev stub** (`app/auth.py`): with `AUTH_MODE=dev` (default) the teacher is
  named by the `X-CMP-Dev-Teacher` header and created on first use; any other mode refuses
  every request until SSO (Microsoft/Google) is added before the pilot.
- An upload stores the files (`core/storage.py`: `<UPLOAD_DIR>/<kb_id>/<sha256><suffix>`,
  default `data/uploads/`, one copy per content; the teacher's file name is never used as a
  path; type and size checked, `MAX_UPLOAD_MB` 200) and creates a new **draft** version
  (rule 5) with `pending_files` and `base_version_id` = the active version, else the newest
  processed one. A file with the same name and content as in the base is not processed again;
  a new name or new content is; nothing new → 409. A second upload while one is queued or
  processing → 409 (it would build on an older version and drop that upload's files).
- Migration `0006`: `kb_version.ingest_status` (queued | processing | ready | failed, separate
  from the approval `status`), `ingest_error`, `ingest_report` (jsonb), `pending_files`,
  `base_version_id`, `created_at`. Migration `0007` applies Procrastinate's schema (3.11); a
  procrastinate upgrade needs a new revision with its migration SQL.
- Jobs (`jobs/`): callers use only `jobs.enqueue_ingest` / `open_queue` / `close_queue`; the
  Procrastinate app and task live in `jobs/app.py` and `jobs/tasks.py`. Queue `ingest`, one
  job per course at a time (`lock`), never queued twice (`queueing_lock`). The job
  (`ingestion.pipeline.process_version`) copies unchanged documents with their chunks and
  embeddings from the base version (`copy_documents`, no re-parsing), processes the pending
  files with the same `ingest_document` that `scripts.ingest` uses, and stores the report;
  an error marks the version `failed` with the message, and a retry starts from a clean version.
  The API enqueues after the version is committed; if queueing fails the version is `failed`,
  not stuck.
- Worker: `uv run python -m scripts.worker` (sets the selector event loop on Windows, which
  psycopg's async mode needs). The API no longer imports Docling.
- Tests (`tests/test_upload.py`): file names never carry a path, one stored copy per content,
  bad files refused, which files an upload processes, carry-over copies documents/chunks/
  embeddings with parent links and leaves the base untouched, API ownership/status codes.
- **End-to-end check (2026-11-17)**, API (uvicorn) + worker on this laptop, two lectures of
  `Rad_s_darovitom_djecom`:
  - v1: 2 PPTX processed in 66 s (26 chunks, the same counts as the script ingest).
  - The same file again → 409.
  - v2: a new lecture + an unchanged one → 1 processed (6 s), 2 carried over; search on v2
    finds both the new and the carried-over lecture.
  - v3: a broken PDF → `failed` with "broken.pdf: the file could not be read (damaged,
    password-protected or not really this type)"; the full error, with server paths, goes only
    to the worker log. The retry re-queued it.
**Step 3, part 2: approval and activation (2026-11-17).**
- `core/approval.py`: draft → **approve** (only a processed draft; records `approved_at`,
  `approved_by`, and `document.approved_at`) → **activate** (only an approved version; the
  previous active version goes back to `approved` in the same transaction, so students never
  see none or two; `activated_at`). Re-activating an older approved version is the
  **rollback**. **Discard** deletes a draft's documents and chunks (never an approved or active
  version, which stay as history; refused while it or a version built on it is processing;
  stored files stay, they are shared by content). Each step locks the course row; document
  and version rows change in one transaction (`core.db.scope_transaction`). Migration `0008`.
- `active_version(kb_id)` is what students get (the chat endpoint will use only that).
- Uploads now build on the newest processed **draft newer than the active version** (so
  unreviewed uploads stack), else on the active version (also after a rollback), else the
  newest processed version.
- API: `POST …/versions/{n}/approve`, `POST …/versions/{n}/activate`,
  `DELETE …/versions/{n}` (draft only); versions show `approved_at` / `activated_at`, courses
  show `active_version`. 409 with a reason for a step the state does not allow.
- Tests (`tests/test_approval.py`, 5): approve → activate → rollback, only a processed draft is
  approvable, discard never touches approved versions or other versions, where uploads stack,
  the API flow and ownership. End-to-end with the server and worker: upload → refused
  activation before approval → approve → activate → second upload (carries the first file
  over) → activate (v1 back to approved, course shows active 2) → rollback → discarding the
  approved v2 refused.
- Not yet: the auto-generated check set after ingest, the
  teacher deleting a file from a version, solution tagging for uploads (the folder rule only
  applies to `scripts.ingest`), context sentences for uploads (off), containers for
  `api`/`worker`, the frontend.

**Step 3, part 3: student chat (2026-11-17).**
- `core/chat.py` `ask`: answers only from the course's **active** version (fixed when the
  question arrives, so an activation during a slow answer cannot mix versions), with the
  unchanged step-2 pipeline (`answer_question`, dense k=5, live check on); stores every
  exchange in `chat_message` (migration `0009`: question, what the student saw, forward
  reason, citations, trace, seconds, 👍/👎) and, when forwarded, queues the question for the
  teacher (`teacher_question`) in the same transaction. Single questions, no conversation
  memory yet: the pipeline and its checks were built and measured on single questions.
- API (`app/chat.py`): `GET /api/chat/{kb_id}` (course name, `live`, the AI notice),
  `POST /api/chat/{kb_id}/messages` (server-sent events), `GET /api/chat/{kb_id}/messages`
  (own history only), `POST /api/chat/messages/{id}/feedback` (own messages only). 409 while
  the course has no active version.
- **The stream does not send answer tokens**: it sends `notice` (rule 6, first event),
  `stage` events (scope, search, answer, check, via a new `on_stage` callback in
  `answer_question`), then one `answer` event once every check has passed (rule 1: the check
  runs before the student sees anything), or `error`. Keep-alive comments every 15 s. The
  answer runs in its own thread to the end even if the student closes the page, so a
  forwarded question still reaches the teacher. Model errors reach the student as a generic
  "try again later" (details only in the log); nothing is queued for them.
- Student identity is a dev stub like the teacher's: `X-CMP-Dev-Student` → opaque
  `student_ref` "dev:<value>" (rule 9: never a name); LTI will supply the platform's opaque
  user id. In dev any student can ask in any live course; LTI launch maps course access.
- Tests (`tests/test_chat.py`, 4, fake answer function): no answers before publishing, the
  event order (notice first, stages, then the answer from the active version), own history and
  feedback only, a forwarded question in the teacher queue with an opaque student ref, a model
  failure without details.
- **End-to-end with the real dev models** (API + worker on this laptop, two lectures of
  `Rad_s_darovitom_djecom` uploaded, approved, activated): before activation 409; "Koliko
  bodova treba za prolaz?" forwarded `admin` at once (0 s); "Zašto identifikacija darovite
  djece mora biti kontinuirani proces?" answered in **82 s** (scope 9 s, answer ~40 s, check
  ~33 s), citing "7 Identifikacija.pptx" slide 16, correct; "Tko je Lav Vigotski?" forwarded
  `low_score` after 12 s. Both forwarded questions were in the teacher queue, all three
  exchanges stored. (curl from Git Bash sent "š" in the wrong encoding and got a 422; a client
  sending UTF-8 works, as browsers do.)
- Not yet: the chat page (frontend), the teacher's view of the queue, LTI, retention
  rules for `chat_message`, conversation memory, rate limits per student.

**Step 3, part 4: the teacher's question queue (2026-11-17).**
- `core/queue.py`: `list_questions` (by status), `answer_forwarded` (answer or correct an
  earlier answer; optional sources `[{file_name, page}]`, checked against the files of the
  version the student asked and of the active version), `dismiss_forwarded` (open questions
  only). Migration `0010`: `teacher_question.answer_sources`, `answered_by`.
- API (`app/questions.py`): `GET /api/courses/{kb_id}/questions[?status=open|answered|
  dismissed|all]` (default open; each item has the forward reason as a code and in words),
  `POST …/questions/{id}/answer`, `POST …/questions/{id}/dismiss`; only the course's teacher
  (404 otherwise). **No student identity is returned** (rule 9).
- The student's chat history shows the teacher's answer under the forwarded question
  (`teacher_answer`, `teacher_answered_at`; the UI must show it as the teacher's, not the AI's).
  Nothing is pushed to the student yet (no LTI / notifications).
- A teacher answer with a source is a question + source pair: the start of the collected
  real-question set (CLAUDE.md "Evaluation strategy"). Turning those into an eval set is not
  built yet.
- Test (`tests/test_questions.py`): list in order with reasons and no student field, unknown
  source file refused, other teachers 404, answer with source, dismiss only when open, status
  filter, the student sees the teacher's answer and nothing for a dismissed question. Runs the
  real app and database (the model is not involved in the queue).
- Not yet: notifications (teacher: new questions; student: answered), grouping similar
  questions, turning answered questions into the collected eval set, the dashboard page.

**Step 3, part 5: web pages (2026-11-17).**
- `frontend/`: React 19.3 + Vite 8.3 + TypeScript 7.0 (type-check only; Vite builds), Vitest 5.1
  + Testing Library + jsdom; exact versions, each at least two weeks old when added. Croatian
  UI, light and dark, readable at phone width (checked at 375 px: no horizontal scroll). No
  router or UI library: hash routes (`#/teacher`, `#/teacher/<id>`, `#/chat/<id>`) work from a
  static server and inside LMS iframes.
- **Node runs only in Docker** (decided 2026-11-17; nothing installed on the host): the
  `frontend` service in `infra/docker-compose.dev.yml` (profile `frontend`, image built from
  `frontend/Dockerfile.dev` = `node:24.11.1-bookworm-slim`, user `node`, `node_modules` in a
  named volume, polling file watcher for Windows mounts) runs `npm ci && npm run dev` on
  127.0.0.1:5173 and proxies `/api` to the backend on the host (`CMP_API_URL`,
  host.docker.internal). Docker Desktop forwards that to the host's loopback, so the API stays
  bound to 127.0.0.1 (checked); on Linux Docker Engine the API would have to listen on the
  bridge address instead.
- Pages: start page with the **dev sign-in** (a name stored in localStorage, sent as the
  X-CMP-Dev-* header; says plainly that it is not real sign-in); teacher course list + new
  course; course page with upload (pick or drag files), versions (status in words, summary,
  review of files and sections, approve / publish with confirmation / discard / retry; polls
  every 3 s while a version is processing; upload disabled meanwhile), the student link once
  published, and the question queue (filter, answer with optional file + slide from the active
  version, edit, dismiss; no student identity). Student chat: the AI notice pinned at the top
  (rule 6, also before sign-in and when the course is not published), history, progress text
  per stage, the answer with [n] markers as buttons that scroll to the numbered source list,
  👍/👎, the teacher's answer in its own bubble labelled "Odgovor nastavnika".
- The chat reads the server-sent events from a `fetch` POST (`src/sse.ts`; `EventSource` cannot
  POST). An error boundary replaces a crashed page with a message and a reload button.
- Tests (13): SSE parsing (split chunks, CRLF, keep-alive), citation markers (unknown numbers
  dropped), routes, version buttons per state, the chat (notice always shown, the dev header,
  the answer with its source, the teacher's answer labelled, an unpublished course).
  `src/test-setup.ts` makes `scrollIntoView` return a Promise like current browsers: the first
  browser run crashed the chat on exactly that (an effect returned its result), which jsdom
  had hidden; the setup now fails those tests on the old code.
- **Checked in the browser** (API + worker + frontend container, dev models): teacher signs
  in, creates a course; an upload (sent through the API: the browser tools cannot pick files)
  shows as processing and the page refreshes itself until ready; review shows 28 slides / 18
  chunks / 14 s; approve; publish (green box with the student link). Student: the AI notice,
  "Zašto identifikacija … kontinuirani proces?" → stage text → the answer citing
  "7 Identifikacija.pptx, str. 16" after ~80 s; "Tko je Lav Vigotski?" → forwarded, "Nastavnik
  još nije odgovorio". Teacher answers it in the queue with source slide 4; the student's chat
  then shows "Odgovor nastavnika".
- `npm audit`: 6 high, all one advisory (picomatch ReDoS, every version) in Vite/Vitest's file
  matching; dev tooling only, nothing reaches users; the offered fix downgrades Vitest, not
  applied. Re-check when Vite/Vitest update.
- Not yet: the production image (static build behind Caddy) and HTTPS proxy, real sign-in,
  LTI launch into the chat, notifications, the teacher's settings page.

**Step 3, part 6: the check set after every upload (2026-11-17).**
- Check questions now live in the database (`check_question`, migration `0011`), tied to the
  course and to a document's **content** (file name + hash) and slide: valid for every version
  holding the same file, so carried-over documents keep their questions and a changed file gets
  new ones. Teacher deletions are soft (`deleted_at`) and stick: a document with any question,
  deleted or not, gets no new ones.
- `core/checks.py` `run_checks`: for documents of the version without questions, writes about
  `CHECK_QUESTIONS` (setting, default 60) per course, each document's share by its eligible
  slides (at least one), with the step-2 generator (`core.checkset`: spread over the document,
  styles in turn, `PRESKOČI` → next slide); then searches every valid question in the version
  (dense, k=5) and stores `check_report` (questions, found in top 5, recall@5, MRR, written,
  up to 20 misses, seconds, model ids) with `check_status` (queued | running | done | failed).
  Search only: the answer-level check (grounding, the 12 generic forward questions) needs the
  pilot GPU. The YAML generator (`scripts.checkset`) stays for evaluation runs.
- Job: `ingest_version` queues `check_version` after a successful ingest (through
  `jobs.enqueue_checks`; same per-course lock, so two checks never write questions for one
  new document); a failed check never fails the ingest. Queueing from inside a running job
  works (checked live).
- API: versions carry `check_status` and `check`; `GET /api/courses/{kb_id}/checks`,
  `DELETE /api/courses/{kb_id}/checks/{id}`. Pages: each processed version shows "Provjera:
  asistent pronalazi pravi slajd za X od Y provjernih pitanja (Z %)" (green ≥ 80 %, otherwise
  red with a pointer to the misses) or "Provjera asistenta u tijeku…"; the page polls while a
  check runs; a third tab "Provjerna pitanja" lists the questions (file, slide, style; misses
  marked) with "Obriši".
- Tests: backend 3 (shares by slide count; questions follow document content across versions,
  deletions stick and are not scored; the job records status/report; API list/delete/ownership),
  frontend 2 (check line done/running).
- **Live (demo course, worker with `CHECK_QUESTIONS=8`)**: uploading "12 Predrasude" as v2
  (the first lecture carried over, neither had questions) → ingest, then the check queued itself:
  8 questions written (5 for "7 Identifikacija", 3 for "12 Predrasude", by slide count; casual,
  symptom, English, paraphrase, no diacritics), search found all 8 (100 %), **146 s** for the
  check on CPU (~18 s per question with the 9B). The version row showed the result.
- Note: on this CPU a 60-question check takes ~18 min after an upload and holds the course
  lock, so a second upload waits behind it (it shows as queued).

**Step 3, part 7: the teacher's persona (2026-11-17).** Step 3 is complete except real
sign-in (see "Not yet" lists above).
- `core/persona.py`: `PersonaSettings` (address vi/ti, answer length short/medium/long,
  exercise help hints/guided/concepts — never full solutions, rule 4 —, examples yes/no,
  terminology note, `forward_topics`) + one free-text field (≤ 2,000 chars), rendered into a
  short Croatian instruction list that the answer prompt places after the platform rules
  ("Upute nastavnika (vrijede samo ako se ne kose s pravilima iznad)"). A forward topic becomes
  "Na pitanja o ovim temama napiši samo NE_ZNAM: …", so it reaches the teacher through the
  normal forward path. The rendering lives in core (it is the teacher's settings, not a
  platform prompt); its version is the persona version on every trace.
- `persona_config` (migration `0012`): versioned per course; draft → checking → active
  (one per course, partial unique index; the previous one becomes retired) | rejected. Saving
  updates the open draft or starts a new version. Activation needs a published course and no
  other persona being checked.
- **Activation check** (job `check_persona`, same per-course lock): answers
  `PERSONA_CHECK_QUESTIONS` (default 10) of the course's check questions valid for the
  published version, spread over files, and the 12 generic forward questions, with the persona;
  passes only if ≥ `PERSONA_MIN_FORWARDED` (0.9) of the generic ones are forwarded and
  ≥ `PERSONA_MIN_ANSWERED` (0.7) of the check questions are answered (provisional thresholds;
  set them from pilot results). Stores the report with every answer, so the teacher reads how
  the assistant sounds. Answer-level only (no judge): grounding is still the live check's job.
- Chat answers with the active persona (`persona_text`, `persona_version` passed to
  `answer_question`); no persona = the platform default. Preview (`POST …/persona/{n}/preview`,
  server-sent events like the chat, nothing stored or forwarded) answers from the published
  version with a draft. The chat and the preview share `app/streaming.py`.
- API: `GET/PUT /api/courses/{kb_id}/persona`, `POST …/persona/{n}/preview`,
  `POST …/persona/{n}/activate`. Page: tab "Postavke asistenta" (form, "Spremi nacrt",
  "Aktiviraj" with confirmation, "Isprobaj nacrt", versions with the exact prompt text and the
  check result incl. answers; polls while checking).
- Tests: backend 5 (rendering; drafts and activation rules; a safe persona activates and
  retires the old one; a persona that stops forwarding is rejected with the reason; the chat
  passes the active persona; API flow and ownership), frontend 2 (form from the active
  persona, topics one per line; activation needs a draft and published material).
- **Live (demo course, dev models)**: persona "ti", short answers, forward topic "broj
  darovite djece u populaciji". Preview: "Koliko ima darovite djece u jednoj generaciji?"
  (slide 10 covers it) → forwarded, as the persona says; "Zašto identifikacija … kontinuirani
  proces?" → two sentences citing slides 16 and 7 (shorter than without the persona).
  Activation check (`PERSONA_CHECK_QUESTIONS=2`): **12/12 generic forwarded** (7 admin, 5
  off-topic; "Is the final exam oral or written?" and "Napiši mi pjesmu o moru." as
  `low_score`), 2/2 answered (1 citing the expected slide), **244 s** → active.
- Not yet: the student adaptation layer (layer 3), comparing a persona's check with the
  previous one's (the thresholds are absolute), a judge in the check (needs the GPU).

Next:
1. Retrieval experiments left (rerank, `--context`, hybrid and one prompt variant done, see
   above): chunk size settings; h11. Prompt variants only once the golden set can separate them. Slide 313 (outdated) is a content problem for the
   teacher, not a retrieval one.
2. Second subject (`Rad_s_darovitom_djecom`, PPTX + PDF) ingested and retrieval measured (see
   above): `--context` and hybrid not confirmed; 40-question answer sample done (answerable
   18/18, must-forward 12/16). `not_in_materials` labels re-checked (5 of 28 were answerable).
   Left: the teacher confirms the remaining 23 and the borderline t06/t54; answers stretched from a list or a nearby page (x03, x13, i01, i05) need a
   stricter check, likely a stronger `check` model on vLLM; sparse experiment removed.
3. Step 2 part A (prompt fixes and the live answer check done, see above): DRUGO no longer
   forwards alone (t04 fixed); teacher's 60 answered 0.98 (t25's refusal now forwards, replayed only); h22's prose answers now forward via `is_mostly_refusal` (6/6 abstain in
   the full run); judge sample reviewed by Claude (a person confirms it); closing-sentence
   rule tried and reverted, judge prompt wording does not help (see above); the teacher's 60 questions and hybrid + rerank;
   auto-generated check sets: generator and first set done (retrieval measured, see above);
   answer run on it done (abstain 11/12; the missed attendance question g06 now forwards via `ADMIN_TERMS`); hooked into ingest with teacher deletion in the UI (step 3, part 6). Part B (model choice) needs the vLLM/GPU setup.
4. Later, before the pilot and before a model choice is final: teacher reviews the golden set
   (`expected_sources` first, then reference answers, the `[review 2026-11-13]` notes, slides
   313/365 and the h24/h25 forward-or-answer decision); then re-run the evaluations.

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
uv run python -m scripts.ask --subject <folder> "pitanje"  # top-5 hits; --version N, --rerank 20
#   --answer: the clone's answer with citations (or forward; queued unless --no-queue)
#   needs the chat models: ... exec ollama ollama pull qwen3.5:9b  (and qwen3.5:4b)
uv run python -m scripts.evaluate_answers --subject <folder> --version 3   # answers on the golden set
#   --judge (grounding/on-topic), --ids e01,h20, --difficulty, --limit, --set, --rerank,
#   --no-check (skip the live answer check)
#   ~130 s per question on CPU (dense, with the check and --judge; 109 min for 50),
#   ~20 s more with --rerank 20
uv run python -m scripts.checkset --subject <folder> --version 3 -n 60   # auto-generated check set
#   -> eval/sets/local/<folder>.check.yaml (git-ignored); --seed, --force (replace an edited set)
#   ~18 s per question on CPU; run it with --set like any other set
uv run python -m scripts.evaluate --subject <folder>       # recall@5 + MRR on eval/sets/<folder>.yaml
#   per difficulty (easy/hard); abstain questions report their top score;
#   --version N, -k, --rerank 20,
#   --set PATH (another set for the subject, e.g. eval/sets/local/<subject>.teacher.yaml)
uv run pytest                            # tests (DB tests skip if Postgres is down)
uv run ruff check . && uv run ruff format .  # lint + format
uv run fastapi dev app/main.py           # API with reload: /health, /api/courses (teacher API)
uv run python -m scripts.worker          # background worker: processes uploads (needs --group ingest)
#   dev sign-in: send the header X-CMP-Dev-Teacher: <name>; e.g.
#   curl -X POST 127.0.0.1:8000/api/courses -H "X-CMP-Dev-Teacher: ana" \
#        -H "Content-Type: application/json" -d '{"subject_name": "Predmet"}'
#   curl -X POST 127.0.0.1:8000/api/courses/<id>/versions -H "X-CMP-Dev-Teacher: ana" -F files=@slides.pptx
#   then .../versions/1/approve and .../versions/1/activate; a student asks (server-sent events):
#   curl -N -X POST 127.0.0.1:8000/api/chat/<id>/messages -H "X-CMP-Dev-Student: s1" \
#        -H "Content-Type: application/json; charset=utf-8" -d '{"question": "..."}'
```

Web pages (Node only in Docker; run from the repo root, with the API and worker above running):
```bash
docker compose -f infra/docker-compose.dev.yml --env-file .env --profile frontend up -d frontend
#   -> http://127.0.0.1:5173 (live reload; /api proxied to the API on the host)
docker compose -f infra/docker-compose.dev.yml --env-file .env --profile frontend run --rm frontend npm test
docker compose -f infra/docker-compose.dev.yml --env-file .env --profile frontend run --rm frontend npm run build
#   npm run typecheck; npm install --save-exact <pkg>@<version> to add a package
```

Planned (later build steps):
```bash
docker compose -f infra/docker-compose.dev.yml up -d --build      # + api, worker, langfuse, moodle
docker compose -f infra/docker-compose.prod.yml up -d             # vLLM on GPU, proxy with HTTPS
```

Database roles: migrations connect as the owner (`DATABASE_OWNER_URL`); everything else connects
as the app role (`DATABASE_URL`), which is not owner or superuser, so row-level security applies.
KB content is read/written only inside `core.db.kb_scope(kb_version_id)`.

## Open questions
- Which open model wins the `answer` role (Qwen 3.5 vs Mistral Small 4 vs Gemma 4) — decided by golden-set results on Croatian material.
- GPU hosting for the pilot: own hardware vs rented EU cloud GPU (one 24 GB GPU fits the ~30B class). Measure parallel conversations per GPU at acceptable latency.
- `judge` role: an open model may be too weak to grade reliably. The internal benchmark holds no personal data, so a stronger (possibly commercial) judge for it only is an option — decide explicitly. Local checks (collected real questions contain student text) must use a judge that runs inside the installation.
- Whether to ship an optional **smoke test** with CMP: a small course written by us (owned by the project, so nothing to approve or revoke) with ~30 questions, to check that an installation or a swapped model works. Not decided; revisit later.
- Whether the pilot institution accepts open models of non-EU origin (Qwen) when self-hosted in the EU.
- Pilot institution, subject(s) and teacher(s); SRCE contact for Merlin.
- Business model: who pays (institution licence is the working assumption).
