# Evaluation

- `sets/<subject_id>.yaml` — golden question set per subject (format: `sets/_template.yaml`).
- `reports/` — run outputs (git-ignored).

Build step 1 measures **recall@5**: for each `answerable` question, does any of the top 5
retrieved chunks match one of `expected_sources` (document + page/slide)? A chunk for a slide
that repeats elsewhere in the material matches on every page it is on (`also_on_pages`), so
naming any one copy's page in `expected_sources` is enough.

Course material for the spike goes in `/data/<subject_id>/` at the repo root (git-ignored).
Run it with `uv run python -m scripts.evaluate --subject <id>` from `backend/`. The full
answer-level runner (`eval.run`: grounding, abstention) arrives in build step 2.
