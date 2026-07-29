# Learn Highland by following a run

This path teaches the agent harness by running four deterministic examples,
then tracing each result into the code and inspectable local records. The
scripted exercises require no API key and do not claim to be model-generated.
They use predetermined protocol responses to exercise the same retrieval,
tool-policy, approval, artifact, and workflow boundaries as real mode.

## Before you begin

The easiest live-model setup asks for only one Cohere API key:

```bash
./bootstrap.sh
```

For the deterministic, non-billable learning mode, start the complete local
stack directly:

```bash
docker compose up --build --detach --wait
```

Open the workspace at `http://localhost:3000`. The API is at
`http://localhost:8080`, and its health response names the active backend:

```bash
curl http://localhost:8080/health
```

Both commands synchronize the checked-in mock content into the local search
index before the API starts. The direct Compose command defaults to `scripted`;
`bootstrap.sh` explicitly selects Cohere mode after receiving a key. All
mutable state is under `var/`; checked-in fictional source data remains under
`data/seed/`. MCP connectors are supervised stdio child processes of the API
and call the mock systems over HTTP.

The executable tutorials below use the Python development environment because
they expose exact assertions and trace records:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## 1. Discover: prepare for a customer meeting

Run the scripted, read-only meeting preparation:

```bash
.venv/bin/pytest -q -s tests/acceptance/test_meeting_preparation.py
```

Follow the run:

1. Seed records in `data/seed/` are normalized and chunked by
   `src/highland/retrieval/ingestion.py` and
   `src/highland/retrieval/contracts.py`.
2. `src/highland/retrieval/hybrid.py` searches local lexical and FAISS indexes,
   applies customer and visibility filters locally, then reranks the allowed
   candidate text.
3. Indexed Archive, Relay, Beacon, Pulse, and Track text supports discovery.
   Current CRM account fields, meeting details, ticket state, deployment state,
   and issue state come through atomic MCP tools because those records can
   change.
4. `src/highland/runtime/agent.py` offers only policy-allowed tools, consumes
   their normalized results, and produces citations.
5. `src/highland/retrieval/citations.py` resolves indexed evidence IDs. The
   acceptance test verifies that no write tool was offered or called.

Trust boundaries encountered: filtering happens before candidate text reaches a
real provider; tool arguments are validated before MCP; the connector translates
between MCP and the mock HTTP API; citation resolution never trusts arbitrary
model text as an evidence ID.

In an interactive run, inspect `GET /runs/{run_id}/trace` and
`GET /runs/{run_id}/evidence`. Conversations persist beneath
`var/highland/conversations/`, event JSONL beneath
`var/highland/runs/events/`, and the derived index beneath
`var/highland/indexes/`.

## 2. Create: turn grounded evidence into an artifact

Run the scripted model-to-artifact flow:

```bash
.venv/bin/pytest -q -s tests/integration/test_artifact_model_flow.py
.venv/bin/pytest -q -s tests/integration/test_artifact_export.py
```

`src/highland/artifacts/generation.py` gives the drafting model the selected
conversation evidence and requires structured artifact output.
`src/highland/artifacts/repository.py` stores editable Markdown, metadata,
citations, and revision history beneath `var/highland/artifacts/`.
`src/highland/artifacts/coverage.py` checks claims against attached evidence;
it does not invent supporting facts. `src/highland/artifacts/export.py` renders
Markdown and PDF while refusing external resource fetches.

The model call drafts or revises prose. Application code owns schema
validation, optimistic revision checks, citation preservation, coverage rules,
safe filenames, persistence, and export. In the web workspace, use **Create
artifact** from a completed Discover answer, then inspect revisions and evidence
coverage in the artifact editor.

## 3. Approve an external action

Run the deployment investigation, including its durable approval pause:

```bash
.venv/bin/pytest -q -s tests/acceptance/test_deployment_investigation.py
```

The scripted model requests fresh metrics and ticket data, then proposes
`support__create_ticket`. `src/highland/runtime/policy.py` identifies the write
as approval-required before the connector is called.
`src/highland/runtime/approvals.py` persists the exact validated arguments and
stable idempotency key. The run pauses, and only an explicit approval resumes
the write. `src/highland/runtime/mcp.py` then executes the atomic tool; the mock
support service persists the fictional ticket beneath `var/`.

The model proposes the action and, after receiving its result, summarizes it.
Application code owns the pause, decision state, idempotency, execution, and
audit events. Rejecting the request records a decision and never executes the
write.

