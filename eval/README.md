# Evaluation

- `sets/<subject_id>.yaml` — golden question set per subject (format: `sets/_template.yaml`).
- `reports/` — run outputs (git-ignored).

Build step 1 measures **recall@5**: for each `answerable` question, does any of the top 5
retrieved chunks match one of `expected_sources` (document + page/slide)?

Course material for the spike goes in `/data/<subject_id>/` at the repo root (git-ignored).
Run it with `uv run python -m scripts.evaluate --subject <id>` from `backend/`. The full
answer-level runner (`eval.run`: grounding, abstention) arrives in build step 2.
