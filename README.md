# Highland

Highland is a self-contained educational enterprise-agent workspace inspired by
the product shape of Cohere North: **Discover**, **Create**, and **Automate**.
It is not a clone and does not use Cohere branding or proprietary behavior.

The repository contains a realistic fictional enterprise called **Summit
Software** and six local “external” systems that can be reached through REST
APIs or MCP:

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

## Easiest start: one key

Install Docker, then run:

```bash
./bootstrap.sh
```

On Windows PowerShell, use `./bootstrap.ps1` for the same guided flow.

Paste a Cohere API key at the private prompt. That is the only configuration
the guided setup asks for. The script:

1. stores the key in the ignored local `.env` file with owner-only permissions;
2. selects the real Cohere model backend;
3. builds and starts the web app, API, six mock systems, and MCP connectors;
4. synchronizes all searchable fictional enterprise content; and
5. waits until the workspace is ready at <http://localhost:3000>.

The initial index build and model interactions are potentially billable.
Review the small learning-budget defaults shown in `.env.example` and set a
provider-side spending limit before experimenting. Re-running `./bootstrap.sh`
is safe: unchanged mock content is not embedded again.

### Access from a remote VPS

When Highland is running on a VPS, create an SSH tunnel from your local machine
instead of exposing the web and API ports directly to the internet:

```bash
ssh -N \
  -L 127.0.0.1:3000:127.0.0.1:3000 \
  -L 127.0.0.1:8080:127.0.0.1:8080 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=60 \
  USER@VPS_IP
```

Replace `USER@VPS_IP` with the same SSH destination and identity options you
normally use for the VPS. Keep the tunnel terminal open, then visit
<http://localhost:3000> on your local machine. The API documentation is
available at <http://localhost:8080/docs>. Both ports are forwarded because the
browser frontend calls the Highland API; the mock-service ports do not need to
be forwarded. Press `Ctrl+C` to close the tunnel.

For tunnel-only access, allow SSH through the VPS or provider firewall and block
public inbound access to the application and mock-service ports.

To see the same setup without a real model or API key, use deterministic
scripted mode:

```bash
docker compose up --build --detach --wait
```

Both paths populate the search index before the API and UI start. Scripted
responses are clearly simulated and are best for following the mechanics
offline; the one-key path is best for exploring real model behavior.

## Local Python setup

Python 3.11+ is required.

```bash
make setup
. .venv/bin/activate
highland-mocks generate
highland-mocks dev
```

`make setup` installs the checked-in development lock. Production containers
use the separate production lock, so Cohere, MCP, and their transitive
dependencies resolve consistently in local development, CI, and Docker.

## Debug the application

Highland includes a repeatable Python debugging environment. After installing
Docker, run this single command from the repository root:

```bash
make debug
```

The command installs the local development environment when necessary, starts
the web app and all synthetic enterprise services, synchronizes the search
index, and runs the API through `debugpy`. It uses the application backend
selected by `HIGHLAND_MODEL_BACKEND` in `.env`. A new setup defaults to
`scripted`; a setup configured by `bootstrap.sh` uses `cohere` and can make
billable embedding, rerank, and chat calls while debugging. The process pauses
before importing Highland and prints this message when it is ready:

```text
Highland is waiting for a debugger at 127.0.0.1:5678.
```

### Attach VS Code

1. Open the repository folder in VS Code. For a remote machine, open it with
   VS Code Remote - SSH so the editor extensions and debugger run beside the
   application.
2. Accept the workspace recommendations for Microsoft's Python and Python
   Debugger extensions. They are recorded in `.vscode/extensions.json`; if the
   recommendation prompt is dismissed, install extensions `ms-python.python`
   and `ms-python.debugpy` manually.
3. Set breakpoints in the Python source.
4. Open **Run and Debug**, select **Highland: Attach to make debug**, and press
   `F5`. The checked-in `.vscode/launch.json` connects to the waiting process
   and follows Python subprocesses, including the stdio MCP connectors.
