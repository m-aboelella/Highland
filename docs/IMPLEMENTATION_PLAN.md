# Highland implementation plan

Last revised: 2026-07-29
Plan baseline: `379208d` (`Build Highland mock enterprise foundation`)
Plan owner: repository maintainers and future Codex implementation sessions

This is the authoritative delivery plan for Highland. It is intentionally
structured so that a fresh Codex session can select one epic, implement it,
verify it, update this file, and create one focused commit.

## Status convention

| Marker | Meaning |
| --- | --- |
| ✅ Implemented | Code, tests, and documentation satisfy the epic's acceptance criteria |
| 🟨 Partial | Some code exists, but at least one acceptance criterion is unmet |
| ⬜ Not started | No implementation beyond planning or prerequisites |
| ⛔ Blocked | Work cannot proceed until the recorded blocker is resolved |
| 🚫 Out of scope | Deliberately excluded from the educational project |

An epic may only be changed to **Implemented** after its verification commands
pass. The implementing commit must update the epic's status and add its commit
hash to the `Implemented in` field. If the hash is not yet known while creating
the commit, use `Implemented in: this commit`; a later session should not create
a separate commit only to replace that phrase with the hash.

## Product boundary

Highland is a single-user, self-hosted educational workspace for learning how
an enterprise agent harness works. It should demonstrate the product shape of
Discover, Create, and Automate without copying Cohere North's branding or
proprietary implementation.

The finished project should let a learner:

1. Start the entire fictional enterprise environment on one machine.
2. Backfill and incrementally synchronize searchable source content.
3. Ask grounded questions across indexed text and live MCP tools.
4. Watch the model plan, retrieve evidence, invoke tools, and consume results.
5. Turn an answer into a cited, editable artifact.
6. Pause before an external write and resume after human approval.
7. build and run a small versioned automation.
8. Inspect traces, citations, usage, estimated cost, and failure behavior.
9. Run deterministic tests without an API key and optional integration tests
   against real Cohere models.

### Explicit non-goals

- 🚫 Multi-user accounts, login, invitations, organizations, or tenant isolation.
- 🚫 A production RBAC system. A single local workspace policy may restrict
  visible source categories to demonstrate filtering before model access.
- 🚫 PostgreSQL, pgvector, Redis, or another separately operated database in
  the baseline.
- 🚫 Real Slack, Jira, Gmail, CRM, or observability credentials and OAuth flows.
- 🚫 A general no-code platform or distributed workflow engine.
- 🚫 Production high availability, horizontal scaling, or distributed locking.
- 🚫 Reimplementing Cohere model intelligence with application heuristics.
- 🚫 Pretending that scripted test responses are real model output.

## Locked architectural decisions

Future sessions should treat these as defaults. Changing one requires a
documented architecture decision and an update to this plan before code changes.

### One local user, one local workspace

There is no account system. The application opens directly into one workspace.
A checked-in example configuration defines the workspace name, model choices,
allowed visibility labels, connector definitions, and cost limits. Secrets stay
in environment variables and are never checked in.

### No server database in the baseline

The initial platform uses inspectable local persistence:

| Data | Storage |
| --- | --- |
| Synthetic source-of-truth records | Checked-in JSON and Markdown under `data/seed/` |
| Mutable mock-system state | JSON under ignored `var/` |
| Index synchronization manifest | Atomic JSON file |
| Chunk text and metadata | JSON or JSONL sidecars |
| Vector index | FAISS files |
| Conversations and approval state | Atomic JSON documents |
| Run events, audit, and cost ledger | Append-only JSONL |
| Artifacts | Markdown plus JSON metadata |
| Workflow definitions and versions | JSON documents |

This is appropriate because Highland is single-user and normally runs one
application process. Repository classes must isolate storage details so SQLite
or PostgreSQL could be added later without changing the agent runtime.

Writes must use temporary files plus atomic replacement. Append-only logs must
be tolerant of a truncated final record. Runtime state must be resettable
without touching the checked-in seed.

### FAISS is a derived retrieval index

FAISS stores vectors; it is not the source of truth and not merely a response
cache. The index has two population modes:

1. **Initial backfill** enumerates eligible records, downloads their canonical
   content, chunks it, calls Cohere Embed, writes metadata, and builds FAISS.
2. **Incremental synchronization** compares stable source IDs, `updated_at`,
   and content hashes. It embeds only new or changed chunks and removes or
   tombstones deleted content.

The entire index can be deleted and rebuilt from the mock services. A failed
sync must leave the last completed index readable. Synchronization is explicit
through a CLI/API operation; a later optional mode may run it at startup.

### MCP versus indexed retrieval

The index is used to discover unstructured or semi-structured text by meaning.
MCP is used for canonical record lookup, fresh structured state, metrics, and
all actions.

| Mock system | Indexed content | MCP remains authoritative for |
| --- | --- | --- |
| Atlas CRM | None initially | Customer lookup, account tier, renewal, contacts, ownership |
| Archive | Document passages and metadata | Canonical document fetch and source download |
| Relay Desk | Ticket descriptions and comments | Current status, priority, complete ticket, ticket creation |
| Beacon | Incident summaries, symptoms, and resolution notes | Deployment topology, current incidents, time-series metrics |
| Pulse | Messages and completed meeting notes | Meeting schedule/details and posting customer updates |
| Track | Issue titles and descriptions | Current issue state and issue creation |

An indexed result is evidence for discovery, not proof that mutable fields are
current. Before asserting a time-sensitive status or taking action, the agent
should retrieve the canonical record through MCP. Metrics are never embedded.

Customer, source-type, date, and local visibility filtering happens before
chunks are passed to Cohere Rerank or Chat. FAISS may score vector identifiers
globally, but unauthorized or out-of-scope chunk text must never leave the
local filtering boundary.

### Cohere supplies real model intelligence

Highland uses normalized application contracts with two implementations:

| Capability | Real implementation | Deterministic implementation |
| --- | --- | --- |
| Chat, reasoning, structured generation, tool calls | Cohere Chat v2; default `command-a-plus-05-2026` | Scripted responses |
| Embeddings | Cohere Embed; default `embed-v4.0` | Stable deterministic vectors |
| Reranking | Cohere Rerank; default `rerank-v4.0-fast`, optional Pro | Scripted or deterministic ordering |

Real-mode model use is explicit and configurable:

