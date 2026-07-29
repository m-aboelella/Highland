# Highland architecture

Highland separates the educational system into three trust zones.

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Highland workspace                                                  │
│ UI ─ API ─ agent runtime ─ retrieval ─ artifacts ─ workflow engine  │
│                    │                                                 │
│                    ├─ approvals, policy, budgets, trace, audit        │
│                    └─ MCP client                                     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ stdio MCP
                 ┌─────────────┴─────────────┐
                 │ Connector adapters        │
                 │ typed tools + translation │
                 └─────────────┬─────────────┘
                               │ HTTP
       ┌───────────┬───────────┼───────────┬──────────┬───────────┐
       │ Atlas CRM │ Archive   │ Relay     │ Beacon   │ Pulse     │ Track
       │ :8101     │ :8102     │ :8103     │ :8104   │ :8105     │ :8106
       └───────────┴───────────┴───────────┴──────────┴───────────┘
```

The mock services are separate network applications even though they live in
one repository. This preserves important production-like behavior:

- connectors can fail independently;
- every call has serialization and network latency;
- systems use different record shapes;
- write tools can use idempotency keys;
- the platform must correlate records through stable IDs;
- policy is enforced before the MCP call, not buried in a prompt.

## Ownership boundaries

### Highland owns

- one local workspace and its visibility policy;
- conversations and persistent artifacts;
- the model/tool loop and model routing;
- indexed chunks, embeddings, hybrid retrieval, and reranking;
- approval requests and approval decisions;
- workflow definitions, versions, schedules, runs, and retries;
- event streaming, traces, token usage, and audit history.

### Mock systems own

- source records and source-specific identifiers;
- source APIs and their failure semantics;
- mutable tickets and project issues;
- canonical source URLs used in citations;
- their own update timestamps and actor histories.

### MCP adapters own

- small, atomic tool schemas;
- source authentication in a future milestone;
- conversion from source-specific response shapes to tool documents;
- stable tool errors that do not expose internal stack traces.

Adapters do not own workflow logic. A tool named
`prepare_northwind_briefing()` would hide the very behavior this project is
intended to teach.

## Self-contained does not mean monolithic

Everything runs on one machine and all persistent inputs are checked into this
repository, but localhost networking remains in the architecture. This lets a
learner use a packet trace, stop a connector, inject latency, or alter one
system’s data without changing the agent runtime.

## Planned platform layers

1. **Mock ecosystem** — delivered in the current foundation.
2. **Application foundation** — typed configuration, inspectable local state,
   capability-aware model providers, and usage budgets.
3. **Discover** — ingestion into local chunk sidecars and a derived FAISS
   vector index, hybrid retrieval, reranking, grounded chat, evidence viewer.
4. **Create** — persistent editable artifacts, claim/evidence checks, Markdown
   and PDF export.
5. **Agent runtime** — direct Cohere Chat v2 loop, tool registry, budgets,
   approval pauses, SSE trace.
6. **Automate** — versioned five-node workflow model, scheduler, branch, loop,
   run history.
7. **Evaluation** — deterministic retrieval tests and optional billable
   end-to-end model evaluations.

The baseline has no accounts, teams, roles, PostgreSQL, pgvector, or Redis.
Atomic files under `var/highland/` are the application source of truth; FAISS is
rebuildable derived state. Repository boundaries keep a future storage adapter
possible without making an external database part of the learning setup.

The mock ecosystem intentionally works before a model key exists. Model-backed
features should degrade to deterministic fixtures or be clearly marked as
unavailable, never silently call a paid API.
