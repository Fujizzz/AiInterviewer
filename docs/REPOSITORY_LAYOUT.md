# Repository layout and collaboration rules

```text
ai_interviewer/
├── agents/       deterministic interview Agent core
├── app/          runnable terminal MVP and temporary concrete adapters
├── backend/      backend team ownership boundary
├── frontend/     frontend team ownership boundary
├── evaluation/   production evaluation module boundary
├── rag/          production retrieval module boundary
├── ai_security/  AI safety and security module boundary
├── shared/       versioned cross-module contracts
├── tests/        Python contract, unit and integration tests
└── docs/         architecture, module specifications, guides and examples
```

## Dependency direction

```text
frontend → backend API
backend  → shared contracts + Agent public service
app      → shared contracts + Agent public service + temporary adapters
agents   → shared contracts + ports
evaluation / rag → shared contracts + the port they implement
ai_security → shared contracts + explicit public integration points
shared   → no implementation module
```

## Upload rules for new modules

1. Put frontend and backend code only in their reserved top-level directories.
   Backend-owned diagnostic pages stay under `backend/diagnostics/`; product UI stays in `frontend/`.
2. Preserve module-specific manifests such as `package.json`, `pyproject.toml` or lock files
   inside the owning module unless the team explicitly adopts one shared workspace.
3. Do not copy shared request/response models into multiple modules. Interface mapping will be
   handled in a later integration change.
4. Do not import `agents/policies/`, `agents/domain/` or other module internals.
5. Keep generated files, local environments, build output and secrets out of Git.
6. Submit structural moves separately from interface or business-logic changes.
