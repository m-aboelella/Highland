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

Install the exact checked-in development graph with `make setup`. Runtime
containers use `requirements.lock`; local development and CI use
`requirements-dev.lock`. When a declared dependency range changes, run
`make lock`, review both generated lock diffs, and rerun `make ci`.

Before a live run, review the configured model IDs and budgets, and set a
spending limit in the Cohere dashboard. Never put the API key in a command that
will be committed or copied into a report.

Run the deterministic production-path retrieval benchmark with:

```bash
highland eval retrieval --enforce-baseline
```

It runs the reviewed cases in `config/evaluation/retrieval-relevance.json`
through `DiscoverService` and the production hybrid retriever. It reports
candidate recall, precision/recall at k, MRR, stage loss, latency, leakage, and
configuration/content fingerprints, comparing quality with
`config/evaluation/retrieval-baseline.json`. Ignored JSON and Markdown reports
are written under `var/highland/reports/retrieval/`.

Billable end-to-end quality evidence is deliberately separate:

```bash
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... \
  highland eval scenario customer-meeting-preparation
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... highland eval all
```

Each report keeps model IDs, prompt version and hash, provider trace IDs, and
usage. Citation and approval-policy failures are deterministic; semantic claim
coverage, forbidden behavior, and response structure are model-judged. The
evaluator uses the production retrieval, agent, MCP, policy, trace, and workflow
paths. It rejects approval checkpoints and never executes a proposed write, so
repeated runs do not mutate the mock systems.

Run the deterministic failure matrix, retry, approval-restart, corrupted-log,
and trace replay checks with `pytest tests/reliability`. Recovery behavior and
the read-only replay versus fresh linked re-run distinction are documented in
[Failure recovery and trace replay](RELIABILITY.md).

Live evaluation aggregates model calls, tokens, rerank units, estimated
dollars, and latency by scenario, run, node, and model. Configure
`HIGHLAND_EVALUATION_WARNING_BUDGET_USD` and
`HIGHLAND_EVALUATION_HARD_BUDGET_USD`; reaching the hard boundary aborts before
the next call. Pass `--baseline path/to/cost-report.json` to compare against
stored estimates without re-pricing historical usage. Unknown prices remain
visible, and reports contain hashes and usage metadata rather than prompts or
keys.

With the repository's dated price configuration, the two Chat calls in each
showcase evaluation (scenario plus semantic judge) currently estimate to
approximately `$0.00`, so all three are approximately `$0.00`. This is a dated
configuration value, not a promise about Cohere pricing; update
`config/model_prices.json` for the applicable commercial agreement.

## Repository quality gate

Run the same secret-free checks used by pull requests:

```bash
make ci
```

This runs Ruff, focused Python static typing for the durable public contracts,
TypeScript checking, the hermetic Python suite, frontend tests, a production
frontend build, and a tracked-file scan for secrets or generated runtime state.
CI explicitly selects scripted mode and never opts into live Cohere tests.
Failed deterministic reports are uploaded for diagnosis without uploading
provider keys or private prompts.

For the clean-checkout runtime gate, run `make compose-smoke`. It builds and
starts scripted Compose, waits for index synchronization, checks API health, a
real indexed search, and the UI, enforces the committed retrieval baseline,
then shuts the stack down even on failure. `make release-check` includes this
gate and therefore requires Docker Compose v2.
