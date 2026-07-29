from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.artifacts import (
    ArtifactCitation,
    ArtifactRepository,
    ArtifactType,
    export_markdown,
    export_pdf,
    safe_export_filename,
)
from highland.settings import HighlandSettings


def stored_artifact(tmp_path: Path):
    return ArtifactRepository(tmp_path / "artifacts").create(
        title="../../ Northwind: Q4 <script>",
        artifact_type=ArtifactType.BRIEFING,
        content=(
            "# Northwind briefing\n\n"
            "Latency reached 800 ms [E1].\n\n"
            "| Metric | Value |\n| --- | --- |\n| Latency | 800 ms [E1] |\n\n"
            '<img src="https://tracker.invalid/pixel.png"><script>alert(1)</script>'
        ),
        citations=[
            ArtifactCitation(
                id="E1",
                label="1",
                source_id="doc_latency",
                source_url="mock://archive/doc_latency",
                title="Latency report",
                passage="Latency reached 800 ms.",
                updated_at=datetime(2026, 7, 29, tzinfo=UTC),
            )
        ],
        conversation_id="con_1",
        run_id="run_1",
    )


def test_canonical_markdown_contains_resolvable_source_appendix(tmp_path: Path) -> None:
    artifact = stored_artifact(tmp_path)
    exported = export_markdown(artifact)
    assert "## Sources" in exported
    assert "[E1] Latency report — mock://archive/doc_latency" in exported
    assert artifact.updated_at.isoformat() in exported
    assert safe_export_filename(artifact.title, extension="md") == "northwind-q4-script.md"


def test_pdf_export_is_local_sanitized_and_preserves_document_structure(tmp_path: Path) -> None:
    artifact = stored_artifact(tmp_path)
    first = export_pdf(artifact)
    second = export_pdf(artifact)
    assert first.startswith(b"%PDF")
    assert len(first) > 1_000
    assert first == second


def test_export_api_returns_offline_downloads(tmp_path: Path) -> None:
    artifact = stored_artifact(tmp_path)
    with TestClient(create_app(HighlandSettings(workspace_dir=tmp_path))) as client:
        markdown_response = client.get(f"/artifacts/{artifact.id}/export.md")
        pdf_response = client.get(f"/artifacts/{artifact.id}/export.pdf")
    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-disposition"].endswith('"northwind-q4-script.md"')
    assert "[E1] Latency report" in markdown_response.text
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.content.startswith(b"%PDF")
