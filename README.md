# Clone My Professor (CMP)

A platform where a teacher builds an AI "clone" that helps their students learn around the clock,
using **only the teacher's approved materials** and following the teacher's teaching instructions.
Students reach it from their learning platform (Moodle/Merlin first, later Teams and Google
Classroom) or from CMP's own web app. Content and users are primarily **Croatian**.

> **Status (2026-11-18):** prototype, ready to try. Teachers upload or sync material from
> Moodle (Teams and Google Classroom too), review and publish it; students ask in a chat (also
> inside their learning platform) and get answers with citations, or their question goes to
> the teacher. One script installs and starts
> everything: see [Install and run](#install-and-run-one-script).
> See [Progress so far](#progress-so-far).

## Contents

- [Progress so far](#progress-so-far)
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

## Progress so far

*Plain-language summary, kept up to date. Technical detail and every measurement:
[CLAUDE.md](CLAUDE.md), section "Current status".*

**Where we are:** step 4 of 6. Step 1 (finding the right slide) and step 2 (writing answers)
work; their remaining fixes wait for the pilot server and the teacher's review. Step 3, the
product (upload, approval, chat, teacher's question queue, web pages, automatic test
questions, teacher's settings), is done. Step 4 connects Moodle: opening CMP from Moodle,
syncing its files and removing what disappears from it all work; only Merlin (which needs
SRCE's approval) is left. A setup script installs the whole thing on another computer.
Everything runs on a laptop without a
graphics card, so one answer takes about two minutes; on the pilot server it will take seconds.

**Test course:** *Suvremene tehnike programiranja* (Java, one 945-page PDF of lecture slides),
used with permission and never stored in this repository. Two question sets:

- 50 questions written to test the clone: 25 worded like the slides, 19 worded like a student
  would ask (casual, paraphrased, one in English), and 6 the clone must **not** answer
  (other subjects, grades and deadlines). Written by Claude from the slides; the teacher reviews them
  later.
- 60 real oral-exam practice questions written by the teacher.
- New: 60 questions the system writes by itself from slides spread over the whole course, worded
  like a student (casual, describing a problem, in English, without Croatian letters), plus 12
  standard questions it must forward. Every course will get such a set automatically, so
  teachers never have to write test questions. Search finds the right slide for 82–92 % of them
  (depending on the search mode), about the same as for the hand-written student-like
  questions, which suggests they are realistic; about 5 of the 60 were poorly worded.
  When it answered them, the clone gave 57 of 60 an answer; most of those cite the right
  slide or the one next to it. It forwarded 11 of the 12 questions it must not answer. The one it
  missed: "do I have to come to the exercises?" got the answer "yes, attendance is required",
  guessed from the timetable slide. Attendance questions now go straight to the teacher, like
  questions about grades and deadlines.

**Second course (2026-11-17):** *Rad s darovitom djecom* (working with gifted children; 12
PowerPoint lectures and two long official PDF guidelines), a very different kind of material, to
check that nothing is tuned to the Java course. Also used with permission and never stored here.
Question sets: the teacher's 89 study questions plus 13 more from old exams, 30 questions
students wrote, 5 exam questions from another subject (in Italian) that the clone must not
answer, and the automatic set of 60 + 12.

- The pipeline handled PowerPoint without changes to its logic; three small fixes: lecture
  names now come from file names, empty bullet lines are dropped, and very long text pages are
  split by sentence.
- Search finds the right slide for 89 % of the teacher's questions, 100 % of the students'
  questions (they reuse the slides' wording) and 83 % of the automatic set. Most automatic-set
  misses are broad questions about the long PDF guidelines, where many pages fit.
- **The tricks that helped on the Java course do not help here.** The extra sentence describing
  each slide made search worse (89 % → 84 %): on a course about one topic, every slide got almost
  the same sentence. The keyword search and the slower re-ranking did not improve results either.
  So the simplest setup is the best one here. The sentence stays an option, switched off by
  default; the keyword search was an experiment and has been removed.
- Many of the teacher's questions seemed to be answered only in the course handbook, which is
  not part of the material. Their search scores look just like those of answerable questions, so
  only the answer step can send them to the teacher.
- **Answers, on a sample of 40 questions** (about 2 hours on this laptop): every one of the 18
  answerable questions was answered citing the right slide. Of the 16 it must not answer, it
  forwarded 12. The 4 that slipped through were stretched from a nearby page: an NTC game
  named on a list slide got a made-up description, a creativity slide was attributed to the
  wrong author, and two exam questions from another subject (on assessment, which this
  course's guidelines also cover) got answers from those guidelines. The answers were in
  Croatian, as always. The automatic check does not yet catch this kind of stretching; a
  stronger model on the pilot server is the likely fix.
- Three questions we had marked "handbook only" turned out to be answered by the slides
  after all, and the clone answered them correctly. A re-check of all 28 such questions
  against the slide and PDF text found 5 that the material does answer; 23 really are only in
  the handbook (e.g. Vygotsky, Piaget's stages, descriptions of NTC exercises). The teacher
  confirms the final list.

**Latest results** (2026-11-15):

| What we measured | Result |
|---|---|
| Finds the right slide (top 5 results), slide-like / student-like questions | 100 % / 89 % (95 % with the slower search mode) |
| Answers the teacher's 60 exam questions, citing the right slides | 59 of 60 |
| Answers the 25 slide-like questions, citing the right slides | 25 of 25 |
| Answers the 19 student-like questions, citing the right slides | 16–17 of 19 |
| Forwards the 6 questions it must not answer to the teacher | 6 of 6 |
| Of 35 answers checked by hand against their slides | 5 with a clear error, 3 more with a small unsupported addition |

**What an answer goes through:** questions about grades or deadlines go straight to the teacher;
otherwise the clone searches the slides, writes an answer citing slide numbers, and an automatic
check stops answers that cite nothing, say "I don't know" in prose, or contradict the slides.
Everything stopped goes to the teacher's question queue.

**Known weak spots:**

- A handful of answers still slip in something the slides don't say (e.g. mixing up two columns of
  a Java vs C# table). The automatic check catches contradictions but not all of these.
- A few questions (2 of 19 student-like, 1 of 60 exam) are missed because search doesn't find
  their slide; the clone then forwards them instead of guessing.
- Answers often ended with a sentence listing things the slides "don't cover", even when the
  question was fully answered. A new instruction removed it from about half of 17 answers
  checked, but on the full 50-question run the effect was small (11 → 9 of 42 answers), and in
  one case the model marked a correct answer "I don't know", so it went to the teacher. The
  instruction was taken back out; we will retry with a stronger model.
- The automatic check is not consistent between runs: one wrong "yes" answer that it stopped
  last time reached the student this time.
- The automatic grader we use for measuring is too strict about half the time when it fails an
  answer, so its scores understate quality; a sample was checked by hand. Rewording its
  instructions did not make it more reliable; a stronger grader model is the likely fix.
- A few slides themselves are outdated or contain errors; that list goes to the teacher.

**Step 3, first piece (2026-11-17): upload.** A teacher can create a course and upload PDF,
PowerPoint or Word files. The upload returns at once; a separate background worker then
reads, splits and indexes the files, and the teacher can see its progress (queued,
processing, ready, or failed with a reason and a retry). Each upload becomes a new draft
version that students do not see yet. Files that did not change are copied over from the
previous version instead of being processed again. There is no sign-in yet (a stand-in
for development) and no web page yet: it is used through the API.

**Step 3, second piece (2026-11-17): approval.** A processed draft goes live only in two
steps by the teacher: *approve* (they have checked the files) and *activate* (students get
it from now on). Exactly one version is live at a time; activating a new one retires the
old one, which can be made live again if something is wrong. A draft the teacher does not
want can be thrown away; approved versions are kept as history.

**Step 3, third piece (2026-11-17): student chat.** A student can ask a question in a
published course and gets the answer from the live version only, with its sources, and the
first thing every chat says is that they are talking to an AI. While the clone works, the
student sees what it is doing (checking the question, searching, writing, checking the
answer); the answer itself appears only after the automatic check has passed. Questions it
cannot answer go to the teacher's queue. Students can mark an answer helpful or not, and
see only their own questions. Tried on the second course with the real models: a question
about points went straight to the teacher, a question the slides answer got a correct
answer citing slide 16 after 82 seconds on this laptop, and a question about Vygotsky (not
in the material) went to the teacher. There is no chat page yet.

**Step 3, fourth piece (2026-11-17): the teacher's question queue.** The teacher sees the
questions the clone passed on, with the reason in plain words, but not who asked. They can
answer (optionally pointing to the file and slide) or dismiss a question that needs no
answer. The student finds the teacher's answer under their question in the chat history.
Answers that point to a slide later become test questions for the course, at no extra work.

**Step 3, fifth piece (2026-11-17): the web pages.** CMP can now be used in a browser, in
Croatian, on a computer or a phone. Teachers create a course, upload files, review a
draft, approve and publish it, and answer the questions in their queue. Students open the
course's chat link: the notice that they are talking to an AI is always at the top, they
see what the assistant is doing while it works, then the answer with clickable sources,
and later the teacher's own answers to their forwarded questions. The whole loop was tried
in a browser on the second course. Sign-in is still a development stand-in (just a name).

**Step 3, sixth piece (2026-11-17): test questions after every upload.** After each upload
the system writes student-style questions from the new slides by itself (about 60 per
course, in several styles) and checks whether the assistant finds the slide each question
came from. The teacher sees the result while reviewing the draft ("finds the right slide
for 8 of 8 check questions"), can look through the questions and delete bad ones; nothing
is required of them. Unchanged files keep their questions. On this laptop a full check takes
about 18 minutes; on the pilot server, about a minute.

**Step 3, last piece (2026-11-17): the teacher's settings.** A teacher can set how the
assistant talks: "ti" or "Vi", how long its answers are, how much it helps with exercises
(never full solutions), examples, terminology, topics it should always pass to the teacher,
and a few lines of their own. They try the settings on a test question first; when they
activate them, the system answers a sample of questions with the new settings and switches
only if the assistant still passes on what it must not answer and still answers the course
material. Tried on the demo course: a topic set to go to the teacher was passed on, answers
got shorter, and the check passed (all 12 "must not answer" questions passed on).

**Step 4, first piece (2026-11-17): opening CMP from Moodle.** CMP can now be added to a
Moodle course as an external tool (LTI 1.3, the standard Moodle and Merlin use). A teacher
who opens it the first time links the Moodle course to a CMP course; after that, students
who open it land straight in that course's chat, signed in by Moodle, with no extra
password. CMP keeps only an anonymous code per person, never names, e-mails or Moodle
user numbers. Tried with a real Moodle running locally: the teacher linked the course and
published a lecture, the student landed in the chat and got an answer.

**Step 4, second piece (2026-11-17): CMP keeps up with Moodle.** When the teacher turns it
on, CMP checks the Moodle course every 20 minutes (or at once with "Provjeri sada") for new
or changed files. It takes only what students can see; files the teacher keeps hidden stay
in Moodle. Anything new becomes a draft for the teacher to review and publish, never live by
itself. Unchanged files are not downloaded again. CMP reads Moodle through a separate
account that can only look at course contents. Tried with the local Moodle: two visible
lectures arrived as a draft, a hidden one did not, and a second check found nothing new.

**Removing material (2026-11-18).** A teacher can remove a file from the course (as a new
draft they publish). A file that is deleted or hidden in Moodle leaves the assistant by
itself, straight away; files the teacher uploaded directly in CMP are never touched, and if
Moodle suddenly lists no files at all, nothing is removed (that is more likely an access
problem). Tried with the local Moodle: a lecture hidden in Moodle was gone from what
students get after the next check, and the previous version stayed available.

**Several teachers (2026-11-18).** A course can have more than one teacher. Every teacher of
the Moodle course who opens CMP joins the linked course automatically and can manage it.

**Notifications (2026-11-18).** Teachers see notices in CMP when a new version waits for
review, when Moodle files were removed, when a student's question comes to them, and when new
assistant settings pass or fail their check. Students see when a teacher has answered their
question. CMP sends no e-mail: it keeps no e-mail addresses.

**Also (2026-11-18):** CMP can open inside the Moodle page itself; a teacher can choose Moodle
sections whose new files go live straight away (everything else still waits for review); chat
history and handled questions are deleted after 180 days (configurable); and whoever runs the
installation can register learning platforms on an admin page instead of the command line.

**Installing CMP on another computer (2026-11-18).** One script sets up and starts
everything: `setup.ps1` on Windows, `setup.sh` on Linux and macOS. It checks for Git and
Docker (and, if you ask it to, installs them), creates the settings file with fresh random
passwords, builds and starts all parts of CMP in containers, downloads the AI models and
waits until CMP answers. With an NVIDIA graphics card it also starts the fast model server
(vLLM). An optional switch adds a test Moodle that is already connected to CMP. Running it
again updates and restarts; nothing is lost. On a real server with its own name, CMP gets an
HTTPS certificate by itself. Tried on this laptop from scratch: about 2.5 minutes with the
test Moodle once the models were downloaded, then a Moodle teacher linked a course, Moodle
material arrived, was published, and a student got an answer. One fault found on the way: the
web server cut answers off after 60 seconds (answers take longer than that on a laptop);
fixed, a 144-second answer now arrives in full.

**Sign-in, teams, tracing, real questions, follow-ups, Teams and Classroom (2026-11-18).**
- Teachers who use CMP outside Moodle can sign in with their school's Microsoft or Google
  account; only accounts of that institution are let in. This was tested against a
  simulated sign-in service; it needs the school's own app registration before it can be
  tried for real.
- A teacher can invite a colleague to a course with a one-time link, and owners can remove
  teachers.
- Whoever runs CMP can now see a record of every answer in Langfuse: which question, which
  version of the material, which model, how long each step took and how many tokens it used.
  It runs on the same server; tried on this laptop, the records arrive complete.
- When a student marks an answer 👎, the question and the answer go to the teacher. Questions
  the teacher answers while pointing to a slide become the course's own test questions, so
  the test set grows from normal work; they are checked after every upload.
- The chat now understands short follow-ups such as "a tko je provodi?" by turning them into
  a full question first. Each student can ask up to 30 questions an hour per course, one at a
  time.
- CMP can fetch course files from Microsoft Teams and Google Classroom (Teams' files,
  Classroom's published materials; Google Docs and Slides are converted). This was tested
  against simulated services only; it needs a real school account to try. (Correction,
  2026-11-19: this was first written as "they open CMP the same way Moodle does (LTI)". They
  don't: neither Teams nor Classroom can open outside tools that way. Teams uses the tab app
  below; how Classroom students get in is still open.)

**Teams without LTI, a smoke test, model downloads (2026-11-18).**
- Schools add CMP to Teams as a **Teams tab** (the only way in from Teams). Teams signs the user in;
  CMP asks Microsoft which team members are owners (treated as teachers) and which are
  members (students). From there it works as in Moodle: a teacher links the team to a course
  once, and members get its chat. A script builds the app package the school uploads to
  Teams. Tested only against simulated Microsoft services.
- **Smoke test:** a 14-slide course of our own (beekeeping basics) and 10 questions show in
  one go whether an installation or a newly chosen AI model works. It runs every part for
  real and removes its course afterwards. First run on this laptop: 10 of 10 right, about
  10 minutes; on a GPU server it will take seconds. `.\setup.ps1 -Check` runs it after
  installing.
- **Models:** which model does which job is set in `config/models.yaml` (without a GPU) or
  `config/models.vllm.yaml` (with one). A new command, also used by the setup scripts, lists
  them and downloads whatever is missing.
- **For the GPU computer:** a step-by-step guide ([docs/GPU-SETUP.md](docs/GPU-SETUP.md)) and
  the whole benchmark in one command (`.\setup.ps1 -Benchmark`): it loads the courses, runs
  every question set and writes one summary table per model. Tried here on the smaller course:
  same search numbers as before, answers working.
- **Any NVIDIA card size (2026-11-19):** the setup now reads how much memory the graphics card
  has and picks a model that fits: the full Qwen 3.5 9B on 24 GB or more, a compressed 9B on
  16 GB, a compressed 4B on 8 GB (such as the RTX A1000 test PC). vLLM downloads it by itself.
  On Windows it also checks Docker's share of the PC's memory and offers to raise it.
- **First run on a graphics card (2026-11-23):** on a test PC with an 8 GB NVIDIA RTX A1000 the
  smoke test passed 10 of 10, with each answer in **4–12 seconds** instead of 1–3 minutes on a
  processor. Getting there took a few fixes to the setup script and to how much of the card's
  memory the model server may use. Next: the full benchmark on that PC.

**Next:** the pilot server (rent or buy a GPU; the containers are ready), SRCE's approval for
Merlin, the teacher's review of the question sets, and trying sign-in, Teams and Classroom
with a real school's accounts. Fixes for the weak spots above (on both courses, answers
stretched from a nearby slide are the main one) and the teacher's review of the question
sets continue alongside. Choosing the final AI model waits for the pilot server.

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
| Retrieval | Dense vector search filtered by `kb_version`, optional reranking |
| Question queue (step 3) | Teacher API: forwarded questions with the reason, answer with optional sources (checked against the course's files) or dismiss; no student identity shown; the student sees the teacher's answer in their history |
| Moodle sync (step 4) | Every 20 minutes or on demand: new or changed visible files in the linked Moodle course become a draft for the teacher; hidden files are skipped; unchanged files are not downloaded again; read-only Moodle account |
| Moodle launch (step 4) | LTI 1.3 tool: Moodle opens CMP and says who the user is; teachers link the Moodle course once, students land in its chat; only anonymous codes stored; signed sessions replace the dev sign-in outside dev mode; a local Moodle in Docker for testing |
| Teacher's settings (step 3) | Persona: ti/Vi, answer length, help with exercises, examples, terminology, topics passed to the teacher, free text; preview; activation only after a check that forwarding and answering still work |
| Web pages (step 3) | React + Vite: teacher pages (courses, upload, review, approve, publish, question queue) and the student chat (AI notice, progress, answer with sources, teacher answers); runs in a dev container on port 5173 |
| Student chat (step 3) | Chat API: answers from the live version only, server-sent progress then the checked answer with citations, AI notice first, forwarded questions to the teacher queue, own history, 👍/👎 |
| Upload and approval (step 3) | Teacher API: create a course, upload files into a new draft version, follow its processing, approve and activate it (one live version, rollback), discard drafts; a background worker (Procrastinate queue in Postgres) processes new files and carries unchanged ones over; dev-only sign-in |
| Answers | Scope check (`fast`; admin questions forwarded at once, also by a keyword net), answer with numbered citations (`answer`), citation and prose-refusal checks, a live answer check (`check`) that stops contradictions and mostly-refusal answers; everything stopped goes to the teacher queue with a reason |
| Evaluation | Golden question set per subject; recall@k and MRR split by easy/hard, plus top scores for questions the clone should decline; answer evaluation (answered, cites the expected slide, forwarded, optional judge) |
| Sign-in and teams | Teachers: Microsoft/Google (institution accounts only) or LTI; course invites, owners, removing teachers; students only through their learning platform |
| Teams and Classroom (step 5) | Teams: a Teams tab app with Teams sign-in (Teams cannot launch LTI tools); Classroom: no student entry yet; material sync through Microsoft Graph (team files) and Classroom + Drive (published materials, Docs/Slides exported) |
| Tracing | One Langfuse trace per answer with every model call (OpenTelemetry), self-hosted next to CMP; optional |
| Collected questions | 👎 answers go to the teacher; queue answers with a source become the course's own test questions, checked after each upload and exportable for the evaluators |
| Chat | Short follow-ups rewritten to stand on their own; 30 questions per hour per student and course, one at a time |
| Smoke test | `scripts.smoketest`: our own 14-slide course + 10 questions, every part for real; `setup -Check` |
| Tests | pytest (178): RLS isolation, model config, chunking, citations, answer checks, queue, LTI, sign-in, sync, connectors, tracing; Vitest (22) for the pages |

Retrieval: recall@5 is **1.00** on easy questions and **0.84** on hard ones with plain dense
search, **0.89** with a context sentence per chunk (`--context`, the current default version), and
**0.95** with hybrid candidates plus reranking (an experiment, removed after the second course did not confirm it). Answers (dense, dev models on CPU): easy 25/25,
hard 16–17/19, teacher's exam questions 59/60 cited the expected slides; 6/6 out-of-scope and admin
questions forwarded. Full comparisons and timings in CLAUDE.md.

Not tried for real yet (needs a school's accounts): Microsoft/Google sign-in, Teams and
Classroom. Not built yet: the student profile (layer 3), voice and avatar (step 6).

## Architecture

```
LMS (Moodle/Merlin, Teams, Classroom, …) ─► connector ─► CMP core
                                                          ├─ ingestion: Docling → chunking → embeddings → pgvector
                                                          ├─ retrieval: dense → rerank → parent sections
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

### Install and run (one script)

To try CMP or run it on a server, use the setup script. It needs only Git and Docker and
can install both itself. Windows (PowerShell):

```powershell
.\setup.ps1                # check, set up, start; prints what is missing
.\setup.ps1 -Install       # also install missing Git / Docker Desktop (winget, admin rights)
.\setup.ps1 -WithMoodle    # plus a local test Moodle connected to CMP
.\setup.ps1 -WithTracing   # plus Langfuse: a record of every answer (http://127.0.0.1:3000)
.\setup.ps1 -Check         # then the smoke test: 10 questions on a small course of our own
.\setup.ps1 -Benchmark     # then the internal benchmark on the courses in data\ (see below)
.\setup.ps1 -Stop          # stop everything (data is kept); -Status shows the containers
```

### "running scripts is disabled on this system"

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

Linux / macOS:

```bash
./setup.sh                 # same switches: --install, --with-moodle, --with-tracing, --check, --benchmark, --stop, --status
```

**On a PC with an NVIDIA GPU**, follow [docs/GPU-SETUP.md](docs/GPU-SETUP.md): what to copy
from the old computer (the course material is not in git), which model fits the card's memory,
the smoke test, and the benchmark in one command (`.\setup.ps1 -Benchmark`) for comparing models.

Run on its own (not from a clone), the script fetches the project first. It creates `.env`
with random passwords, `SESSION_SECRET` and `ADMIN_TOKEN` (or adds what is missing), builds
the images, starts the containers (vLLM as well when `nvidia-smi` finds a GPU), downloads the
models named in the active models file and waits for `/health`. Then open
<http://localhost>; on this computer you sign in with any name (test sign-in). The admin page
is `http://localhost/#/admin` (key: `ADMIN_TOKEN` in `.env`). With `-WithMoodle` the test
Moodle is at <http://127.0.0.1:8080> (`CMP_MOODLE_PORT`), users `teacher1` / `student1`,
password `MOODLE_TEST_PASSWORD` in `.env`.

On a server, pass its host name (`-Site cmp.example.hr` / `--site cmp.example.hr`): CMP then
uses HTTPS with an automatic certificate and only LTI sign-in (`AUTH_MODE=prod`). Only the web
server is published (ports 80/443); data lives in named Docker volumes. The stack is
`infra/docker-compose.yml` (+ `infra/docker-compose.gpu.yml` for vLLM).

### Development setup

The rest of this section is for working on CMP's code (backend on the host, live reload).

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
   `ask` and `evaluate` also take `--version N`, and `--rerank 20`; `evaluate` takes
   `--set PATH` for another question set of the same subject. Sets made from someone else's text
   (e.g. a teacher's own exam questions) go in `eval/sets/local/`, which is git-ignored.
   Ingest reports record time per step; eval reports record their runtime.
5. Get the clone's answer (needs `ollama pull qwen3.5:9b` and `qwen3.5:4b`), and evaluate answers
   on the golden set (~2 min per question with the answer check and the judge on a CPU laptop;
   `--ids`/`--limit` for small runs, `--no-check` skips the answer check):
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
| `answer` | Qwen 3.5 9B | student answers (step 2) |
| `fast` | Qwen 3.5 4B | query rewriting, out-of-scope check |
| `check` | Qwen 3.5 9B | checks each answer against its sources before the student sees it |
| `context` | Qwen 3.5 4B | chunk context at indexing |
| `vision` | Qwen3-VL 8B | image/diagram captions |
| `judge` | Qwen 3.5 9B | evaluation |

The dev chat models are placeholders that fit an 11 GB GPU. The real choice for `answer` is made on
golden-set results from the vLLM setup used in the pilot, never on Ollama's quantized models.

Model files: `config/models.yaml` (no GPU: everything through Ollama) and, with an NVIDIA GPU,
one per card size: `config/models.vllm.yaml` (24 GB+), `models.vllm-16gb.yaml`,
`models.vllm-8gb.yaml` (the chat roles through vLLM, which serves the one model named by
`serve_model`, started with `serve_args`). The setup scripts pick the file from the card's memory
and download what it names (see [docs/GPU-SETUP.md](docs/GPU-SETUP.md)).

To try another model (e.g. a Mistral): change the `model:` lines (Ollama names such as
`ollama_chat/mistral-small3.2:24b`, or `serve_model` + `hosted_vllm/<Hugging Face id>` for vLLM)
and raise `version`, then:

```bash
cd backend && uv run python -m scripts.models --pull   # downloads missing Ollama models
uv run python -m scripts.smoketest                     # 10 questions: does it still work?
```

vLLM downloads its `serve_model` from Hugging Face by itself when it starts (rerun the setup
script after changing it). Whether a model is *better* is decided on the full question sets.

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
  scripts/      smoke, parse, ingest, ask, evaluate, evaluate_answers
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
uv run fastapi dev app/main.py                 # API with reload (teacher API under /api/courses)
uv run python -m scripts.worker                # background worker that processes uploads
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
2. **Answers** — model roles, citations, abstention + teacher queue, live answer check *(working, being tuned)*; compare 2–3 open models *(needs the pilot GPU server)*
3. **Product** — teacher persona, approval workflow, versioning, question queue, chat UI, direct upload
4. **Moodle connector** — LTI 1.3 + Web Services polling, then SRCE/Merlin
5. Teams and Google Classroom connectors
6. Later: student profile, voice, avatar

## License

Proprietary — all rights reserved. See [LICENSE](LICENSE).