| Work | Default |
| --- | --- |
| Main agent loop, tool selection, grounded answers, artifact drafting, workflow planning | `command-a-plus-05-2026` |
| Optional lightweight structured extraction/classification experiments | `command-r7b-12-2024` |
| Optional deep-investigation comparison | configured Command reasoning model or Command A+ |
| Document and query vectors | `embed-v4.0` |
| Interactive retrieval | `rerank-v4.0-fast` |
| High-quality report/evaluation comparison | `rerank-v4.0-pro` |

The first implementation should use Command A+ consistently before adding
model-routing experiments. Operation-specific routing may be configuration or a
model-assisted decision, but it must be visible in the trace and may not
silently substitute hard-coded business conclusions.

The application may deterministically enforce schemas, permissions, budgets,
approval rules, maximum steps, idempotency, and citation validity. It must not
hard-code intent classification, diagnoses, health assessments, tool selection,
or final prose that should come from a Cohere model.

The scripted implementation exists for unit tests, offline UI development, and
clearly labelled guided walkthroughs. It must never silently activate when a
real Cohere run was requested.

### Provider interfaces remain capability-aware

Use separate `ChatModel`, `EmbeddingModel`, and `RerankModel` contracts composed
into one configured provider. Normalized responses include tool calls,
citations, finish reason, usage, billed units when available, latency, and raw
provider identifiers. Capability flags prevent a future adapter from silently
dropping tool use, citations, structured output, reasoning, or vision.

### Local and provider-side cost controls

Every real model call records request count, model, input/output tokens where
available, rerank search units, latency, and estimated cost. The runtime
enforces configurable limits before starting another call:

```env
HIGHLAND_MONTHLY_BUDGET_USD=5.00
HIGHLAND_MAX_RUN_COST_USD=0.10
HIGHLAND_MAX_MODEL_CALLS_PER_RUN=10
HIGHLAND_MAX_RERANK_SEARCHES_PER_RUN=10
```

These are educational guardrails, not a financial guarantee. Documentation
must also recommend setting Cohere's dashboard spending limit. Model prices
must live in configuration with an `effective_date`, because provider pricing
changes.

## Data and control flow

```text
User
  -> Highland API
  -> agent runtime
       -> hybrid retrieval
            -> local lexical index
            -> local FAISS index
            -> Cohere Embed for the query
            -> local metadata filters
            -> Cohere Rerank
       -> Cohere Chat
            -> zero or more atomic MCP tool calls
                 -> local connector process
                 -> local mock REST service
            -> approval pause before write-capable tools
       -> grounded response and citations
  -> conversation, trace, cost ledger, and optional artifact
  -> SSE events to the UI
```

## Progress summary

| Milestone | Status | Implemented epics | Purpose |
| --- | --- | ---: | --- |
| M0 — Mock enterprise foundation | ✅ Implemented | 5/5 | Realistic local systems and MCP tools |
| M1 — Application and model-provider foundation | ✅ Implemented | 5/5 | Testable Cohere boundary and local state |
| M2 — Ingestion and hybrid retrieval | ✅ Implemented | 6/6 | Backfill, incremental sync, FAISS, reranking |
| M3 — Agent runtime and MCP gateway | ✅ Implemented | 6/6 | Direct model/tool loop, policy, approvals, traces |
| M4 — Discover workspace | ✅ Implemented | 6/6 | Grounded search/chat and evidence UI |
| M5 — Create artifacts | ✅ Implemented | 5/5 | Persistent editable cited outputs |
| M6 — Automate workflows | ✅ Implemented | 7/7 | Small model-assisted workflow system |
| M7 — Evaluation and reliability | ✅ Implemented | 5/5 | Deterministic and live-model evidence |
| M8 — Self-hosted learning experience | ⬜ Not started | 0/4 | One-command stack and teaching path |

---

# M0 — Mock enterprise foundation

**Milestone status:** ✅ Implemented
**Milestone outcome:** Six realistic local source systems can be queried through
REST or atomic MCP tools without a model key.

This milestone predates the one-epic/one-commit convention. It was delivered
coherently in commit `379208d`.

## E0.1 — Deterministic synthetic enterprise dataset

**Status:** ✅ Implemented
**Implemented in:** `379208d`

Delivered:

- Summit Software and Northwind-centered cross-system narrative.
- Stable prefixed IDs and customer joins.
- Generated JSON plus downloadable Markdown knowledge files.
- Repeatable seed generation and runtime reset.
- Provenance, visibility, timestamps, and fictional source URLs.

Verification:

```bash
pytest tests/test_seed.py
```

## E0.2 — Six mock REST systems

**Status:** ✅ Implemented
**Implemented in:** `379208d`

Delivered Atlas CRM, Archive, Relay Desk, Beacon, Pulse, and Track with
source-specific schemas, health endpoints, OpenAPI documentation, and a service
catalog.

Verification:

```bash
pytest tests/test_api.py
```

## E0.3 — Atomic MCP connector tools

**Status:** ✅ Implemented
**Implemented in:** `379208d`

Delivered one stdio MCP server per source system with small read/write tools and
stable error translation.

Verification:

```bash
pytest tests/test_mcp.py
```

## E0.4 — Stateful writes and reproducible failures

**Status:** ✅ Implemented
**Implemented in:** `379208d`

Delivered idempotent ticket, project-issue, and customer-update writes plus
latency and HTTP-failure injection.

Verification:

```bash
pytest tests/test_api.py
```

## E0.5 — Scenario specifications and local packaging

**Status:** ✅ Implemented
**Implemented in:** `379208d`

Delivered three scenario manifests, tool policy, Compose services, CLI,
architecture/data documentation, and Python packaging.

Verification:

```bash
ruff check .
pytest
```

---

# M1 — Application and model-provider foundation

**Milestone status:** ✅ Implemented
**Milestone outcome:** Highland has a runnable application core whose model
dependencies can be switched explicitly between deterministic test doubles and
real Cohere adapters.

## E1.1 — Application package, configuration, and local workspace layout

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** M0

Scope:

- Add a `highland` application package without merging it into
  `highland_mocks`.
- Add typed settings for paths, connector commands, model IDs, provider mode,
  timeouts, step limits, and budgets.
- Define `var/highland/` subdirectories for indexes, conversations, runs,
  artifacts, workflows, and synchronization state.
- Add `highland app`, `highland doctor`, and `highland reset-platform-state`
  commands. Reset must not reset mock source systems unless explicitly asked.
