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

## Implemented platform composition

`src/highland/services.py` is the composition root. `ApplicationServices`
constructs the configured Cohere or scripted provider, retrieval, repositories,
agent runtime, approvals, traces, artifacts, and workflows once. The HTTP API
and evaluation commands both use this object, so evaluation does not maintain a
second simplified platform.

`src/highland/api.py` registers four teaching surfaces—Discover and runs,
artifacts, workflows, and platform operations—without changing their public
URLs. Business behavior remains in the corresponding service modules. The
agent loop in `src/highland/runtime/agent.py` stays deliberately linear: model
call, validated tool request, policy and approval decision, tool result, and
final grounded response.

Each fictional source under `src/highland_mocks/systems/` owns its seed
fragment, REST routes, and FastMCP tool registration. Shared storage, errors,
identifiers, and reset behavior remain central. This vertical organization
makes one integration easy to trace without turning the sources into plugins.

The delivered layers are:

1. **Mock ecosystem** — six independently reachable fictional source systems
   with REST and atomic MCP tools.
2. **Application foundation** — typed settings, explicit model providers,
   usage budgets, local durable state, and shared composition.
3. **Discover** — ingestion, local filtering, BM25 and FAISS candidate search,
   reciprocal-rank fusion, Cohere or deterministic reranking, grounded chat,
   and evidence inspection.
4. **Create** — persistent editable artifacts, evidence checks, revision
   history, and Markdown/PDF export.
5. **Agent runtime** — direct Cohere Chat v2 tool loop, policy, durable approval
   pauses, MCP execution, SSE events, and trace history.
6. **Automate** — a bounded versioned workflow model, scheduler, branches,
   loops, retries, approvals, and run history.
7. **Evaluation** — the production Discover retriever and production agent and
   workflow orchestration, with deterministic checks and explicitly opted-in
   Cohere semantic grading.

Retrieval evaluation reads the reviewed cases in
`config/evaluation/retrieval-relevance.json`, calls `DiscoverService.search`,
and reports candidate recall, precision/recall at k, MRR, stage loss, leakage,
latency, model IDs, and corpus/index fingerprints. Scenario evaluation uses the
same MCP gateway, policy, approval, trace, and workflow constructors as HTTP;
write approvals are rejected during evaluation and are never executed.

The baseline has no accounts, teams, roles, PostgreSQL, pgvector, or Redis.
Atomic files under `var/highland/` are the application source of truth; FAISS is
rebuildable derived state. Repository boundaries keep a future storage adapter
possible without making an external database part of the learning setup.

The mock ecosystem intentionally works before a model key exists. Model-backed
features should degrade to deterministic fixtures or be clearly marked as
unavailable, never silently call a paid API.
