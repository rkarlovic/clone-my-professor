# Setting up CMP on a PC with an NVIDIA GPU, and running the benchmark

Written so you can follow it without help on that PC. Two goals:

1. **CMP running on the GPU** (vLLM serves the chat models), checked with the smoke test.
2. **The internal benchmark**: our question sets on our courses, to compare models
   (CLAUDE.md, "Evaluation strategy" and rule 10).

Commands are for Windows PowerShell; Linux uses `./setup.sh` with `--double-dash` switches
(`-Check` → `--check`, `-Benchmark` → `--benchmark`). If Windows answers "running scripts is
disabled on this system", see the next section.

## "running scripts is disabled on this system"

Windows blocks PowerShell scripts on many PCs (company and school PCs especially). Then
`.\setup.ps1` stops at once with:

```text
.\setup.ps1 : File C:\...\setup.ps1 cannot be loaded because running scripts is disabled on this
system. For more information, see about_Execution_Policies at https:/go.microsoft.com/fwlink/?LinkID=135170.
    + FullyQualifiedErrorId : UnauthorizedAccess
```

**Fix for one run:** put `powershell -ExecutionPolicy Bypass -File` in front of the command. The
switches after `.\setup.ps1` stay exactly the same:

| instead of | type |
|---|---|
| `.\setup.ps1` | `powershell -ExecutionPolicy Bypass -File .\setup.ps1` |
| `.\setup.ps1 -Install -Check` | `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Install -Check` |
| `.\setup.ps1 -Benchmark -BenchmarkArgs "--retrieval-only"` | `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Benchmark -BenchmarkArgs "--retrieval-only"` |
| `.\setup.ps1 -Stop` | `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Stop` |

This changes nothing on the PC: it allows only this one run of this one script.

**Fix for good (optional):** allow scripts for your own Windows user, then unblock the project's
scripts. Run both in PowerShell, the second one inside the project folder:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
Get-ChildItem -Recurse -Filter *.ps1 | Unblock-File
```

- The first allows scripts made on this PC and signed downloaded ones, for your user only (no
  admin rights needed; it changes a Windows security setting, so it is your choice).
- The second is needed because the project came as a ZIP from the internet: Windows marks such
  files as "downloaded" and still blocks them. Run it again after downloading a new ZIP; running
  it again any time does no harm.

After that the short form `.\setup.ps1 …` works. To undo the first command later:
`Set-ExecutionPolicy -Scope CurrentUser Undefined`. If your organisation sets the policy centrally,
the first command fails with a message saying so; then use the one-run fix above.

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

It must print the card and its memory (e.g. `8188MiB` = 8 GB). If the command is not found or
fails, install the latest NVIDIA driver for your card from nvidia.com and restart. On Windows the
driver is all Docker needs; on Linux also install the **NVIDIA Container Toolkit**.

**The setup picks the model from the card's memory by itself** (one file per size in `config\`):

| GPU memory | models file | model vLLM downloads on its first start |
|---|---|---|
| 24 GB or more | `models.vllm.yaml` | Qwen 3.5 9B, full precision (~20 GB) |
| 14–23 GB | `models.vllm-16gb.yaml` | Qwen 3.5 9B with 4-bit weights (Red Hat build, ~11 GB) |
| 7–13 GB (e.g. RTX A1000 8 GB) | `models.vllm-8gb.yaml` | Qwen 3.5 4B with 4-bit weights (Red Hat build, ~5.5 GB) |
| less, or no NVIDIA card | `models.yaml` | everything on the processor (slow) |

With less than 24 GB the search model (embeddings) runs on the processor, so vLLM has the card to
itself. The final model choice needs 24 GB or more (the other candidates, Mistral Small 4 and
Gemma 4, are bigger); a smaller card is for checking the GPU setup and for fast test runs.

**If the PC has a processor with built-in graphics, plug the monitor into the motherboard**,
not into the NVIDIA card: then Windows uses no memory on the card.

## 2. Get the project

```powershell
git clone https://github.com/rkarlovic/clone-my-professor.git
cd clone-my-professor
```

If the repository is private, Git opens a browser window to sign in to GitHub. No Git yet?
Download the repository as a ZIP from GitHub (green "Code" button → "Download ZIP"), unpack it,
and `cd` into it; step 3's `-Install` installs Git for later.

Now put the copied folders from step 0 into `data\` and `eval\sets\local\` of this folder.

## 3. Install and start, with the smoke test

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Install -Check
```

What happens:

- `-Install` installs Git and Docker Desktop if missing (winget, asks for admin rights).
  Docker Desktop may require a **restart of Windows**; afterwards run the same command again.
  It is always safe to run again.
- It prints the card and the chosen file, e.g.
  `NVIDIA GPU with 8.0 GB of memory: config\models.vllm-8gb.yaml`.