- Add `.env.example` without secrets.
- Update architecture documentation to remove PostgreSQL/pgvector, Redis,
  multi-user, teams, and roles from the baseline.

Acceptance criteria:

- `highland doctor` reports filesystem readiness, connector executables, mock
  service reachability, selected provider mode, and whether a Cohere key exists
  without printing it.
- Importing application settings has no network side effects.
- Platform state can be created and reset in a temporary directory in tests.

Verification:

```bash
pytest tests/unit/test_settings.py tests/unit/test_workspace_paths.py
highland doctor
ruff check .
```

Suggested commit:

```text
Add Highland application configuration and local workspace
```

## E1.2 — Normalized model contracts and capability declarations

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.1

Scope:

- Define separate async `ChatModel`, `EmbeddingModel`, and `RerankModel`
  protocols.
- Define provider-neutral requests/responses for messages, documents, tool
  definitions, tool calls, citations, vectors, ranked results, usage, and
  provider errors.
- Preserve provider request IDs and optional raw response data for trace
  debugging.
- Declare capabilities for tools, citations, structured output, reasoning,
  vision, and streaming.
- Validate unsupported capability combinations at startup.

Acceptance criteria:

- The runtime-facing package does not import the Cohere SDK.
- Contract serialization round-trips without losing tool calls or citations.
- An adapter that lacks a required capability fails explicitly.

Verification:

```bash
pytest tests/unit/models/test_contracts.py
ruff check .
```

Suggested commit:

```text
Define capability-aware model provider contracts
```

## E1.3 — Deterministic scripted model provider

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.2

Scope:

- Implement queued/scripted Chat responses, deterministic local embeddings, and
  scripted reranking.
- Record every request for assertions.
- Support streaming fragments, tool calls, structured output, citations,
  artificial latency, rate-limit errors, timeouts, and malformed responses.
- Fail tests when an unexpected model call occurs.
- Add named offline scripts for the three showcase scenarios, labelled
  `simulated` in all emitted metadata.

Acceptance criteria:

- Unit tests never require network access or an API key.
- The same application interfaces work with scripted and real providers.
- Script exhaustion is an explicit test failure, not an empty model response.
- No keyword router or canned answer is used outside scripted mode.

Verification:

```bash
pytest tests/unit/models/test_scripted_provider.py
```

Suggested commit:

```text
Add deterministic model provider for tests and offline demos
```

## E1.4 — Real Cohere model provider

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.2

Scope:

- Add the Cohere Python SDK v2.
- Implement Chat v2, Embed v4, and Rerank v4 adapters.
- Map Highland tool schemas, tool calls, structured responses, citations,
  streaming events, usage, request IDs, and Cohere errors.
- Make all model IDs configurable; default to Command A+, Embed 4, and Rerank 4
  Fast.
- Add explicit retry classification. Retry transient failures only and retain
  the original logical call ID.
- Mark live tests `integration` and skip them unless a Cohere key and an
  explicit opt-in flag are present.

Acceptance criteria:

- No real call happens when `HIGHLAND_MODEL_BACKEND=scripted`.
- A live smoke test exercises one Chat, one Embed, and one Rerank request.
- Secrets and full provider responses containing sensitive prompt text are not
  logged by default.

Verification:

```bash
pytest tests/unit/models/test_cohere_mapping.py
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... pytest -m integration \
  tests/integration/test_cohere_provider.py
```

Suggested commit:

```text
Implement Cohere chat embedding and rerank adapters
```

## E1.5 — Usage accounting and fail-closed model budgets

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.2, E1.3

Scope:

- Add dated model-price configuration.
- Normalize tokens and billed search units from provider responses.
- Append usage entries to a local JSONL cost ledger.
- Enforce per-run call, rerank, token, and estimated-dollar limits before the
  next request.
- Expose unknown pricing explicitly; do not silently calculate it as zero.
- Include a budget-exceeded model error suitable for UI display.

Acceptance criteria:

- Tests prove a runaway scripted loop stops at each configured boundary.
- Cost ledger writes are append-only and redact prompts and keys.
- `highland usage` summarizes the current run and calendar month.

Verification:

```bash
pytest tests/unit/models/test_budgets.py tests/unit/storage/test_cost_ledger.py
```

Suggested commit:

```text
Track model usage and enforce local spending budgets
```

---

# M2 — Ingestion and hybrid retrieval

**Milestone status:** ✅ Implemented
**Milestone outcome:** Highland maintains a rebuildable FAISS-backed index,
combines semantic and lexical discovery, and reranks eligible evidence with
Cohere.

## E2.1 — Canonical chunk and synchronization manifest contracts

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.1

Scope:

- Define canonical source document, chunk, provenance, filter, and sync-record
  schemas.
- Preserve source system, record ID, passage/section, customer, source type,
  visibility, timestamps, and exact source URL.
- Use deterministic chunk IDs derived from source ID, content hash, and chunk
  ordinal.
- Define manifest states for completed syncs, partial failures, and tombstones.
- Add semantic chunking rules by source type rather than fixed-size splitting
  alone.

Acceptance criteria:

- Identical source content produces identical chunk IDs.
- Updated text changes only affected chunk identities.
- Every chunk can resolve to a canonical source record and display location.

Verification:

```bash
pytest tests/unit/retrieval/test_chunk_contracts.py
```

Suggested commit:

```text
Define retrieval chunks provenance and sync manifests
```

## E2.2 — Source backfill through MCP

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E2.1, E3.1

Scope:

- Enumerate eligible content from Archive, Relay, Beacon incidents, Pulse, and
  Track through connector tools.
- Fetch canonical content and transform it into normalized documents/chunks.
- Add `highland index backfill --source ...` and `--all`.
- Record per-source counts, failures, start/end timestamps, and source cursors
  where available.
- Build into a staging directory and atomically promote only a completed index.

Acceptance criteria:

- A backfill from freshly generated mock data is deterministic before embedding.
- One unavailable connector produces a visible partial result without
  corrupting the previous index.
- CRM records and metric samples are not embedded.

Verification:

```bash
pytest tests/integration/test_index_backfill.py
```

Suggested commit:

```text
Backfill searchable source content through MCP
```

## E2.3 — Incremental synchronization and rebuild

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E2.2

Scope:

- Add `highland index sync`, `status`, and `rebuild`.
- Compare source ID, update timestamp, and content hash.
- Embed only changed/new chunks and remove or tombstone deleted chunks.
- Preserve the last usable index if embedding or source access fails.
- Expose sync results through an application endpoint for the future UI.

