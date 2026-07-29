# Showcase scenarios and future evaluations

The manifests in `data/scenarios` are executable product specifications for the
three showcase workflows. They are not model-generated golden answers. Each
defines the prompt, required source coverage, expected claims, evidence IDs,
approval boundary, and forbidden behavior.

## Customer meeting preparation

This is a read-only, multi-source aggregation task. A strong run should resolve
“Northwind Bank” to `cus_northwind`, then retrieve account, meeting, support,
deployment, incident, document, message, and project evidence. It should clearly
distinguish observed facts from the suspected root cause.

## Deployment issue investigation

This task exercises dynamic investigation and a write boundary. The runtime can
prepare the full `create_ticket` arguments, but it must persist an approval
request and pause before executing the MCP tool. After approval, it must reuse a
stable idempotency key if the run is retried.

## Weekly customer health

This task loops over three accounts and is useful before a scheduler exists.
The expected classifications are based on explicit source data, but the future
evaluation should grade the evidence and rationale rather than require one
exact wording.

## Evaluation layers

Use separate suites so failures remain understandable:

1. deterministic source-contract tests;
2. retrieval recall and customer-isolation tests;
3. tool-selection and argument tests;
4. approval-policy tests;
5. grounded-claim and citation tests;
6. optional billable live-model end-to-end runs.

The first four layers should work without a Cohere API key.
