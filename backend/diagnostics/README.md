# Backend diagnostics

Backend-owned diagnostic clients and fixtures live here. They validate backend protocols but are
not the product frontend.

`web/` contains the browser page used to exercise the in-memory WebSocket streaming protocol.
The Django backend serves only its explicit asset allowlist from this directory.

Future product UI belongs in the repository-level `frontend/` module.