Acceptance criteria:

- A no-change sync makes zero embedding calls.
- Updating one ticket causes only its affected chunks to be re-embedded.
- Rebuild after deleting all index files reproduces equivalent searchable
  content.

Verification:

```bash
pytest tests/integration/test_index_incremental_sync.py
```

Suggested commit:

```text
Add incremental index synchronization and safe rebuilds
```

## E2.4 — Cohere embeddings and FAISS persistence

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.3, E1.4, E2.1

Scope:

- Add FAISS and persist the vector index plus a chunk-ID mapping.
- Batch document embeddings using Cohere `input_type=search_document`.
- Embed queries using `input_type=search_query`.
- Validate dimensions and selected embedding model against index metadata.
- Refuse to mix vectors from different model IDs or dimensions.
- Support deterministic vectors for unit tests.

Acceptance criteria:

- Index close/reopen preserves nearest-neighbor results.
- Changing the embedding model requires a rebuild.
- Empty, oversized, and failed embedding batches have explicit behavior.

Verification:

```bash
pytest tests/unit/retrieval/test_faiss_store.py
pytest tests/integration/test_real_embedding_index.py -m integration
```

Suggested commit:

```text
Persist Cohere embeddings in a local FAISS index
```

## E2.5 — Hybrid retrieval, filters, and Cohere reranking

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E2.4

Scope:

- Add a lightweight local lexical scorer over chunk text and metadata.
- Retrieve semantic and lexical candidates and fuse them using documented
  reciprocal-rank fusion.
- Filter customer, source type, date, and allowed visibility before sending
  chunk text to Rerank.
- Retrieve approximately 30 candidates and rerank the eligible set to 5–10.
- Support Fast and Pro rerank selection by configuration/run purpose.
- Record retrieval, filter, and rerank timings and billed search units.

Acceptance criteria:

- Exact identifiers remain discoverable lexically.
- Semantically related language is discoverable through FAISS.
- Another customer's text never reaches Rerank for a customer-scoped query.
- Results include enough provenance to render exact evidence.

Verification:

```bash
pytest tests/unit/retrieval/test_hybrid_search.py
pytest tests/integration/test_customer_isolation.py
```

Suggested commit:

```text
Combine lexical and semantic retrieval with Cohere reranking
```

## E2.6 — Citation resolution and retrieval diagnostics

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E2.5

Scope:

- Resolve model citation document IDs and spans to chunk text, source record,
  section, and exact source URL.
- Add retrieval diagnostics showing lexical rank, semantic rank, fused score,
  filter decisions, and rerank score.
- Add an API to inspect a chunk without exposing arbitrary filesystem paths.
- Define behavior for stale citations after synchronization.

Acceptance criteria:

- Every rendered citation either resolves or is visibly marked unresolved.
- The diagnostics explain why each final chunk was selected.
- Citation resolution cannot escape the configured index directory.

Verification:

```bash
pytest tests/unit/retrieval/test_citations.py
```

Suggested commit:

```text
Resolve exact citations and expose retrieval diagnostics
```

---

# M3 — Agent runtime and MCP gateway

**Milestone status:** ✅ Implemented
**Milestone outcome:** A direct, inspectable Cohere model/tool loop can use local
MCP tools, enforce policy, pause for approval, and stream a durable trace.

## E3.1 — MCP process manager and tool discovery

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.1, M0

Scope:

- Read connector definitions from Highland configuration.
- Spawn and supervise local stdio MCP servers.
- Discover tool schemas and qualify tool names to prevent connector collisions.
- Add per-connector startup, request, and shutdown timeouts.
- Normalize MCP results and errors without hiding source-system context.
- Implement clean shutdown so development runs do not leave orphan processes.

Acceptance criteria:

- All six connectors and their expected tools are discoverable.
- One unavailable connector does not prevent read-only use of healthy
  connectors.
- Tool schemas can be converted to the model-provider tool contract.

Verification:

```bash
pytest tests/integration/test_mcp_gateway.py
```

Suggested commit:

```text
Add MCP connector supervision and tool discovery
```

## E3.2 — Tool registry, local workspace policy, and argument validation

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.1

Scope:

- Load `config/tool_policy.json` and merge it with discovered tools.
- Classify tools as read/write, approval-required, and idempotent.
- Validate model arguments against tool schemas before execution.
- Enforce allowed customers and visibility scope from the active run.
- Generate stable idempotency keys from run and logical step IDs.
- Record rejected calls as trace events without executing them.

Acceptance criteria:

- Model output cannot bypass approval by changing a tool alias.
- Invalid arguments return a bounded tool error the model may correct.
- Retrying an approved logical write reuses its idempotency key.

Verification:

```bash
pytest tests/unit/runtime/test_tool_policy.py
```

Suggested commit:

```text
Validate MCP tool calls against Highland policy
```

## E3.3 — Direct multi-step Cohere agent loop

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.3, E1.4, E1.5, E3.2

Scope:

- Build the model/tool execution loop directly instead of adopting an agent
  framework.
- Store local, versioned agent profiles containing instructions, configured
  model, allowed tools, retrieval defaults, and run budgets. Seed one general
  Highland agent; specialized profiles are configuration, not hard-coded
  decision logic.
- Supply system instructions, conversation context, retrieved documents, and
  available tools to Chat.
- Execute zero, one, or parallel read tool calls and return normalized results
  to the model.
- Bound steps, calls, wall time, tool-result size, and model context.
- Let Cohere choose tools and synthesize results; deterministic code only
  enforces control boundaries.
- Persist enough state to resume or replay a run.

Acceptance criteria:

- Scripted tests cover no-tool, one-tool, multiple-tool, corrective retry, and
  maximum-step paths.
- Every tool result given back to the model is represented in the trace.
- A final response records finish reason, usage, and citations.

Verification:

```bash
pytest tests/unit/runtime/test_agent_loop.py
```

Suggested commit:

```text
Implement the direct Cohere model and tool execution loop
```

## E3.4 — Durable approval pause and resume

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.2, E3.3

Scope:

- Persist approval requests with run ID, tool, validated arguments, reason,
  preview, idempotency key, and expiry.
- Pause without calling a write tool.
- Support approve and reject decisions through application APIs.
- Resume the same logical run after approval and include the decision in model
  context.
- Make repeat approvals and resumes idempotent.

Acceptance criteria:

