# Backend module placeholder

Reserved for the backend team's code. No backend implementation has been added yet.

Expected ownership:

- HTTP/WebSocket APIs;
- authentication and authorization;
- database and cache adapters;
- interview session lifecycle;
- request validation and API serialization.

Keep backend framework models inside this directory. When interface work begins, depend on
`shared/contracts/` and call the public Agent service; do not import Agent policies or internals.
The backend may keep its own dependency manifest and tests inside this directory.
