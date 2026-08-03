from datetime import UTC, datetime

from highland.discover.service import aggregate_search_results
from highland.retrieval.contracts import Chunk, ChunkLocation
from highland.retrieval.hybrid import RetrievalResponse, RetrievalResult, RetrievalTimings


def passage(
    source_system: str,
    source_id: str,
    *,
    chunk_id: str,
    ordinal: int,
    score: float,
) -> RetrievalResult:
    return RetrievalResult(
        score=score,
        chunk=Chunk(
            id=chunk_id,
            source_system=source_system,
            source_id=source_id,
            title=f"{source_system}:{source_id}",
            text=f"Passage {ordinal}",
            content_hash=chunk_id,
            source_type="incident",
            visibility="engineering",
            updated_at=datetime(2026, 8, 3, tzinfo=UTC),
            source_url=f"mock://{source_system}/{source_id}",
            location=ChunkLocation(section=f"Section {ordinal}", ordinal=ordinal),
        ),
    )


def test_aggregation_uses_canonical_source_identity_and_preserves_passages() -> None:
    retrieval = RetrievalResponse(
        query="incident",
        results=[
            passage("beacon", "shared-id", chunk_id="chk_beacon_2", ordinal=2, score=0.9),
            passage("relay", "shared-id", chunk_id="chk_relay", ordinal=0, score=0.8),
            passage("beacon", "shared-id", chunk_id="chk_beacon_0", ordinal=0, score=0.7),
            passage("archive", "third", chunk_id="chk_third", ordinal=0, score=0.6),
        ],
        diagnostics=[],
        timings=RetrievalTimings(
            lexical_ms=0,
            semantic_ms=0,
            filter_ms=0,
            rerank_ms=0,
            total_ms=0,
        ),
    )

    response = aggregate_search_results(retrieval, source_limit=2)

    assert [(item.source_system, item.source_id) for item in response.results] == [
        ("beacon", "shared-id"),
        ("relay", "shared-id"),
    ]
    assert response.results[0].score == 0.9
    assert [item.chunk.id for item in response.results[0].passages] == [
        "chk_beacon_0",
        "chk_beacon_2",
    ]