- Process restart between request and decision does not lose the approval.
- Rejection never calls the external tool.
- Approval executes exactly one logical write, even if resume is retried.

Verification:

```bash
pytest tests/integration/test_approval_resume.py
```

Suggested commit:

```text
Persist approval checkpoints and resume agent runs safely
```

## E3.5 — Run event schema, trace storage, and SSE

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.3

Scope:

- Define versioned events for run lifecycle, retrieval, model calls, model
  deltas, tool calls/results, approvals, citations, usage, errors, and final
  output.
- Append events to JSONL and recover safely from a truncated final line.
- Stream the same event schema over Server-Sent Events.
- Redact keys and configured sensitive fields.
- Add run summary and trace replay endpoints.

Acceptance criteria:

- Replaying persisted events reconstructs the visible run state.
- SSE reconnect with a last-event ID resumes without duplicating events.
- Trace failures cannot execute a tool twice.

Verification:

```bash
pytest tests/unit/runtime/test_events.py
pytest tests/integration/test_sse_reconnect.py
```

Suggested commit:

```text
Stream and persist replayable agent run events
```

## E3.6 — Deployment-investigation runtime acceptance

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.4, E3.5, M2

Scope:

- Run the deployment-issue scenario with real retrieval and mock MCP systems.
- Have Cohere investigate metrics and evidence and prepare a ticket.
- Demonstrate the approval pause, ticket preview, approved write, and
  idempotent replay.
- Keep the suspected compaction cause explicitly uncertain.
- Add a deterministic scripted acceptance test plus an optional live-model
  test.

Acceptance criteria:

- All required source categories appear in the trace.
- `create_ticket` is absent before approval and called once after approval.
- The final response cites the runbook and observed metrics.

Verification:

```bash
pytest tests/acceptance/test_deployment_investigation.py
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... pytest -m live_scenario \
  tests/live/test_deployment_investigation.py
```

Suggested commit:

```text
Validate the approved deployment investigation scenario
```

---

# M4 — Discover workspace

**Milestone status:** ✅ Implemented
**Milestone outcome:** A learner can search and chat across enterprise
knowledge, see exact sources, and inspect how the answer was produced.

## E4.1 — Conversation persistence and application APIs

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.1, E3.5

Scope:

- Persist conversations and messages as local JSON.
- Add create/list/get/rename/delete conversation APIs.
- Add a run endpoint and SSE endpoint tied to a user message.
- Preserve follow-up context with explicit context-window management.
- Use generated IDs and atomic writes; no user or tenant fields.

Acceptance criteria:

- Conversations survive application restart.
- Follow-up runs reference prior messages and prior cited sources.
- Deleting a conversation does not delete source data or artifacts.

Verification:

```bash
pytest tests/integration/test_conversation_api.py
```

Suggested commit:

```text
Persist single-workspace conversations and chat runs
```

## E4.2 — Grounded search and chat orchestration

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** M2, E3.3, E4.1

Scope:

- Add explicit Search mode and conversational Discover mode.
- Retrieve obvious indexed context before the first model call.
- Allow the model to use MCP for fresh structured facts and canonical records.
- Pass reranked chunks as Cohere documents and preserve returned citation spans.
- Keep answer claims separated from evidence and label unresolved evidence.
- Add source/customer/date/type filters to requests.

Acceptance criteria:

- Search can return evidence without invoking Chat.
- Chat answers contain resolvable inline citations.
- Follow-up questions retain context without bypassing current filters.

Verification:

```bash
pytest tests/acceptance/test_discover_api.py
```

Suggested commit:

```text
Add grounded enterprise search and conversational discovery
```

## E4.3 — North-inspired but original web workspace shell

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E4.1

Scope:

- Add a TypeScript/React workspace, using Next.js unless an ADR documents a
  simpler alternative.
- Add original Highland visual identity and avoid Cohere trademarks or copied
  layouts/assets.
- Build left navigation for New chat, Search, Artifacts, Agents, and
  Automations.
- Add a basic Agents page for inspecting/selecting local agent profiles and
  their tools, model, instructions, and budgets. Agent editing may remain a
  later enhancement unless required by a showcase scenario.
- Build a top bar for active agent, connector health, model mode, index status,
  and run status.
- Make the layout usable on common laptop sizes.

Acceptance criteria:

- The shell starts without a Cohere key in scripted mode.
- Connector and index health are visible.
- No multi-user controls appear.

Verification:

```bash
npm --prefix web test
npm --prefix web run build
```

Suggested commit:

```text
Add the Highland single-workspace web shell
```

## E4.4 — Streaming conversation and visible execution trace

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.5, E4.3

Scope:

- Render streaming answer deltas from SSE.
- Render a concise trace of retrieval, model, MCP, error, approval, and final
  events.
- Add expandable raw-but-redacted event details.
- Support cancellation and reconnect.
- Clearly label scripted versus Cohere-backed runs.

Acceptance criteria:

- Refresh during a run reconnects or replays persisted state.
- Tool arguments/results and timings are inspectable.
- Cancellation prevents new model/tool steps and records the outcome.

Verification:

```bash
npm --prefix web test
pytest tests/acceptance/test_streaming_trace.py
```

Suggested commit:

```text
Render streaming answers and inspectable execution traces
```

## E4.5 — Sources, filters, and “Why this answer?”

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E2.6, E4.2, E4.4

Scope:

- Render inline citation markers linked to an evidence side panel.
- Show exact passage, section, source, timestamp, customer, and canonical URL.
- Add customer/source/date/type filters.
- Add “Why this answer?” diagnostics using retrieval and rerank evidence, not a
  second invented explanation.
- Show when a mutable fact was refreshed through MCP.

Acceptance criteria:

- Clicking a citation selects its exact supporting passage.
- Filter state is visible and carried into follow-up requests.
- Unsupported claims are visually distinguishable from cited claims.

Verification:

```bash
npm --prefix web test
pytest tests/acceptance/test_evidence_panel.py
```

Suggested commit:

```text
Add source filters citations and answer diagnostics
```

## E4.6 — Customer-meeting preparation acceptance

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E4.5

Scope:

- Execute the meeting-preparation scenario end to end.
- Cover CRM, meeting, support, deployment, incident, runbook, message, and
  project evidence.
- Produce structured deployment, issues, unresolved actions, and talking
  points sections.
- Add a deterministic acceptance test and an optional live Cohere evaluation.

Acceptance criteria:

