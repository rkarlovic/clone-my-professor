# Evaluation

- `sets/<subject_id>.yaml` — golden question set per subject (format: `sets/_template.yaml`).
- `reports/` — run outputs (git-ignored).

Build step 1 measures **recall@5**: for each `answerable` question, does any of the top 5
retrieved chunks match one of `expected_sources` (document + page/slide)?

Course material for the spike goes in `/data/<subject_id>/` at the repo root (git-ignored).
The eval runner (`uv run python -m eval.run --subject <id>`) is part of the spike itself.
