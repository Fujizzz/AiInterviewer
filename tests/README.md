# Test layout

- `agent/contracts/`: shared model and port contract tests;
- `agent/unit/`: deterministic Agent policy tests;
- `agent/integration/`: Agent service and failure-mode tests;
- `agent/mocks/`: reusable Agent port test doubles;
- `app/`: terminal MVP, provider and resume parsing tests.
- `evaluation/`: Evaluation-owned contracts, versioned rubric validation and example fixtures.
- `security/`: current behavior contracts, provenance permissions, guarded execution, model transport and the single CLI.

`security/test_behavior.py` covers the primary behavior-boundary contract, deterministic permits,
semantic requirement coverage, fail-closed execution and isolated SQLite commit scenarios.

`security/test_project_provider.py` and `test_cli.py` use explicit offline ports.
Real backend I/O boundaries are tested in `backend/interviews/tests/test_agent_safety.py`.
Retired classification tests were removed with their implementation; fewer tests after cleanup
do not indicate a measured accuracy change. Historical data and reports are read-only under
`docs/archive/ai_security/`.

Future modules should keep focused unit tests near their own module when their toolchains require
it, and place Python cross-module integration tests under a clearly named directory here.
