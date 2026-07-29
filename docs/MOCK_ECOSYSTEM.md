# Mock enterprise ecosystem

The fictional vendor is **Summit Software**, a B2B search platform. Highland’s
users are Summit customer-success, support, and engineering employees.

## Atlas CRM

Atlas is the commercial source of truth. A customer contains account tier,
region, renewal date, owners, contacts, tags, and links to known deployments.
It does not contain operational metrics or full support histories.

Read tools:

- `list_customers`
- `get_customer`

## Archive knowledge

Archive stores product documentation and operational runbooks. Documents have
stable passage IDs so Highland can link citations to exact sections. Archive’s
search is deliberately lexical; Highland will later add embeddings and
reranking in its own retrieval layer. The source documents also exist as
generated Markdown files under `data/seed/files` and can be downloaded from the
Archive API, allowing the future ingestion pipeline to parse real files.

Read tools:

- `search_documents`
- `get_document`

## Relay Desk

Relay holds customer support tickets and timestamped comments. Creating a
ticket accepts an idempotency key. Repeating the same request returns the
existing record rather than creating duplicates.

Read tools:

- `list_customer_tickets`
- `get_ticket`

Write tools:

- `create_ticket`

## Beacon observability

Beacon owns deployment metadata, incidents, and metric samples. Metric queries
accept a customer, metric name, and simple time range. The initial dataset
includes retrieval latency, error rate, memory utilization, and throughput.

Read tools:

- `get_deployment`
- `query_deployment_metrics`
- `list_incidents`

## Pulse communications

Pulse combines internal channels and meeting notes for the demo. Messages
record participants, visibility, customer links, channel, and thread. Search
returns matching passages, not entire unrestricted channel exports.

Read tools:

- `search_messages`
- `list_customer_meetings`

Write tools:

- `post_customer_update`

## Track projects

Track holds work items across engineering and customer success. Creating an
issue is stateful and idempotent.

Read tools:

- `list_project_issues`
- `get_project_issue`

Write tools:

- `create_project_issue`

## Failure injection

Every service accepts these optional request headers:

- `X-Mock-Latency-Ms`: delay the response by the requested number of
  milliseconds, capped at 5 seconds.
- `X-Mock-Failure`: return the specified HTTP status between 400 and 599.

These controls make timeout, retry, and partial-result behavior reproducible
without an unreliable external dependency.

## Data reset and mutation

Generated source data lives in `data/seed`. Mutable copies live in `var` and
are ignored by Git. On first startup a service copies its seed. Running
`highland-mocks reset` replaces all runtime copies. The generator is
deterministic so IDs and cross-system links remain stable.