- Required claims are cited to expected source records.
- The suspected root cause remains a hypothesis.
- No write tool is offered or called.
- No Alpine or Lumon-only evidence leaks into the result.

Verification:

```bash
pytest tests/acceptance/test_meeting_preparation.py
```

Suggested commit:

```text
Validate the grounded customer meeting preparation flow
```

---

# M5 — Create artifacts

**Milestone status:** ✅ Implemented
**Milestone outcome:** A chat result can become a persistent, editable,
evidence-backed document without regenerating the whole artifact.

## E5.1 — Artifact domain, file repository, and APIs

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.1, E4.1

Scope:

- Store Markdown content plus JSON metadata, citations, revision number, and
  originating conversation/run.
- Add create/list/get/update/delete and revision-history APIs.
- Use optimistic revision checks to prevent accidental overwrite.
- Support artifact types: briefing, incident report, rollout plan, executive
  summary, comparison, and proposal.

Acceptance criteria:

- Artifacts survive restart and remain human-readable on disk.
- Concurrent stale updates are rejected.
- An artifact links back to its originating conversation and run.

Verification:

```bash
pytest tests/integration/test_artifact_repository.py
```

Suggested commit:

```text
Persist versioned Markdown artifacts in the local workspace
```

## E5.2 — Cohere-backed artifact generation and section revision

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E5.1, E4.2

Scope:

- Generate structured artifacts from a selected answer and its evidence.
- Use Cohere structured output for section plans and cited content.
- Revise a selected section while preserving unaffected sections.
- Convert retrieved structured facts to Markdown tables through model output
  validated against a schema.
- Preserve or explicitly invalidate citations during edits.

Acceptance criteria:

- Production artifact prose comes from Cohere, not templates containing final
  claims.
- Section editing does not silently regenerate other sections.
- Every carried citation resolves to stored evidence.

Verification:

```bash
pytest tests/unit/artifacts/test_generation.py
pytest tests/integration/test_artifact_model_flow.py -m integration
```

Suggested commit:

```text
Generate and revise cited artifacts with Cohere
```

## E5.3 — Split conversation and artifact editor UI

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E5.1, E5.2, E4.3

Scope:

- Add “Turn into artifact” from a completed answer.
- Add split conversation/editor mode.
- Support Markdown editing, section selection, save state, revisions, and
  citation insertion.
- Show artifact provenance and a path back to the originating run.

Acceptance criteria:

- Manual edits persist without a model call.
- Model-assisted section edits show a preview before replacement.
- Unsaved changes are protected during navigation.

Verification:

```bash
npm --prefix web test
```

Suggested commit:

```text
Add the split conversation and artifact editing experience
```

## E5.4 — Claim and evidence coverage checks

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E5.2

Scope:

- Ask Cohere for structured claim extraction and proposed evidence mappings.
- Deterministically verify that referenced citations exist and their spans are
  valid.
- Mark supported, weakly supported, unsupported, and stale claims.
- Never present the model's support classification as a mathematical guarantee.

Acceptance criteria:

- Invented citation IDs are rejected.
- Uncited factual claims are visible in the editor.
- The check can be rerun after manual edits.

Verification:

```bash
pytest tests/unit/artifacts/test_evidence_coverage.py
```

Suggested commit:

```text
Highlight artifact claims without valid supporting evidence
```

## E5.5 — Markdown and PDF export

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E5.1, E5.4

Scope:

- Export canonical Markdown with a source appendix.
- Add a reproducible local PDF renderer packaged in the container.
- Preserve tables, headings, citation labels, and generated timestamp.
- Sanitize filenames and rendered HTML.

Acceptance criteria:

- Export works offline after the artifact exists.
- PDF generation does not load remote resources.
- Exported citation labels resolve to source details in the document.

Verification:

```bash
pytest tests/integration/test_artifact_export.py
```

Suggested commit:

```text
Export Highland artifacts to Markdown and PDF
```

---

# M6 — Automate workflows

**Milestone status:** ✅ Implemented
**Milestone outcome:** A learner can use Cohere to draft a small workflow,
review its explicit plan, run it deterministically, and inspect each model and
tool step.

## E6.1 — Versioned workflow schema and local repository

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.1

Scope:

- Define Trigger, Retrieve, Tool, Generate, and Approval nodes.
- Support sequential edges, one conditional branch, and one loop over records.
- Define node inputs/outputs with explicit typed references.
- Store immutable published versions plus an editable draft in JSON.
- Validate graph reachability, cycles, missing references, and approval
  placement.

Acceptance criteria:

- Invalid graphs fail before publication or execution.
- Published versions never change in place.
- The format remains readable without running Highland.

Verification:

```bash
pytest tests/unit/workflows/test_schema.py
```

Suggested commit:

```text
Define versioned five-node Highland workflows
```

## E6.2 — Natural-language workflow drafting with Cohere

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.4, E6.1, E3.2

Scope:

- Ask Cohere to convert a user goal into the workflow schema using structured
  output.
- Supply actual tool definitions and policies to the planner.
- Validate and repair schema errors through a bounded model interaction.
- Require human plan review before saving or publishing.
- Record planner model, usage, and generated rationale.

Acceptance criteria:

- Application code does not classify the request into a canned workflow.
- The model cannot publish or execute its own plan.
- Write tools always receive an approval node before validation succeeds.

Verification:

```bash
pytest tests/unit/workflows/test_model_planner.py
```

Suggested commit:

```text
Draft validated workflow plans with Cohere structured output
```

## E6.3 — Sequential workflow executor

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E6.1, M2, E3.2, E3.5

Scope:

- Execute Manual Trigger, Retrieve, Generate, read-only Tool, and Approval
  nodes sequentially.
- Use Cohere for every Generate node.
- Persist node inputs, outputs, status, attempts, timing, and usage.
- Enforce workflow-level model/tool/time budgets.
- Resume from the last durable node after restart.

Acceptance criteria:

- A completed node is not repeated during resume unless explicitly configured.
- Node output references resolve deterministically.
- A failure produces a bounded, inspectable run rather than a stuck process.

Verification:

```bash
pytest tests/unit/workflows/test_sequential_executor.py
```

Suggested commit:

```text
Execute and resume sequential Highland workflows
```

## E6.4 — Conditional branch and record loop

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E6.3

Scope:

- Add one schema-validated conditional expression over prior structured output.
- Add a bounded loop over a list of records.
- Allow Cohere to produce structured classifications used by deterministic
  branch expressions.