- **Memory for Docker.** Docker Desktop gets only half of the PC's memory by default (8 GB on a
  16 GB PC); with the GPU models CMP needs about 11 GB. If Docker has too little, the script says
  so and shows where that setting lives: the file **`.wslconfig` in your Windows user folder**,
  i.e. `C:\Users\<your user name>\.wslconfig` (the script prints the exact path; in Explorer,
  type `%USERPROFILE%` in the address bar to open that folder). If the file does not exist yet, the
  script offers to create it (`memory=12GB` on a 16 GB PC) and restart Docker: answer `Y`. If it
  exists, it tells you which two lines to put in it; then run `wsl --shutdown` and the setup
  again. Close other big programs while the benchmark runs.
- The first start downloads the model (~5.5 GB for the 8 GB card, ~18 GB for the 24 GB one) and
  Ollama's search model. vLLM needs a few minutes after the download before it answers.
- `-Check` then runs the smoke test (our own 14-slide course, 10 questions). It must end with
  `PASSED`. On a GPU it should take a few minutes at most (10 minutes on the CPU laptop).

Then open **http://localhost**, sign in with any name (test sign-in, this computer only).

## 4. The benchmark

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
they stay on this PC (git-ignored); the `.md` summary has only numbers and can be shared.

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
reproduce the laptop's search numbers, because search does not use the GPU model: Java golden
set about 1.00 easy / 0.84 hard, gifted-children teacher set 0.90 / 0.75, its check set
0.83 / 0.64, students 1.00. If they are far off, something is wrong with the setup, not the model.

### Comparing models

1. Open the models file the setup chose (step 3 printed it) in Notepad and change
   `serve_model:`, every `model: hosted_vllm/…` line to the same name (the part after
   `hosted_vllm/` must be exactly the `serve_model` name), and `version:` to something new, e.g.
   `vllm8-2026-11-25.1` (reports are labelled with it).
2. `.\setup.ps1` (restarts vLLM with the new model; downloads it once).
3. `.\setup.ps1 -Benchmark` (the courses are not ingested again: the embedding model did not change).
4. Compare the two `benchmark-*.md` files. Decide on the answer columns of the **teacher** and
   **golden** sets first; the judge column is rough.

`serve_args:` in the same file holds vLLM's start flags: `--max-model-len` (longest prompt) and
`--gpu-memory-utilization` (share of the card's memory). Thinking (reasoning) is off for every
role (`reasoning_effort: none`); trying it on is one of the GPU experiments: remove that line for
the `answer` role, change `version:`, rerun.

## 5. If something goes wrong

```powershell
.\setup.ps1 -Status
docker compose -f infra/docker-compose.yml -f infra/docker-compose.gpu.yml --env-file .env logs --tail 100 vllm
docker compose -f infra/docker-compose.yml -f infra/docker-compose.gpu.yml --env-file .env logs --tail 100 api worker
.\setup.ps1 -Stop
```

| symptom | likely cause / fix |
|---|---|
| vLLM log: `CUDA out of memory` or "not enough free memory" | something else uses the card (a monitor plugged into it, another program): see step 1; or lower `--gpu-memory-utilization` / `--max-model-len` in `serve_args:` of the chosen models file, then `.\setup.ps1` |
| vLLM log: `max_num_seqs (…) exceeds available Mamba cache blocks (N)` | too many parallel conversations for the card's memory: lower `--max-num-seqs` in `serve_args:` of the chosen models file below N, then `.\setup.ps1` |
| vLLM container stops, `docker` reports exit code 137 | Docker ran out of memory: the `.wslconfig` step in step 3 |
| vLLM log: `401` / `gated repo` | the model needs a Hugging Face account: create a token at huggingface.co, put `HF_TOKEN=…` in `.env`, run `.\setup.ps1` |
| vLLM log: an error about `--language-model-only` | the chosen model has no vision part: remove that flag from `serve_args:` |
| setup does not say `NVIDIA GPU with …` | `nvidia-smi` fails: driver (step 1); Docker Desktop must use the WSL 2 engine (Settings → General) |
| `running scripts is disabled on this system` | put `powershell -ExecutionPolicy Bypass -File` in front: `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Check` (see "running scripts is disabled" at the top) |
| smoke test `FAIL` on answers | look at vLLM's log; a wrong model name in the models file (must match `serve_model`) |
| benchmark: `no subject found` | the course folders are not in `data\` or the sets not in `eval\sets\` (step 0) |
| `docker` not found after install | sign out and in again (or restart) so the PATH updates |

When something fails, keep the log lines (`... logs ... > log.txt`) for later; they contain no
passwords, but the api/worker logs can contain question text, so do not post them publicly.
