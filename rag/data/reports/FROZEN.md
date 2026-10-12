# Frozen evidence (2026-10-08)

These reports are the evidence for the tsvector (`simple` vs `english`) decision and the first tuning
round. Do not overwrite or regenerate them. Only the `.md` reports are versioned; the matching `.json`
files are local, gitignored artifacts (`rag/data/reports/*.json`) and are not in the repository.

- `retrieval-eval-20261008-102646.md`: pre-tuning eval run while both `tsv` and `tsv_en` existed
  (includes the english modes).
- `retrieval-tune-20261008-102824.md`: 324-point grid INCLUDING the `english` dimension.
- `retrieval-eval-20261008-103611.md`: first final-config eval.

`tsv_en` was dropped (migration 002) and the `english` code path removed, so `eval --tune` now runs a
162-point grid (simple only) and CANNOT reproduce the english comparison or the 324-point numbers.
Later reports are the current-code results (versioned: `retrieval-eval-20261008-112120.md`,
`retrieval-eval-20261008-115950.md`, `retrieval-tune-20261008-112149.md`,
`confidence-eval-20261008-120107.md`, `ident-study-20261008-120016.md`).
