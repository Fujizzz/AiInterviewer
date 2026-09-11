# Test layout

- `agent/contracts/`: shared model and port contract tests;
- `agent/unit/`: deterministic Agent policy tests;
- `agent/integration/`: Agent service and failure-mode tests;
- `agent/mocks/`: reusable Agent port test doubles;
- `app/`: terminal MVP, provider and resume parsing tests.

Future modules should keep focused unit tests near their own module when their toolchains require
it, and place Python cross-module integration tests under a clearly named directory here.
