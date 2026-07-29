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

Run the deterministic retrieval benchmark with `highland eval retrieval`.
It derives evidence queries from the scenario manifests, adds exact-ID,
filtering, stale-record, and customer-isolation probes, then writes ignored JSON
and Markdown reports under `var/highland/reports/retrieval/`.

Billable end-to-end quality evidence is deliberately separate:

```bash
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... \
  highland eval scenario customer-meeting-preparation
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... highland eval all
```

Each report keeps model IDs, prompt version and hash, provider trace IDs, and
usage. Citation and approval-policy failures are deterministic; semantic claim
coverage, forbidden behavior, and response structure are model-judged. The
harness supplies read-only evidence and never executes a proposed write, so
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
