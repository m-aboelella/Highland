# Private-cloud deployment architecture

- Document ID: `doc_private_cloud_arch`
- Type: `architecture`
- Team: `platform`
- Version: `5.1`
- Updated: `2026-05-20T08:30:00Z`

## Standard topology

<a id="pas_private_topology"></a>
The standard regulated topology uses three retrieval nodes across separate failure domains, a dedicated ingestion worker, and an external PostgreSQL metadata store. Customer traffic enters through their managed gateway.

## Telemetry

<a id="pas_private_telemetry"></a>
Private-cloud deployments export aggregate service metrics to Beacon through an outbound-only collector. Document content, search queries, and result payloads never leave the customer environment.
