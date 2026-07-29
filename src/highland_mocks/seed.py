from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SEED_DIR

DATASET_VERSION = "2026.07.29.1"
DATASET_AS_OF = "2026-07-29T12:00:00Z"


def _record(system: str, record_id: str, **values: Any) -> dict[str, Any]:
    return {
        "source_system": system,
        "record_id": record_id,
        **values,
    }


def _metric_series(
    metric: str,
    base: float,
    unit: str,
    *,
    spike_start: int | None = None,
    spike_by: float = 0,
) -> dict[str, Any]:
    start = datetime(2026, 7, 28, 12, tzinfo=UTC)
    samples = []
    variation = (0.0, 0.04, -0.02, 0.06, -0.03, 0.02)
    for index in range(25):
        value = base * (1 + variation[index % len(variation)])
        if spike_start is not None and index >= spike_start:
            value += spike_by + (index % 4) * spike_by * 0.06
        samples.append(
            {
                "timestamp": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
                "value": round(value, 3),
            }
        )
    return {"metric": metric, "unit": unit, "samples": samples}


def build_seed() -> dict[str, dict[str, Any]]:
    customers = [
        _record(
            "atlas",
            "cus_northwind",
            id="cus_northwind",
            name="Northwind Bank",
            industry="Financial services",
            tier="Strategic",
            status="active",
            region="EU",
            account_owner={"id": "usr_maya", "name": "Maya Chen"},
            technical_owner={"id": "usr_jonah", "name": "Jonah Okafor"},
            renewal_date="2026-10-31",
            annual_contract_value_usd=780000,
            health="watch",
            health_reason="Open P1 retrieval-latency issue and renewal in 94 days",
            deployment_ids=["dep_northwind_prod"],
            contacts=[
                {
                    "id": "con_elena",
                    "name": "Elena Varga",
                    "role": "VP, Digital Platforms",
                    "email": "elena.varga@northwind-bank.test",
                    "decision_role": "executive sponsor",
                },
                {
                    "id": "con_tomas",
                    "name": "Tomas Reed",
                    "role": "Search Platform Lead",
                    "email": "tomas.reed@northwind-bank.test",
                    "decision_role": "technical owner",
                },
            ],
            tags=["regulated", "private-cloud", "renewal-q4"],
            next_meeting_at="2026-07-30T09:30:00Z",
            notes=(
                "Northwind uses Summit Search for internal policy and analyst research. "
                "They value predictable latency and auditability over raw throughput."
            ),
            visibility="customer-success",
            updated_at="2026-07-29T09:10:00Z",
            source_url="https://atlas.summit.test/customers/cus_northwind",
        ),
        _record(
            "atlas",
            "cus_alpine",
            id="cus_alpine",
            name="Alpine Outfitters",
            industry="Retail",
            tier="Enterprise",
            status="active",
            region="North America",
            account_owner={"id": "usr_maya", "name": "Maya Chen"},
            technical_owner={"id": "usr_priya", "name": "Priya Nair"},
            renewal_date="2027-02-28",
            annual_contract_value_usd=240000,
            health="healthy",
            health_reason="Stable deployment and expanding usage",
            deployment_ids=["dep_alpine_prod"],
            contacts=[],
            tags=["cloud", "expansion"],
            next_meeting_at="2026-08-05T16:00:00Z",
            notes="Alpine powers product-support search for store associates.",
            visibility="customer-success",
            updated_at="2026-07-28T15:00:00Z",
            source_url="https://atlas.summit.test/customers/cus_alpine",
        ),
        _record(
            "atlas",
            "cus_lumon",
            id="cus_lumon",
            name="Lumon Health",
            industry="Healthcare",
            tier="Enterprise",
            status="active",
            region="North America",
            account_owner={"id": "usr_omar", "name": "Omar Haddad"},
            technical_owner={"id": "usr_jonah", "name": "Jonah Okafor"},
            renewal_date="2027-01-15",
            annual_contract_value_usd=320000,
            health="at_risk",
            health_reason="Security review is blocking the production rollout",
            deployment_ids=["dep_lumon_stage"],
            contacts=[],
            tags=["regulated", "healthcare", "pre-production"],
            next_meeting_at=None,
            notes="Lumon is still in staging pending a security exception.",
            visibility="customer-success",
            updated_at="2026-07-27T18:30:00Z",
            source_url="https://atlas.summit.test/customers/cus_lumon",
        ),
    ]

    documents = [
        _record(
            "archive",
            "doc_latency_runbook",
            id="doc_latency_runbook",
            title="Runbook: elevated retrieval latency",
            document_type="runbook",
            team="site-reliability",
            customer_id=None,
            version="3.4",
            visibility="support",
            updated_at="2026-06-18T14:00:00Z",
            source_url="https://archive.summit.test/docs/doc_latency_runbook",
            passages=[
                {
                    "passage_id": "pas_latency_triage",
                    "ordinal": 1,
                    "section": "Triage order",
                    "text": (
                        "For a sustained retrieval p95 above 900 ms, first compare query "
                        "volume, error rate, and shard memory. If volume is stable and one "
                        "shard exceeds 85% memory, inspect compaction and cache eviction "
                        "before scaling the whole cluster."
                    ),
                },
                {
                    "passage_id": "pas_latency_compaction",
                    "ordinal": 2,
                    "section": "Compaction contention",
                    "text": (
                        "Background index compaction can contend with retrieval when a shard "
                        "has less than 15% free memory. Pause the compaction job, allow caches "
                        "to recover for 20 minutes, then compare p50 and p95. Do not restart "
                        "all nodes at once."
                    ),
                },
                {
                    "passage_id": "pas_latency_customer_update",
                    "ordinal": 3,
                    "section": "Customer communication",
                    "text": (
                        "Tell the customer what users experienced, the current mitigation, "
                        "and the next checkpoint. Label suspected causes as hypotheses until "
                        "correlated with logs. Never promise a resolution time before the "
                        "mitigation has been observed under representative load."
                    ),
                },
            ],
        ),
        _record(
            "archive",
            "doc_private_cloud_arch",
            id="doc_private_cloud_arch",
            title="Private-cloud deployment architecture",
            document_type="architecture",
            team="platform",
            customer_id=None,
            version="5.1",
            visibility="company",
            updated_at="2026-05-20T08:30:00Z",
            source_url="https://archive.summit.test/docs/doc_private_cloud_arch",
            passages=[
                {
                    "passage_id": "pas_private_topology",
                    "ordinal": 1,
                    "section": "Standard topology",
                    "text": (
                        "The standard regulated topology uses three retrieval nodes across "
                        "separate failure domains, a dedicated ingestion worker, and an "
                        "external PostgreSQL metadata store. Customer traffic enters through "
                        "their managed gateway."
                    ),
                },
                {
                    "passage_id": "pas_private_telemetry",
                    "ordinal": 2,
                    "section": "Telemetry",
                    "text": (
                        "Private-cloud deployments export aggregate service metrics to Beacon "
                        "through an outbound-only collector. Document content, search queries, "
                        "and result payloads never leave the customer environment."
                    ),
                },
            ],
        ),
        _record(
            "archive",
            "doc_4182_release",
            id="doc_4182_release",
            title="Summit Search 4.18.2 release notes",
            document_type="release-notes",
            team="product",
            customer_id=None,
            version="4.18.2",
            visibility="company",
            updated_at="2026-07-10T12:00:00Z",
            source_url="https://archive.summit.test/docs/doc_4182_release",
            passages=[
                {
                    "passage_id": "pas_4182_compaction",
                    "ordinal": 1,
                    "section": "Index maintenance",
                    "text": (
                        "Version 4.18.2 reduces average compaction duration for newly created "
                        "indexes. Existing large shards can temporarily use more memory during "
                        "their first post-upgrade compaction. Schedule that operation outside "
                        "peak query periods."
                    ),
                },
                {
                    "passage_id": "pas_4182_known",
                    "ordinal": 2,
                    "section": "Known limitations",
                    "text": (
                        "On clusters with shards above 1.5 TB, compaction concurrency must "
                        "remain at one until the shard has completed its first 4.18.2 cycle."
                    ),
                },
            ],
        ),
        _record(
            "archive",
            "doc_incident_policy",
            id="doc_incident_policy",
            title="Customer incident severity and communication policy",
            document_type="policy",
            team="support",
            customer_id=None,
            version="2.2",
            visibility="support",
            updated_at="2026-04-05T10:00:00Z",
            source_url="https://archive.summit.test/docs/doc_incident_policy",
            passages=[
                {
                    "passage_id": "pas_severity_p1",
                    "ordinal": 1,
                    "section": "Priority 1",
                    "text": (
                        "A production degradation affecting a primary customer workflow with "
                        "no acceptable workaround is Priority 1. Provide an update at least "
                        "every 60 minutes while actively mitigating."
                    ),
                },
                {
                    "passage_id": "pas_incident_approval",
                    "ordinal": 2,
                    "section": "External updates",
                    "text": (
                        "Drafts generated from internal investigation notes require a human "
                        "owner to verify customer-safe wording before they are posted to an "
                        "external channel."
                    ),
                },
            ],
        ),
        _record(
            "archive",
            "doc_northwind_success_plan",
            id="doc_northwind_success_plan",
            title="Northwind Bank 2026 success plan",
            document_type="customer-plan",
            team="customer-success",
            customer_id="cus_northwind",
            version="1.6",
            visibility="customer-success",
            updated_at="2026-07-01T11:00:00Z",
            source_url="https://archive.summit.test/docs/doc_northwind_success_plan",
            passages=[
                {
                    "passage_id": "pas_northwind_outcomes",
                    "ordinal": 1,
                    "section": "Business outcomes",
                    "text": (
                        "Northwind’s 2026 target is to reduce analyst time spent locating "
                        "approved policy material by 30%. The executive sponsor will assess "
                        "adoption, answer quality, and stable performance at the October "
                        "renewal review."
                    ),
                },
                {
                    "passage_id": "pas_northwind_actions",
                    "ordinal": 2,
                    "section": "Open success actions",
                    "text": (
                        "Summit will deliver a capacity review before August 7. Northwind will "
                        "confirm its Q4 ingestion forecast. Both teams will agree on a latency "
                        "service-level objective before renewal planning."
                    ),
                },
            ],
        ),
    ]
    for document in documents:
        file_name = f"{document['id']}.md"
        document["file_name"] = file_name
        document["download_url"] = f"http://localhost:8102/documents/{document['id']}/download"

    tickets = [
        _record(
            "relay",
            "tkt_1042",
            id="tkt_1042",
            key="SUP-1042",
            customer_id="cus_northwind",
            title="Production search latency above SLO",
            status="open",
            priority="P1",
            category="performance",
            assignee={"id": "usr_priya", "name": "Priya Nair"},
            requester={"id": "con_tomas", "name": "Tomas Reed"},
            created_at="2026-07-28T13:18:00Z",
            updated_at="2026-07-29T11:42:00Z",
            description=(
                "Northwind reports analyst searches taking 1.5–2.2 seconds. Normal p95 is "
                "under 700 ms. The slowdown began after the weekend maintenance window."
            ),
            comments=[
                {
                    "id": "cmt_1042_1",
                    "author": "Tomas Reed",
                    "visibility": "external",
                    "created_at": "2026-07-28T13:18:00Z",
                    "text": "The issue is reproducible across policy and analyst indexes.",
                },
                {
                    "id": "cmt_1042_2",
                    "author": "Priya Nair",
                    "visibility": "internal",
                    "created_at": "2026-07-29T08:05:00Z",
                    "text": (
                        "Query volume is flat. Beacon shows retrieval shard 3 memory at 91% "
                        "during compaction. Pausing the job brought p95 below 900 ms."
                    ),
                },
                {
                    "id": "cmt_1042_3",
                    "author": "Jonah Okafor",
                    "visibility": "internal",
                    "created_at": "2026-07-29T11:42:00Z",
                    "text": (
                        "Hypothesis is memory contention from the first 4.18.2 compaction, "
                        "not yet confirmed. Need a controlled compaction tonight."
                    ),
                },
            ],
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1042",
        ),
        _record(
            "relay",
            "tkt_1033",
            id="tkt_1033",
            key="SUP-1033",
            customer_id="cus_northwind",
            title="Collector certificate rotation",
            status="resolved",
            priority="P3",
            category="configuration",
            assignee={"id": "usr_priya", "name": "Priya Nair"},
            requester={"id": "con_tomas", "name": "Tomas Reed"},
            created_at="2026-07-16T09:20:00Z",
            updated_at="2026-07-18T16:12:00Z",
            description="Beacon telemetry collector certificate was due to expire.",
            comments=[],
            resolution="New certificate installed and telemetry continuity verified.",
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1033",
        ),
        _record(
            "relay",
            "tkt_1018",
            id="tkt_1018",
            key="SUP-1018",
            customer_id="cus_northwind",
            title="Bulk ingestion job missed two PDFs",
            status="resolved",
            priority="P3",
            category="ingestion",
            assignee={"id": "usr_sam", "name": "Sam Rivera"},
            requester={"id": "con_tomas", "name": "Tomas Reed"},
            created_at="2026-06-20T10:00:00Z",
            updated_at="2026-06-21T12:00:00Z",
            description="Two password-protected PDFs were skipped by the ingestion job.",
            comments=[],
            resolution="Files were re-exported without passwords and ingested.",
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1018",
        ),
        _record(
            "relay",
            "tkt_1045",
            id="tkt_1045",
            key="SUP-1045",
            customer_id="cus_alpine",
            title="Synonym export format question",
            status="open",
            priority="P4",
            category="how-to",
            assignee={"id": "usr_sam", "name": "Sam Rivera"},
            requester={"id": "con_alpine_ops", "name": "Riley Morgan"},
            created_at="2026-07-29T08:00:00Z",
            updated_at="2026-07-29T08:00:00Z",
            description="Customer asks whether synonym exports preserve comments.",
            comments=[],
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1045",
        ),
    ]

    deployments = [
        _record(
            "beacon",
            "dep_northwind_prod",
            id="dep_northwind_prod",
            customer_id="cus_northwind",
            name="northwind-eu-production",
            environment="production",
            deployment_type="private-cloud",
            region="eu-west-1",
            version="4.18.2",
            topology={
                "retrieval_nodes": 3,
                "ingestion_workers": 1,
                "metadata_store": "customer-managed PostgreSQL",
                "index_shards": 6,
            },
            health="degraded",
            last_change_at="2026-07-27T01:00:00Z",
            change_summary="Upgraded from 4.17.6 to 4.18.2",
            visibility="engineering",
            updated_at="2026-07-29T11:55:00Z",
            source_url="https://beacon.summit.test/deployments/dep_northwind_prod",
        ),
        _record(
            "beacon",
            "dep_alpine_prod",
            id="dep_alpine_prod",
            customer_id="cus_alpine",
            name="alpine-us-production",
            environment="production",
            deployment_type="summit-cloud",
            region="us-east-2",
            version="4.18.2",
            topology={"retrieval_nodes": 3, "ingestion_workers": 2, "index_shards": 4},
            health="healthy",
            last_change_at="2026-07-21T02:00:00Z",
            change_summary="Routine patch rollout",
            visibility="engineering",
            updated_at="2026-07-29T11:55:00Z",
            source_url="https://beacon.summit.test/deployments/dep_alpine_prod",
        ),
        _record(
            "beacon",
            "dep_lumon_stage",
            id="dep_lumon_stage",
            customer_id="cus_lumon",
            name="lumon-us-staging",
            environment="staging",
            deployment_type="private-cloud",
            region="us-west-2",
            version="4.18.1",
            topology={"retrieval_nodes": 2, "ingestion_workers": 1, "index_shards": 2},
            health="healthy",
            last_change_at="2026-07-11T18:00:00Z",
            change_summary="Initial staging deployment",
            visibility="engineering",
            updated_at="2026-07-29T11:55:00Z",
            source_url="https://beacon.summit.test/deployments/dep_lumon_stage",
        ),
    ]

    metric_sets = {
        "dep_northwind_prod": [
            _metric_series("retrieval.p95_ms", 610, "ms", spike_start=13, spike_by=690),
            _metric_series("retrieval.p50_ms", 210, "ms", spike_start=13, spike_by=170),
            _metric_series("retrieval.error_rate", 0.002, "ratio", spike_start=13, spike_by=0.003),
            _metric_series(
                "retrieval.shard_3.memory_utilization", 0.72, "ratio", spike_start=13, spike_by=0.16
            ),
            _metric_series("retrieval.queries_per_second", 42, "qps"),
        ],
        "dep_alpine_prod": [
            _metric_series("retrieval.p95_ms", 480, "ms"),
            _metric_series("retrieval.error_rate", 0.001, "ratio"),
        ],
        "dep_lumon_stage": [
            _metric_series("retrieval.p95_ms", 390, "ms"),
            _metric_series("retrieval.error_rate", 0.001, "ratio"),
        ],
    }

    incidents = [
        _record(
            "beacon",
            "inc_208",
            id="inc_208",
            key="INC-208",
            customer_id="cus_northwind",
            deployment_id="dep_northwind_prod",
            title="Elevated retrieval latency during index compaction",
            severity="SEV-2",
            status="investigating",
            started_at="2026-07-28T12:40:00Z",
            ended_at=None,
            commander={"id": "usr_jonah", "name": "Jonah Okafor"},
            hypothesis=(
                "The first post-upgrade compaction increased memory pressure on shard 3, "
                "causing cache eviction and higher tail latency."
            ),
            mitigation=(
                "Compaction is paused. A controlled single-concurrency run is scheduled for "
                "2026-07-29T22:00:00Z."
            ),
            evidence=[
                "retrieval volume remained within 4% of baseline",
                "shard 3 memory exceeded 90%",
                "p95 declined after compaction was paused",
            ],
            visibility="engineering",
            updated_at="2026-07-29T11:50:00Z",
            source_url="https://beacon.summit.test/incidents/INC-208",
        )
    ]

    messages = [
        _record(
            "pulse",
            "msg_7001",
            id="msg_7001",
            customer_id="cus_northwind",
            channel="support-northwind",
            thread_id="thr_latency_northwind",
            author={"id": "usr_priya", "name": "Priya Nair"},
            created_at="2026-07-28T14:05:00Z",
            text=(
                "I reproduced the latency. Traffic is normal, but shard 3 memory climbs while "
                "compaction runs. I have paused it and linked Beacon incident INC-208."
            ),
            visibility="support",
            updated_at="2026-07-28T14:05:00Z",
            source_url="https://pulse.summit.test/channels/support-northwind/msg_7001",
        ),
        _record(
            "pulse",
            "msg_7002",
            id="msg_7002",
            customer_id="cus_northwind",
            channel="support-northwind",
            thread_id="thr_latency_northwind",
            author={"id": "usr_jonah", "name": "Jonah Okafor"},
            created_at="2026-07-28T14:22:00Z",
            text=(
                "This resembles the 4.18.2 first-compaction limitation, but we should call it "
                "a hypothesis until tonight's controlled run. Please avoid telling Northwind "
                "that the upgrade is definitively the root cause."
            ),
            visibility="support",
            updated_at="2026-07-28T14:22:00Z",
            source_url="https://pulse.summit.test/channels/support-northwind/msg_7002",
        ),
        _record(
            "pulse",
            "msg_7003",
            id="msg_7003",
            customer_id="cus_northwind",
            channel="customer-success",
            thread_id="thr_northwind_meeting",
            author={"id": "usr_maya", "name": "Maya Chen"},
            created_at="2026-07-29T09:02:00Z",
            text=(
                "Tomorrow Elena will want to know whether analysts are still affected, what "
                "we know versus suspect, and whether the October renewal timeline is at risk. "
                "We also owe Tomas a date for the capacity review."
            ),
            visibility="customer-success",
            updated_at="2026-07-29T09:02:00Z",
            source_url="https://pulse.summit.test/channels/customer-success/msg_7003",
        ),
        _record(
            "pulse",
            "msg_7004",
            id="msg_7004",
            customer_id="cus_northwind",
            channel="engineering-search",
            thread_id="thr_latency_northwind",
            author={"id": "usr_lee", "name": "Lee Park"},
            created_at="2026-07-29T10:40:00Z",
            text=(
                "Patch 4.18.3 can cap compaction memory, but it has not passed private-cloud "
                "qualification. The safe near-term mitigation is concurrency one plus a "
                "capacity review."
            ),
            visibility="engineering",
            updated_at="2026-07-29T10:40:00Z",
            source_url="https://pulse.summit.test/channels/engineering-search/msg_7004",
        ),
        _record(
            "pulse",
            "msg_7010",
            id="msg_7010",
            customer_id="cus_alpine",
            channel="customer-success",
            thread_id="thr_alpine_expansion",
            author={"id": "usr_maya", "name": "Maya Chen"},
            created_at="2026-07-28T17:00:00Z",
            text="Alpine wants to add two regions next quarter; current search health is strong.",
            visibility="customer-success",
            updated_at="2026-07-28T17:00:00Z",
            source_url="https://pulse.summit.test/channels/customer-success/msg_7010",
        ),
    ]

    meetings = [
        _record(
            "pulse",
            "mtg_northwind_0730",
            id="mtg_northwind_0730",
            customer_id="cus_northwind",
            title="Northwind weekly deployment review",
            starts_at="2026-07-30T09:30:00Z",
            duration_minutes=45,
            attendees=[
                {"name": "Maya Chen", "organization": "Summit Software"},
                {"name": "Jonah Okafor", "organization": "Summit Software"},
                {"name": "Elena Varga", "organization": "Northwind Bank"},
                {"name": "Tomas Reed", "organization": "Northwind Bank"},
            ],
            agenda=[
                "Production service health",
                "Latency incident and mitigation",
                "Capacity review",
                "Q4 ingestion forecast and renewal readiness",
            ],
            notes=None,
            visibility="customer-success",
            updated_at="2026-07-29T09:00:00Z",
            source_url="https://pulse.summit.test/meetings/mtg_northwind_0730",
        ),
        _record(
            "pulse",
            "mtg_northwind_0723",
            id="mtg_northwind_0723",
            customer_id="cus_northwind",
            title="Northwind weekly deployment review",
            starts_at="2026-07-23T09:30:00Z",
            duration_minutes=45,
            attendees=[],
            agenda=[],
            notes=(
                "Northwind approved the 4.18.2 maintenance window. Tomas asked Summit to "
                "quantify headroom before Q4. Elena reiterated that stable response time is "
                "a renewal requirement."
            ),
            visibility="customer-success",
            updated_at="2026-07-23T10:30:00Z",
            source_url="https://pulse.summit.test/meetings/mtg_northwind_0723",
        ),
    ]

    customer_updates = [
        _record(
            "pulse",
            "upd_northwind_0728",
            id="upd_northwind_0728",
            customer_id="cus_northwind",
            channel="northwind-operations",
            author={"id": "usr_priya", "name": "Priya Nair"},
            message=(
                "We are investigating elevated search latency and have paused background "
                "maintenance while we validate a mitigation. The next update is scheduled "
                "for 15:00 UTC."
            ),
            created_at="2026-07-28T14:10:00Z",
            visibility="external",
            updated_at="2026-07-28T14:10:00Z",
            source_url="https://pulse.summit.test/customer-updates/upd_northwind_0728",
        )
    ]

    projects = [
        _record(
            "track",
            "prj_search_runtime",
            id="prj_search_runtime",
            key="SEARCH",
            name="Search Runtime",
            team="engineering",
            visibility="engineering",
            updated_at="2026-07-29T10:30:00Z",
            source_url="https://track.summit.test/projects/SEARCH",
        ),
        _record(
            "track",
            "prj_customer_success",
            id="prj_customer_success",
            key="CUST",
            name="Customer Success",
            team="customer-success",
            visibility="customer-success",
            updated_at="2026-07-29T10:30:00Z",
            source_url="https://track.summit.test/projects/CUST",
        ),
    ]

    issues = [
        _record(
            "track",
            "iss_482",
            id="iss_482",
            key="SEARCH-482",
            project_id="prj_search_runtime",
            customer_id="cus_northwind",
            title="Limit compaction memory on large existing shards",
            description=(
                "Add a configurable memory ceiling and validate the change on private-cloud "
                "clusters with shards larger than 1.5 TB."
            ),
            status="in_progress",
            priority="high",
            assignee={"id": "usr_lee", "name": "Lee Park"},
            due_at="2026-08-04T17:00:00Z",
            labels=["performance", "compaction", "private-cloud"],
            visibility="engineering",
            created_at="2026-07-28T16:00:00Z",
            updated_at="2026-07-29T10:25:00Z",
            source_url="https://track.summit.test/issues/SEARCH-482",
        ),
        _record(
            "track",
            "iss_91",
            id="iss_91",
            key="CUST-91",
            project_id="prj_customer_success",
            customer_id="cus_northwind",
            title="Deliver Northwind capacity review",
            description=(
                "Summarize current headroom, expected Q4 ingestion, and recommended shard "
                "plan. Share a customer-safe version before August 7."
            ),
            status="todo",
            priority="high",
            assignee={"id": "usr_jonah", "name": "Jonah Okafor"},
            due_at="2026-08-07T17:00:00Z",
            labels=["customer-commitment", "renewal"],
            visibility="customer-success",
            created_at="2026-07-23T10:40:00Z",
            updated_at="2026-07-23T10:40:00Z",
            source_url="https://track.summit.test/issues/CUST-91",
        ),
        _record(
            "track",
            "iss_92",
            id="iss_92",
            key="CUST-92",
            project_id="prj_customer_success",
            customer_id="cus_northwind",
            title="Agree retrieval latency SLO with Northwind",
            description="Draft an SLO proposal for review during renewal planning.",
            status="todo",
            priority="medium",
            assignee={"id": "usr_maya", "name": "Maya Chen"},
            due_at="2026-08-14T17:00:00Z",
            labels=["customer-commitment", "slo"],
            visibility="customer-success",
            created_at="2026-07-23T10:45:00Z",
            updated_at="2026-07-23T10:45:00Z",
            source_url="https://track.summit.test/issues/CUST-92",
        ),
    ]

    return {
        "crm": {
            "meta": {"dataset_version": DATASET_VERSION, "as_of": DATASET_AS_OF},
            "customers": customers,
        },
        "knowledge": {
            "meta": {"dataset_version": DATASET_VERSION, "as_of": DATASET_AS_OF},
            "documents": documents,
        },
        "support": {
            "meta": {"dataset_version": DATASET_VERSION, "as_of": DATASET_AS_OF},
            "tickets": tickets,
            "idempotency": {},
        },
        "observability": {
            "meta": {"dataset_version": DATASET_VERSION, "as_of": DATASET_AS_OF},
            "deployments": deployments,
            "metrics": metric_sets,
            "incidents": incidents,
        },
        "communications": {
            "meta": {"dataset_version": DATASET_VERSION, "as_of": DATASET_AS_OF},
            "messages": messages,
            "meetings": meetings,
            "customer_updates": customer_updates,
            "idempotency": {},
        },
        "projects": {
            "meta": {"dataset_version": DATASET_VERSION, "as_of": DATASET_AS_OF},
            "projects": projects,
            "issues": issues,
            "idempotency": {},
        },
    }


def generate_seed(output_dir: Path = DEFAULT_SEED_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    dataset = build_seed()
    for service, payload in dataset.items():
        destination = output_dir / f"{service}.json"
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        written.append(destination)

    files_dir = output_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for old_file in files_dir.glob("doc_*.md"):
        old_file.unlink()
    for document in dataset["knowledge"]["documents"]:
        body = [
            f"# {document['title']}",
            "",
            f"- Document ID: `{document['id']}`",
            f"- Type: `{document['document_type']}`",
            f"- Team: `{document['team']}`",
            f"- Version: `{document['version']}`",
            f"- Updated: `{document['updated_at']}`",
            "",
        ]
        for passage in document["passages"]:
            body.extend(
                [
                    f"## {passage['section']}",
                    "",
                    f'<a id="{passage["passage_id"]}"></a>',
                    passage["text"],
                    "",
                ]
            )
        destination = files_dir / document["file_name"]
        destination.write_text("\n".join(body), encoding="utf-8")
        written.append(destination)
    return written
