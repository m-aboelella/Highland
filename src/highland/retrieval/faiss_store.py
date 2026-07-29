from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from highland.models.contracts import EmbeddingModel, EmbeddingRequest, InputType

from .contracts import Chunk


class VectorIndexError(RuntimeError):
    pass


class IncompatibleVectorIndex(VectorIndexError):
    pass


@dataclass(frozen=True, slots=True)
class VectorMatch:
    chunk_id: str
    score: float


class FaissStore:
    INDEX_FILE = "vectors.faiss"
    METADATA_FILE = "vector-metadata.json"
    MAPPING_FILE = "vector-chunks.json"

    def __init__(self, path: Path, *, model_id: str, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("vector dimension must be positive")
        self.path = path
        self.model_id = model_id
        self.dimension = dimension
        self._index = faiss.IndexFlatIP(dimension)
        self._chunk_ids: list[str] = []

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        expected_model_id: str | None = None,
        expected_dimension: int | None = None,
    ) -> FaissStore:
        metadata_path = path / cls.METADATA_FILE
        if not metadata_path.exists():
            raise VectorIndexError(f"vector index metadata is missing in {path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        model_id = str(metadata["model_id"])
        dimension = int(metadata["dimension"])
        if expected_model_id is not None and model_id != expected_model_id:
            raise IncompatibleVectorIndex(
                f"index uses embedding model {model_id!r}; rebuild for {expected_model_id!r}"
            )
        if expected_dimension is not None and dimension != expected_dimension:
            raise IncompatibleVectorIndex(
                f"index dimension is {dimension}; rebuild for dimension {expected_dimension}"
            )
        store = cls(path, model_id=model_id, dimension=dimension)
        store._index = faiss.read_index(str(path / cls.INDEX_FILE))
        store._chunk_ids = json.loads((path / cls.MAPPING_FILE).read_text(encoding="utf-8"))
        if store._index.d != dimension or store._index.ntotal != len(store._chunk_ids):
            raise VectorIndexError("FAISS index and chunk mapping are inconsistent")
        return store

    def add(self, chunk_ids: list[str], vectors: list[list[float]]) -> None:
        if not chunk_ids:
            if vectors:
                raise VectorIndexError("vectors were provided without chunk IDs")
            return
        if len(chunk_ids) != len(vectors):
            raise VectorIndexError("chunk ID and vector counts differ")
        array = np.asarray(vectors, dtype="float32")
        if array.ndim != 2 or array.shape[1] != self.dimension:
            actual = array.shape[1] if array.ndim == 2 else "invalid"
            raise IncompatibleVectorIndex(
                f"received vector dimension {actual}; expected {self.dimension}"
            )
        faiss.normalize_L2(array)
        self._index.add(array)
        self._chunk_ids.extend(chunk_ids)

    def search(self, vector: list[float], *, limit: int = 10) -> list[VectorMatch]:
        if limit <= 0:
            raise ValueError("search limit must be positive")
        if not self._chunk_ids:
            return []
        query = np.asarray([vector], dtype="float32")
        if query.shape != (1, self.dimension):
            raise IncompatibleVectorIndex(
                f"query dimension is {query.shape[-1]}; expected {self.dimension}"
            )
        faiss.normalize_L2(query)
        scores, indexes = self._index.search(query, min(limit, len(self._chunk_ids)))
        return [
            VectorMatch(chunk_id=self._chunk_ids[index], score=float(score))
            for score, index in zip(scores[0], indexes[0], strict=True)
            if index >= 0
        ]

    def save(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self.path / self.INDEX_FILE))
        (self.path / self.METADATA_FILE).write_text(
            json.dumps(
                {
                    "version": 1,
                    "model_id": self.model_id,
                    "dimension": self.dimension,
                    "count": len(self._chunk_ids),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.path / self.MAPPING_FILE).write_text(
            json.dumps(self._chunk_ids, indent=2) + "\n", encoding="utf-8"
        )

    def vectors_by_id(self) -> dict[str, list[float]]:
        return {
            chunk_id: self._index.reconstruct(index).tolist()
            for index, chunk_id in enumerate(self._chunk_ids)
        }


class EmbeddingIndex:
    def __init__(self, model: EmbeddingModel, *, batch_size: int = 96) -> None:
        if batch_size <= 0:
            raise ValueError("embedding batch size must be positive")
        self.model = model
        self.batch_size = batch_size

    @property
    def model_id(self) -> str:
        return str(getattr(self.model, "model", self.model.name))

    async def build(self, chunks: list[Chunk], path: Path) -> FaissStore:
        if not chunks:
            raise VectorIndexError("cannot build a vector index without chunks")
        vectors = await self.embed_chunks(chunks)
        return self.build_from_vectors(chunks, vectors, path)

    async def embed_chunks(self, chunks: list[Chunk]) -> dict[str, list[float]]:
        vectors: dict[str, list[float]] = {}
        for offset in range(0, len(chunks), self.batch_size):
            batch = chunks[offset : offset + self.batch_size]
            response = await self.model.embed(
                EmbeddingRequest(
                    texts=[chunk.text for chunk in batch],
                    input_type=InputType.SEARCH_DOCUMENT,
                    logical_call_id=f"index:{offset // self.batch_size + 1}",
                )
            )
            if len(response.vectors) != len(batch):
                raise VectorIndexError("embedding provider returned an unexpected vector count")
            vectors.update(zip((chunk.id for chunk in batch), response.vectors, strict=True))
        return vectors

    def build_from_vectors(
        self,
        chunks: list[Chunk],
        vectors_by_id: dict[str, list[float]],
        path: Path,
    ) -> FaissStore:
        if not chunks:
            raise VectorIndexError("cannot build a vector index without chunks")
        missing = [chunk.id for chunk in chunks if chunk.id not in vectors_by_id]
        if missing:
            raise VectorIndexError(f"vectors are missing for {len(missing)} chunks")
        vectors = [vectors_by_id[chunk.id] for chunk in chunks]
        dimension = len(vectors[0])
        if not dimension or any(len(vector) != dimension for vector in vectors):
            raise VectorIndexError("embedding provider returned empty or inconsistent vectors")
        path.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".vector-stage-", dir=path.parent))
        try:
            store = FaissStore(stage, model_id=self.model_id, dimension=dimension)
            store.add([chunk.id for chunk in chunks], vectors)
            store.save()
            backup = path.with_name(f".{path.name}.previous")
            if backup.exists():
                shutil.rmtree(backup)
            if path.exists():
                os.replace(path, backup)
            os.replace(stage, path)
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return FaissStore.open(path, expected_model_id=self.model_id, expected_dimension=dimension)

    async def query(self, store: FaissStore, text: str, *, limit: int = 10) -> list[VectorMatch]:
        if not text.strip():
            raise ValueError("query text cannot be empty")
        response = await self.model.embed(
            EmbeddingRequest(
                texts=[text],
                input_type=InputType.SEARCH_QUERY,
                logical_call_id="retrieval-query",
            )
        )
        if len(response.vectors) != 1:
            raise VectorIndexError("embedding provider returned an unexpected query vector count")
        return store.search(response.vectors[0], limit=limit)