- Cap iterations and aggregate per-record outputs with provenance.

Acceptance criteria:

- Expressions cannot execute arbitrary Python or shell code.
- Loop records remain customer-isolated.
- Maximum iterations stop oversized model-generated collections.

Verification:

```bash
pytest tests/unit/workflows/test_branch_and_loop.py
```

Suggested commit:

```text
Add safe conditional and loop execution to workflows
```

## E6.5 — Workflow approvals, write tools, and scheduling

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.4, E6.4

Scope:

- Reuse the runtime approval system for workflow write nodes.
- Add manual and simple local schedule triggers.
- Use APScheduler only in-process; persist the schedule definition and next
  intended run time.
- Recover missed schedules conservatively after downtime without duplicating
  non-idempotent work.
- Require publication before scheduled execution.

Acceptance criteria:

- No scheduled draft can run.
- Write nodes cannot execute before the linked approval.
- Restart does not duplicate an already-started scheduled run.

Verification:

```bash
pytest tests/integration/test_workflow_approval_and_schedule.py
```

Suggested commit:

```text
Add approved write steps and local workflow schedules
```

## E6.6 — Workflow builder, testing, and run-history UI

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E6.2, E6.5, E4.3

Scope:

- Add natural-language draft creation and explicit plan review.
- Add a small node/edge editor; do not build a general no-code canvas.
- Show draft versus published version, model selection, estimated budget, test
  run, publish action, and version history.
- Stream and inspect workflow runs using the common trace event schema.
- Surface approval requests in the same right-side panel as agent runs.

Acceptance criteria:

- Users can inspect every node configuration before publication.
- Test runs cannot silently publish or schedule the workflow.
- Run history links to exact workflow version and trace.

Verification:

```bash
npm --prefix web test
pytest tests/acceptance/test_workflow_builder.py
```

Suggested commit:

```text
Add workflow planning testing and run history UI
```

## E6.7 — Weekly customer-health workflow acceptance

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E6.6

Scope:

- Implement the weekly health scenario using a customer loop.
- Retrieve live commercial, deployment, support, incident, and project data.
- Use Cohere to classify each account with structured rationale and evidence.
- Generate a cited summary table and saved weekly-report artifact.
- Demonstrate manual and scheduled execution.

Acceptance criteria:

- All active enterprise customers appear exactly once.
- Expected classifications are achieved or evaluation reports explain the
  evidence-backed deviation.
- Evidence never crosses customer-loop boundaries.
- The run records per-account and total model usage.

Verification:

```bash
pytest tests/acceptance/test_weekly_customer_health.py
```

Suggested commit:

```text
Validate the scheduled weekly customer health workflow
```

---

# M7 — Evaluation and reliability

**Milestone status:** ✅ Implemented
**Milestone outcome:** Learners can distinguish deterministic harness
correctness from probabilistic live-model quality and reproduce failures.

## E7.1 — Test taxonomy and hermetic default suite

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** M3

Scope:

- Organize unit, integration-local, acceptance-scripted, and live-model tests.
- Register pytest markers and fail on unknown markers.
- Make the default `pytest` suite hermetic and non-billable.
- Add one canonical command for each layer.
- Document when Docker and a Cohere key are required.

Acceptance criteria:

- `pytest` never performs an external network request.
- Live tests require both a key and explicit opt-in.
- Test output states whether a run was scripted or model-backed.

Verification:

```bash
pytest
```

Suggested commit:

```text
Separate hermetic harness tests from billable model evaluations
```

## E7.2 — Deterministic retrieval and source-isolation evaluation

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** M2

Scope:

- Derive query/evidence cases from scenario manifests.
- Measure candidate recall before reranking and top-k recall after reranking.
- Test exact-ID, paraphrase, filter, stale-record, and customer-isolation cases.
- Store JSON and Markdown reports under an ignored reports directory.

Acceptance criteria:

- Failures identify missing evidence IDs and the retrieval stage that lost them.
- Customer leakage is a hard failure rather than a quality score.
- Deterministic-provider evaluation can run without Cohere.

Verification:

```bash
highland eval retrieval
```

Suggested commit:

```text
Evaluate retrieval recall and customer isolation
```

## E7.3 — Live Cohere scenario evaluation

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.6, E4.6, E6.7

Scope:

- Execute the three scenario manifests with real Cohere calls.
- Grade required source coverage, expected claims, citation validity, forbidden
  behavior, tool selection, approval compliance, and final structure.
- Use Cohere structured judging only for semantic dimensions; keep policy and
  citation validity deterministic.
- Persist model IDs, prompts/version, trace IDs, usage, and report artifacts.

Acceptance criteria:

- One scenario can be run independently.
- Reports separate deterministic failures from model-judged scores.
- Repeated runs do not mutate external mock state unless the scenario explicitly
  approves a write.

Verification:

```bash
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... \
  highland eval scenario customer-meeting-preparation
HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... highland eval all
```

Suggested commit:

```text
Add billable end-to-end Cohere scenario evaluations
```

## E7.4 — Failure matrix, retries, and trace replay

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E3.5, E7.1

Scope:

- Exercise connector unavailable, injected 429/500/503, latency timeout,
  malformed model output, model rate limit, budget stop, corrupted final log
  line, and restart during approval.
- Document retry/non-retry decisions.
- Add trace replay that never re-executes model or tool calls.
- Add an explicit re-run operation that creates a new run linked to the old
  one.

Acceptance criteria:

- Each failure ends in a documented terminal or resumable state.
- Replay is read-only.
- Write retries retain idempotency.

Verification:

```bash
pytest tests/reliability
```

Suggested commit:

```text
Test failure recovery retries and read-only trace replay
```

## E7.5 — Cost reports and regression budgets

**Status:** ✅ Implemented
**Implemented in:** this commit
**Depends on:** E1.5, E7.3

Scope:

- Report model calls, tokens, rerank units, estimated dollars, and latency by
  scenario, run, node, and model.
- Add configurable warning and hard budgets to live evaluation.
- Compare a run with a stored baseline without treating current prices as
  permanent.
- Document the approximate cost of the three showcase scenarios.

Acceptance criteria:

- An evaluation aborts before its next call when its hard budget is reached.
- Unknown prices are visible in the report.
- No report contains API keys or full private prompt content by default.

Verification:

```bash
pytest tests/unit/evaluation/test_cost_reports.py
```

Suggested commit:

