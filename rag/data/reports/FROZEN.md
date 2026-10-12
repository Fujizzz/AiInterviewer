# Frozen evidence (2026-10-08)

These reports are the evidence for the tsvector (`simple` vs `english`) decision and the first tuning
round. Do not overwrite or regenerate them:

- `retrieval-eval-20261008-102646.{md,json}`: pre-tuning eval run while both `tsv` and `tsv_en` existed
  (includes the english modes).
- `retrieval-tune-20261008-102824.{md,json}`: 324-point grid INCLUDING the `english` dimension.
- `retrieval-eval-20261008-103611.{md,json}`: first final-config eval.

`tsv_en` was dropped (migration 002) and the `english` code path removed, so `eval --tune` now runs a
162-point grid (simple only) and CANNOT reproduce the english comparison or the 324-point numbers.
Later reports (`retrieval-eval-20261008-1121*`, `retrieval-tune-20261008-112149`, `confidence-eval-*`,
`ident-study-*`) are the current-code results.
