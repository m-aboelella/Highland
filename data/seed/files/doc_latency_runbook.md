# Runbook: elevated retrieval latency

- Document ID: `doc_latency_runbook`
- Type: `runbook`
- Team: `site-reliability`
- Version: `3.4`
- Updated: `2026-06-18T14:00:00Z`

## Triage order

<a id="pas_latency_triage"></a>
For a sustained retrieval p95 above 900 ms, first compare query volume, error rate, and shard memory. If volume is stable and one shard exceeds 85% memory, inspect compaction and cache eviction before scaling the whole cluster.

## Compaction contention

<a id="pas_latency_compaction"></a>
Background index compaction can contend with retrieval when a shard has less than 15% free memory. Pause the compaction job, allow caches to recover for 20 minutes, then compare p50 and p95. Do not restart all nodes at once.

## Customer communication

<a id="pas_latency_customer_update"></a>
Tell the customer what users experienced, the current mitigation, and the next checkpoint. Label suspected causes as hypotheses until correlated with logs. Never promise a resolution time before the mitigation has been observed under representative load.
