# Contributing

Before uploading code, identify the owning top-level module and keep the change inside that
boundary. See `docs/REPOSITORY_LAYOUT.md` for dependency direction and upload rules.

For Python changes run:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -q
```

Never commit `.env`, API keys, virtual environments, IDE settings, build outputs or runtime data.
Changes to `shared/contracts/` should be isolated because they affect multiple teams.