5. Open <http://localhost:3000> and start a Discover run. VS Code stops when
   the request reaches a breakpoint.

Useful places to begin are:

- `src/highland/cli.py`, `main`: command-line entry point;
- `src/highland/cli_commands.py`, `run_app`: Uvicorn startup;
- `src/highland/api.py`, `create_app`: service composition;
- `src/highland/http/discover.py`, `create_run`: HTTP and background-task boundary;
- `src/highland/discover/service.py`, `chat`: retrieval and agent construction;
- `src/highland/retrieval/hybrid.py`, `HybridRetriever.search`: retrieval stages;
- `src/highland/runtime/agent.py`, `AgentLoop.run`: model and tool loop; and
- `src/highland_mocks/mcp_server.py`, `main`: connector subprocess entry point.

Use `F10` to step over, `F11` to step into, `Shift+F11` to step out, and `F5`
to continue. The **Highland: Debug meeting-preparation test** configuration is
also available for following one deterministic scenario without operating the
browser.

Disconnect the attached session in VS Code, then stop `make debug` with
`Ctrl+C` in its terminal. The launcher restores the normal containerized API
when the debug process exits.
Compose builds missing images automatically. After changing frontend code,
mock-service code, or locked production dependencies, force their images to be
rebuilt with `HIGHLAND_DEBUG_REBUILD=1 make debug`.
Override the configured backend for one debug session when needed:

```bash
HIGHLAND_DEBUG_MODEL_BACKEND=scripted make debug
HIGHLAND_DEBUG_MODEL_BACKEND=cohere make debug
```

The application-level scripted chat provider consumes finite response queues
supplied by deterministic tests and scenarios. It supports non-billable search
debugging, but an arbitrary interactive Discover prompt has no queued response
and intentionally fails. Use the Cohere backend when stepping through real LLM
responses; use **Highland: Debug meeting-preparation test** when stepping
through a deterministic scripted agent run.

Editors other than VS Code can use the same `make debug` command and attach any
Debug Adapter Protocol client to `127.0.0.1:5678`. Do not expose that debugger
port publicly. When the editor cannot run on the same host, forward the port
over SSH instead:

```bash
ssh -N -L 127.0.0.1:5678:127.0.0.1:5678 USER@VPS_IP
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

After the application is installed, reset both mock and platform state with
explicit confirmation:

```bash
highland reset --yes
```

The command prints every target before acting and never deletes checked-in
`data/seed/`. Preserve human-created artifacts, workflows, and traces around a
reset with:

```bash
highland backup export ./highland-learning-state.zip
highland backup import ./highland-learning-state.zip --replace
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

## Docker Compose details

On a machine with Docker:

```bash
docker compose up --build --detach --wait
```

This starts the complete scripted learning stack and synchronizes its search
index: the web workspace at
`http://localhost:3000`, the Highland API at `http://localhost:8080`, the
catalog, and all six mock systems. All mutable mock and platform state is
mounted beneath the single local `var/` directory. Once images have been
built, scripted mode does not require an internet connection.

MCP adapters remain supervised stdio subprocesses inside the API container;
they are deliberately not long-running Compose services.

For the smooth live-model setup, prefer `./bootstrap.sh`. The equivalent
non-interactive Compose command is:

```bash
HIGHLAND_MODEL_BACKEND=cohere COHERE_API_KEY=... \
  docker compose up --build --detach --wait
```

The backend selection is explicit and defaults to `scripted` when the launcher
has not created `.env`. Stop the stack with `docker compose down`; this
preserves `var/`.

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
Start with [Learn Highland by following a run](docs/LEARNING_PATH.md) for
executable Discover, Create, approval, and Automation tutorials plus a trace-to-code map.
Maintainers should use the [educational release checklist](docs/RELEASE.md).
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
