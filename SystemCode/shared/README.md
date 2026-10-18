# Shared contracts

`shared/contracts/` contains the versioned data structures exchanged across modules.

Shared code must stay implementation-neutral. It must not import from `agents/`, `app/`,
`backend/`, `frontend/`, `evaluation/` or `rag/`.

Cross-module contract changes should be reviewed separately from module implementation changes.
