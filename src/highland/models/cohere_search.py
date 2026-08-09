from __future__ import annotations

import time
from typing import Any

from .cohere_mapping import _CohereBase, _usage
from .contracts import (
    EmbeddingRequest,
    EmbeddingResponse,
    ModelError,
    RankedResult,
    RerankRequest,
    RerankResponse,
)


class CohereEmbeddingModel(_CohereBase):
    name = "cohere-embed-v2"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        started = time.perf_counter()
        response = await self._retry(
            lambda: self.client.embed(
                model=self.model,
                texts=request.texts,
                input_type=request.input_type.value,
                embedding_types=["float"],
            )
        )
        vectors = response.embeddings.float_
        if vectors is None:
            raise ModelError(
                "Cohere did not return float embeddings",
                code="malformed_provider_response",
                provider="cohere",
                request_id=response.id,
            )
        return EmbeddingResponse(
            vectors=vectors,
            usage=_usage(response.meta),
            metadata=self._metadata(
                response,
                logical_call_id=request.logical_call_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )


class CohereRerankModel(_CohereBase):
    name = "cohere-rerank-v2"

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        started = time.perf_counter()
        arguments: dict[str, Any] = {
            "model": self.model,
            "query": request.query,
            "documents": [document.text for document in request.documents],
        }
        if request.top_n is not None:
            arguments["top_n"] = request.top_n
        response = await self._retry(lambda: self.client.rerank(**arguments))
        return RerankResponse(
            results=[
                RankedResult(
                    index=item.index,
                    relevance_score=item.relevance_score,
                    document=request.documents[item.index],
                )
                for item in response.results
            ],
            usage=_usage(response.meta),
            metadata=self._metadata(
                response,
                logical_call_id=request.logical_call_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )
