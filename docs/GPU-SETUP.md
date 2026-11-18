# Setting up CMP on a PC with an NVIDIA GPU, and running the benchmark

Written so you can follow it without help on that PC. Two goals:

1. **CMP running on the GPU** (vLLM serves the chat models), checked with the smoke test.
2. **The internal benchmark**: our question sets on our courses, to compare models
   (CLAUDE.md, "Evaluation strategy" and rule 10).

Commands are for Windows PowerShell; Linux/macOS equivalents use `./setup.sh` with
`--double-dash` switches (`-Check` → `--check`, `-Benchmark` → `--benchmark`).

---

## 0. Before you leave the old computer

The course material and the question sets made from it are **not in git** on purpose
(they are the teachers' material, rule: never in the repository). Copy them by hand, e.g. to a
USB stick, and treat the stick as private:

| copy this folder | what it is | needed for |
|---|---|---|
| `data\Suvremene_tehnike_programiranja\` | the Java course PDF | benchmark |
| `data\Rad_s_darovitom_djecom\` | the gifted-children course (PPTX + PDF) | benchmark |
| `data\.cache\` | the parse cache (saves ~20 min of parsing per course) | optional |
| `eval\sets\local\` | teacher / students / check question sets | benchmark |
| `data\.questions\` | the teachers' original question files | only to rebuild sets |

Do **not** copy `.env` (the setup makes a new one with new passwords) or `data\uploads\`,
`data\keys\`.

The golden set `eval\sets\Suvremene_tehnike_programiranja.yaml` is in git and comes with the clone.

## 1. Check the GPU

```powershell
nvidia-smi
```

It must print the card and its memory (e.g. `24576MiB` = 24 GB). If the command is not found or
fails, install the latest NVIDIA driver from nvidia.com and restart. On Windows the driver is
all Docker needs; on Linux also install the **NVIDIA Container Toolkit**.

**Write down the memory.** It decides which model fits:

| GPU memory | what to put in `config\models.vllm.yaml` |
|---|---|
| 24 GB or more | nothing, the default `Qwen/Qwen3.5-9B` fits (~20 GB) |
| 16 GB | `Qwen/Qwen3.5-4B` (or a quantized 9B, e.g. an AWQ variant) |
| 12 GB or less | `Qwen/Qwen3.5-4B`, and lower `--max-model-len` (step 3) if it still runs out |
| 48 GB+ / several GPUs | room for the larger candidates (Mistral Small 4, Gemma 4) |

## 2. Get the project

```powershell
git clone https://github.com/rkarlovic/clone-my-professor.git
cd clone-my-professor
```

If the repository is private, Git opens a browser window to sign in to GitHub. No Git yet?
Step 4's `-Install` installs it, but you need the folder first: download the repository as a
ZIP from GitHub, unpack it, and `cd` into it.

Now put the copied folders from step 0 into `data\` and `eval\sets\local\` of this folder.

## 3. (Only if the GPU has less than ~24 GB) choose a smaller model

Open `config\models.vllm.yaml` in Notepad and change:

- `serve_model: Qwen/Qwen3.5-9B` → e.g. `serve_model: Qwen/Qwen3.5-4B`
- every line `model: hosted_vllm/Qwen/Qwen3.5-9B` → `model: hosted_vllm/Qwen/Qwen3.5-4B`
  (the part after `hosted_vllm/` must be exactly the `serve_model` name)
- `version:` → something new, e.g. `vllm-2026-11-20.1` (reports are labelled with it)

Still out of memory later? In `infra\docker-compose.gpu.yml`, lower `"16384"` after
`--max-model-len` to `"8192"`.

## 4. Install and start, with the smoke test

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Install -Check
```

What happens:

- `-Install` installs Git and Docker Desktop if missing (winget, asks for admin rights).
  Docker Desktop may require a **restart of Windows**; afterwards run the same command again.
  It is always safe to run again.
- The script finds the GPU itself and says `NVIDIA GPU found: vLLM included`.
- First start downloads the model (~18 GB for the 9B) and Ollama's embedding model. The vLLM
  container needs several minutes after the download before it answers.
- `-Check` then runs the smoke test (our own 14-slide course, 10 questions). It must end with
  `PASSED`. On a GPU it should take a minute or two (it took 10 minutes on the CPU laptop).

Then open **http://localhost**, sign in with any name (test sign-in, this computer only).

## 5. The benchmark

```powershell
.\setup.ps1 -Benchmark
```

For every course folder in `data\` that has a question set, it:

1. ingests the course once (a new version in the database; later runs reuse it),
2. measures search on each set (recall@5, MRR),
3. answers every question with the full pipeline (scope check, search, answer, live check) and
   has the `judge` role grade the answers.

Results: **`eval\reports\benchmark-<models version>-<time>.md`** (the summary table) and the
per-question reports under `eval\reports\<course>\`. These contain the teachers' material, so
they stay on this PC (git-ignored); send the `.md` summary, which has only numbers.

Useful options (`-BenchmarkArgs "…"`):

| option | effect |
|---|---|
| `--retrieval-only` | search only, takes seconds; a quick first test |
| `--subjects Rad_s_darovitom_djecom` | one course |
| `--sets golden,teacher` | only these sets (golden, teacher, students, check) |
| `--limit 10` | first 10 questions of each set, for a quick answer test |
| `--no-judge` | skip grading |
| `--reingest` | make a new version (after changing chunking or the embedding model) |
| `--context` | ingest with the `context` sentence per chunk (fast on a GPU) |

Suggested order on a new PC:

```powershell
.\setup.ps1 -Benchmark -BenchmarkArgs "--retrieval-only"
.\setup.ps1 -Benchmark -BenchmarkArgs "--limit 5"
.\setup.ps1 -Benchmark
```

The first one ingests both courses (parsing is skipped when `data\.cache\` was copied) and should
reproduce the laptop's search numbers: Java golden set dense recall@5 about 1.00 easy / 0.84 hard
(v2, without context), gifted-children teacher set about 0.90. If they are far off, something
is wrong with the setup, not the model.

### Comparing models

1. Change the model in `config\models.vllm.yaml` as in step 3 (and the new `version:`).
2. `.\setup.ps1` (restarts vLLM with the new model; downloads it once).
3. `.\setup.ps1 -Benchmark` (the courses are not ingested again: the embedding model did not change).
4. Compare the two `benchmark-*.md` files. Decide on the answer columns of the **teacher** and
   **golden** sets first; the judge column is rough.

Thinking (reasoning) is off for every role (`reasoning_effort: none`). Trying it on is one of the
GPU experiments: remove that line for the `answer` role, change `version:`, rerun.

## 6. If something goes wrong

```powershell
.\setup.ps1 -Status
docker compose -f infra/docker-compose.yml -f infra/docker-compose.gpu.yml --env-file .env logs --tail 100 vllm
docker compose -f infra/docker-compose.yml -f infra/docker-compose.gpu.yml --env-file .env logs --tail 100 api worker
.\setup.ps1 -Stop
```

| symptom | likely cause / fix |
|---|---|
| vLLM log: `CUDA out of memory` | model too big: step 3 (smaller model or shorter `--max-model-len`) |
| vLLM log: `401` / `gated repo` | the model needs a Hugging Face account: create a token at huggingface.co, put `HF_TOKEN=…` in `.env`, run `.\setup.ps1` |
| setup says `no NVIDIA GPU` | `nvidia-smi` fails: driver (step 1); Docker Desktop must use the WSL 2 engine (Settings → General) |
| smoke test `FAIL` on answers | look at vLLM's log; a wrong model name in `models.vllm.yaml` (must match `serve_model`) |
| benchmark: `no subject found` | the course folders are not in `data\` or the sets not in `eval\sets\` (step 0) |
| `docker` not found after install | sign out and in again (or restart) so the PATH updates |

When something fails, keep the log lines (`... logs ... > log.txt`) for later; they contain no
passwords, but the api/worker logs can contain question text, so do not post them publicly.
