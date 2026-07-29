# Failure recovery and trace replay

Highland fails closed and records enough state to distinguish a terminal
failure from a resumable approval pause. Retry decisions are:

| Failure | Outcome | Notes |
| --- | --- | --- |
| Connector unavailable | Retry | Read calls only; connector failure is isolated |
| Injected HTTP 429, 500, or 503 | Retry | Bounded provider/connector retry |
| Latency timeout | Retry | Bounded; the original logical call ID is retained |
| Malformed model output | Terminal | Correct the prompt/schema or provider response |
| Model rate limit | Retry | Bounded exponential backoff |
| Budget stop | Terminal | Raise the configured budget explicitly before another call |
| Corrupted final trace line | Resume replay | The incomplete final line is ignored |
| Restart during approval | Resumable | Reload the durable approval; do not execute before approval |

A write is retryable only when it retains the original idempotency key.
Otherwise it is terminal and requires human review.

`TraceReplayService.replay` only reads the append-only event log; it never calls
a model or tool. A re-run is a separate explicit operation. It allocates a
fresh run ID, appends `rerun_of` linkage to the new trace, and invokes the
caller-supplied fresh-run operation. The original trace is never modified.