In an API-driven run, read `approval_required` from
`GET /runs/{run_id}/trace`, inspect `GET /approvals/{approval_id}`, and choose
`POST /approvals/{approval_id}/approve` or
`POST /approvals/{approval_id}/reject`.

## 4. Automate: run a versioned weekly health workflow

Run the builder and customer-isolated weekly workflow:

```bash
.venv/bin/pytest -q -s tests/acceptance/test_workflow_builder.py
.venv/bin/pytest -q -s tests/acceptance/test_weekly_customer_health.py
```

`src/highland/workflows/schema.py` defines the bounded five-node workflow
language. `src/highland/workflows/repository.py` separates drafts from immutable
published versions. `src/highland/workflows/executor.py` orders nodes, evaluates
validated branches, bounds loops, pauses writes for approval, and persists run
history. `src/highland/workflows/weekly_health.py` isolates each customer,
requests a structured classification, records per-account usage, and saves a
cited report artifact.

The model drafts workflow structure and performs semantic classification in
real mode. Application code owns graph validation, versions, loop limits,
customer isolation, schedules, approval state, persistence, and usage
aggregation. Workflow definitions persist beneath `var/highland/workflows/`;
workflow runs and trace events persist beneath `var/highland/runs/`.

## Follow this run in code

Every durable trace event is defined in `src/highland/runtime/events.py`.
The table shows where to continue reading after selecting an event in the UI or
from `GET /runs/{run_id}/trace`.

| Trace event | Meaning | Follow in code |
| --- | --- | --- |
| `run_started` | API or executor accepted the run | `src/highland/api.py`, `src/highland/runtime/agent.py` |
| `retrieval` | Local search, filters, and timing completed | `src/highland/discover/service.py`, `src/highland/retrieval/hybrid.py` |
| `model_call` | One scripted or real chat turn completed | `src/highland/runtime/agent.py`, `src/highland/models/cohere.py` |
| `model_delta` | Streamed answer text became visible | `src/highland/runtime/agent.py`, `src/highland/api.py` |
| `tool_call` | The model proposed an MCP operation | `src/highland/runtime/agent.py`, `src/highland/runtime/policy.py` |
| `tool_result` | A validated connector call returned | `src/highland/runtime/mcp.py`, `src/highland_mocks/mcp_server.py` |
| `approval_required` | A write is durably paused | `src/highland/runtime/approvals.py` |
| `approval_decision` | A learner approved or rejected it | `src/highland/runtime/approvals.py`, `src/highland/api.py` |
| `citation` | Answer text references indexed or tool evidence | `src/highland/retrieval/citations.py` |
| `usage` | Tokens or search units were recorded | `src/highland/models/budgets.py`, `src/highland/storage/cost_ledger.py` |
| `final` | The completed answer was persisted | `src/highland/runtime/events.py`, `src/highland/discover/conversations.py` |
| `error`, `run_failed` | The run reached a documented failure | `src/highland/runtime/reliability.py` |
| `run_cancelled` | Cancellation stopped further work | `src/highland/runtime/cancellation.py` |
| `run_completed` | A workflow or replay reached completion | `src/highland/workflows/executor.py`, `src/highland/runtime/reliability.py` |

Trace storage redacts common secret fields and tolerates a truncated final
JSONL record. Replay reads events without repeating model or tool calls.

## Scripted versus real mode

Scripted mode uses `ScriptedChatModel` and deterministic embedding/reranking
implementations in `src/highland/models/scripted.py`. Responses are declared by
tests or checked-in demo scripts. They are simulated protocol fixtures, not
answers generated by a language model. This mode is deterministic,
non-billable, and suitable for the complete learning path.

Real mode is explicit:

```bash
HIGHLAND_MODEL_BACKEND=cohere COHERE_API_KEY=... docker compose up --build
```

No source change is required. `src/highland/models/provider.py` constructs the
Cohere provider only when `cohere` is selected and fails if the key is absent.
Real chat, embed, and rerank calls are potentially billable. Before opting in:

- review model IDs and dated prices in `config/model_prices.json`;
- keep the per-run and monthly limits in `.env.example`;
- set a provider-side spending limit in the Cohere dashboard;
- use `highland usage` to inspect the local ledger;
- use `HIGHLAND_RUN_LIVE_TESTS=1` only for intentionally billable evaluations.

The application budgets stop the next call when a limit is reached; they are
educational guardrails, not a financial guarantee.
