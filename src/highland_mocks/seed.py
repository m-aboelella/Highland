from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SEED_DIR
from .systems import SYSTEMS

DATASET_VERSION = "2026.07.29.1"
DATASET_AS_OF = "2026-07-29T12:00:00Z"


def build_seed() -> dict[str, dict[str, Any]]:
    """Assemble the canonical dataset from each source-owned seed fragment."""
    return {name: system.seed_fragment() for name, system in SYSTEMS.items()}


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
