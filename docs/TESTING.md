# Testing Highland

Highland separates deterministic harness correctness from probabilistic,
potentially billable model quality. Test output includes one of the registered
taxonomy markers, and unknown markers fail collection.

| Layer | Canonical command | Requirements |
| --- | --- | --- |
| Unit | `pytest -m unit` | Python environment only |
| Local integration | `pytest -m integration_local` | Local processes or temporary files; some focused tests may require Docker |
| Scripted acceptance | `pytest -m acceptance_scripted` | Deterministic scripted provider |
| Live model | `HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... pytest -m live_model` | Explicit opt-in and Cohere key; billable |
| Hermetic default | `pytest` | Runs all non-live layers and blocks network access |

The default suite is network-free. A network attempt fails with an explanation
instead of silently reaching an external service. Live tests are collected but
skipped unless both the opt-in flag is exactly `1` and a Cohere key is present.
They print model-backed evaluation output; deterministic evaluation commands
print scripted or deterministic provider labels.

Docker is only needed for tests that explicitly exercise the Compose stack.
The ordinary unit, local-integration, and scripted-acceptance suites use
in-process applications, stdio connectors, temporary directories, and local
FAISS indexes.

Before a live run, review the configured model IDs and budgets, and set a
spending limit in the Cohere dashboard. Never put the API key in a command that
will be committed or copied into a report.