```text
Report scenario cost and enforce evaluation budgets
```

---

# M8 — Self-hosted learning experience

**Milestone status:** ⬜ Not started
**Milestone outcome:** A new learner can start Highland, follow the three
showcase scenarios, inspect the implementation, and reset everything locally.

## E8.1 — Full-stack container packaging

**Status:** ⬜ Not started
**Implemented in:** —
**Depends on:** M4, M5, M6

Scope:

- Extend Compose with the Highland API and web workspace.
- Package FAISS and PDF dependencies reproducibly.
- Add health checks and service startup ordering.
- Mount all runtime state beneath one documented local directory.
- Keep MCP connectors as supervised local processes unless a transport ADR
  changes the decision.

Acceptance criteria:

- `docker compose up --build` starts the complete stack.
- Scripted mode works without internet access after images are built.
- Real mode requires only a Cohere key and an explicit backend selection.

Verification:

```bash
docker compose up --build
highland doctor
```

Suggested commit:

```text
Package the complete Highland learning stack with Compose
```

## E8.2 — Guided learning path and architecture walkthrough

**Status:** ⬜ Not started
**Implemented in:** —
**Depends on:** E8.1

Scope:

- Add tutorials for Discover, Create, approved action, and Automation.
- Explain every trust boundary, model call, indexed/MCP choice, and persisted
  record encountered by the tutorials.
- Add a “follow this run in code” map linking trace event types to runtime
  modules.
- Document scripted versus real model behavior and API-cost safeguards.

Acceptance criteria:

- A new learner can complete the scripted tutorial without an API key.
- A learner can opt into the live tutorial without changing source code.
- Documentation never describes scripted output as model-generated.

Verification:

```bash
pytest tests/docs
```

Suggested commit:

```text
Document the Highland agent harness learning path
```

## E8.3 — Continuous integration and repository quality gates

**Status:** ⬜ Not started
**Implemented in:** —
**Depends on:** E7.1, E4.3

Scope:

- Run Python lint, type checks, hermetic tests, frontend tests, and frontend
  build in CI.
- Never run billable Cohere evaluations on untrusted pull requests.
- Add dependency caching and artifact upload for failed deterministic reports.
- Add secret scanning and generated-file checks.

Acceptance criteria:

- CI passes without provider secrets.
- A network attempt from hermetic tests fails visibly.
- Generated runtime state cannot be accidentally committed.

Verification:

```bash
make ci
```

Suggested commit:

```text
Add hermetic CI quality gates for Highland
```

## E8.4 — Reset, backup, and educational release checklist

**Status:** ⬜ Not started
**Implemented in:** —
**Depends on:** E8.1, E8.2, E8.3

Scope:

- Add one command to reset source and platform runtime state with explicit
  confirmation and precise targets.
- Add local export/import for artifacts, workflows, and traces.
- Verify licensing and fictional-data rules.
- Add a release checklist covering clean clone, scripted demo, live demo,
  budgets, all scenarios, and documentation links.

Acceptance criteria:

- Reset never deletes checked-in seed data.
- Export/import round-trips human-created local artifacts.
- The release checklist passes from a clean clone.

Verification:

```bash
make release-check
```

Suggested commit:

```text
Add safe reset backup and educational release checks
```

---

# Working agreement for future Codex sessions

## Session startup

A new implementation session should begin with:

```text
Read docs/IMPLEMENTATION_PLAN.md completely.
Inspect git status and preserve unrelated user changes.
Implement exactly epic EX.Y, including its tests, documentation, status update,
and one focused commit. Do not begin the next epic.
```

The session must then:

1. Read this entire plan and the files named by the selected epic.
2. Confirm all dependencies are implemented.
3. Inspect current code rather than assuming the plan is perfectly current.
4. Mark the epic `🟨 Partial` only when committing useful but incomplete work.
5. Implement only the selected epic and necessary directly related fixes.
6. Run the epic's verification commands plus relevant existing regression tests.
7. Update this file's epic status, `Implemented in`, and milestone counts.
8. Create one focused commit using the suggested message or a clearer equivalent.
9. Report the commit hash, tests run, remaining limitations, and next eligible
   epic.

## Commit rule

The default is one commit per epic. An epic is intentionally sized as one
reviewable vertical change. If implementation proves too large, the session
must split the epic in this plan before broadening the code change. It should
not bundle a second epic merely because files overlap.

Do not mark an epic complete in a documentation-only commit before its code is
implemented. The code, tests, relevant docs, and plan status belong in the same
epic commit.

## Scope rule

Deterministic orchestration is not the same as hard-coded intelligence:

- **Application code owns:** schemas, state transitions, filtering, validation,
  limits, retries, policy, approval, idempotency, persistence, and rendering.
- **Cohere owns in real mode:** semantic embeddings, reranking, planning, tool
  selection, extraction, classification, investigation, drafting, and final
  synthesis.
- **Scripted providers own in tests:** only predetermined protocol responses
  needed to prove harness behavior.

If an implementation session proposes a heuristic that chooses a workflow,
diagnosis, health state, tool, or final answer in real mode, it conflicts with
this plan.

## Recommended epic order

Follow dependency order rather than milestone labels alone:

```text
E1.1 -> E1.2 -> E1.3 -> E1.5
                  \-> E1.4

E1.1 -> E3.1 -> E3.2
E1.* + E3.1 -> E2.1 -> E2.2 -> E2.3
E1.* + E2.1 -> E2.4 -> E2.5 -> E2.6

E3.2 + model providers -> E3.3 -> E3.4 -> E3.5 -> E3.6
M2 + M3 -> M4 -> M5
M2 + M3 + M5 -> M6
M3 through M6 -> M7 -> M8
```

E3.1 is intentionally required by E2.2 because index population should teach
the same MCP boundary used by the agent, rather than importing mock JSON files
behind the connector layer.

## Definition of done for the project

Highland is complete when:

- all epics through M8 are implemented or explicitly moved out of scope with a
  documented reason;
- the default suite is deterministic, offline, and non-billable;
- the three live Cohere scenarios can be run separately with explicit opt-in;
- the mock ecosystem, index, platform state, and artifacts are resettable;
- Discover answers use resolvable citations;
- Create artifacts preserve evidence and editable revisions;
- Automate runs are versioned, bounded, approval-aware, and inspectable;
- every real model and rerank call is visible in trace and cost reporting; and
- a clean clone can run the guided scripted demo using one documented command.
