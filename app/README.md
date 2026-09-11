# Application module

Owns the currently runnable terminal MVP and concrete local adapters.

- `cli.py`: terminal command implementation;
- `application.py`: application composition and interview use case;
- `parsing/`: resume file loading and profile extraction;
- `providers/`: OpenAI and DashScope clients;
- `reporting/`: user-facing final report construction;
- `adapters/`: temporary MVP implementations of Agent ports.

This directory is not the future backend. HTTP APIs, authentication and production persistence
belong in `backend/` when that module is uploaded.
