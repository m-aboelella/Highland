# Highland

Highland is a self-contained educational enterprise-agent workspace inspired by
the product shape of Cohere North: **Discover**, **Create**, and **Automate**.
It is not a clone and does not use Cohere branding or proprietary behavior.

The repository currently contains the first foundation: a realistic fictional
enterprise called **Summit Software** and six local “external” systems that can
be reached through REST APIs or MCP:

| System | Fictional product | Contents | Port |
| --- | --- | --- | ---: |
| CRM | Atlas CRM | customers, contacts, commercial context | 8101 |
| Knowledge | Archive | product docs and runbooks | 8102 |
| Support | Relay Desk | support tickets and comments | 8103 |
| Observability | Beacon | deployments, incidents, time-series metrics | 8104 |
| Communications | Pulse | messages, meetings, customer updates | 8105 |
| Projects | Track | engineering and customer-success work items | 8106 |

All data is synthetic. Services run on the same machine, but use network
boundaries just like real integrations. Write operations are stateful and
idempotent. The checked-in seed—including downloadable Markdown source
documents—can be restored at any time.

## Quick start with Python

Python 3.11+ is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
highland-mocks generate
highland-mocks dev
```

The service catalog is available at `http://localhost:8099`, and each service
has interactive API documentation at `/docs`, for example
`http://localhost:8103/docs`.

In a second terminal, inspect the ecosystem:

```bash
curl http://localhost:8101/customers/cus_northwind
curl 'http://localhost:8103/tickets?customer_id=cus_northwind&status=open'
curl -X POST http://localhost:8104/metrics/query \
  -H 'content-type: application/json' \
  -d '{"customer_id":"cus_northwind","metric":"retrieval.p95_ms","time_range":"24h"}'
```

Reset mutable service state back to the generated seed:

```bash
highland-mocks reset
```

## Run an MCP connector

Each adapter is deliberately a separate MCP server. Start the mock APIs, then
run one connector over stdio:

```bash
highland-mcp support
```

Example client configuration for all six connectors is in
[`config/mcp.example.json`](config/mcp.example.json). The API locations can be
overridden with environment variables such as
`HIGHLAND_SUPPORT_URL=http://relay:8103`.

## Docker Compose

On a machine with Docker:

```bash
docker compose up --build
```

This starts the complete scripted learning stack: the web workspace at
`http://localhost:3000`, the Highland API at `http://localhost:8080`, the
catalog, and all six mock systems. All mutable mock and platform state is
mounted beneath the single local `var/` directory. Once images have been
built, scripted mode does not require an internet connection.

MCP adapters remain supervised stdio subprocesses inside the API container;
they are deliberately not long-running Compose services.

To opt into real, potentially billable Cohere calls without changing source
code:

```bash
HIGHLAND_MODEL_BACKEND=cohere COHERE_API_KEY=... docker compose up --build
```

The backend selection is explicit and defaults to `scripted`. Stop the stack
with `docker compose down`; this preserves `var/`.

## Guided demo

The dataset is centered on **Northwind Bank**, whose production retrieval
latency increased after an index compaction. The facts are intentionally spread
across systems:

- Atlas CRM has the account, deployment ownership, and renewal context.
- Relay Desk has the open customer ticket.
- Beacon has the time series and incident.
- Archive has the relevant latency runbook and architecture notes.
- Pulse has internal discussion and the upcoming customer meeting.
- Track has the remediation task and unresolved follow-ups.

This makes the canonical prompt genuinely multi-source:

> Prepare me for the Northwind Bank meeting. Summarize their deployment,
> recent issues, unresolved actions, and suggested talking points, with
> evidence.

See [Architecture](docs/ARCHITECTURE.md), [Mock ecosystem](docs/MOCK_ECOSYSTEM.md),
[data contract](docs/DATA_CONTRACT.md), and
[showcase scenarios](docs/SCENARIOS.md) for the design and learning path.
The milestone-by-milestone delivery roadmap, implementation status, and
one-epic-per-commit working agreement are in the
[implementation plan](docs/IMPLEMENTATION_PLAN.md).

## Application foundation

Highland defaults to its explicit deterministic model backend. Copy
`.env.example` to `.env`, then inspect readiness and local model usage:

```bash
highland doctor
highland usage
```

Run the application API with `highland app`. Its platform state lives under
`var/highland/` and can be cleared without changing mock source-system records:

```bash
highland reset-platform-state
```

Set `HIGHLAND_MODEL_BACKEND=cohere` and `COHERE_API_KEY` only when live,
potentially billable model calls are intended. Model prices and effective dates
are explicit in `config/model_prices.json`; unknown production rates fail
closed once usage is observed and must be configured for the applicable
commercial agreement.

The default `pytest` suite is hermetic and non-billable. See
[Testing Highland](docs/TESTING.md) for the unit, local integration, scripted
acceptance, and explicitly opted-in live-model commands.
