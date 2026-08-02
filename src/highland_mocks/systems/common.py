from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..constants import SERVICE_PRODUCTS


class MetricQuery(BaseModel):
    customer_id: str
    metric: str
    time_range: str = Field(default="24h", pattern=r"^(1h|6h|12h|24h|48h|7d)$")


class TicketCreate(BaseModel):
    customer_id: str
    title: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=10, max_length=5000)
    priority: str = Field(default="P3", pattern=r"^P[1-4]$")
    category: str = Field(default="investigation", max_length=50)


class IssueCreate(BaseModel):
    project_id: str
    customer_id: str | None = None
    title: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=10, max_length=5000)
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|urgent)$")
    labels: list[str] = Field(default_factory=list, max_length=10)


class CustomerUpdateCreate(BaseModel):
    customer_id: str
    message: str = Field(min_length=10, max_length=5000)
    channel: str = Field(default="customer-operations", min_length=2, max_length=80)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def make_record(system: str, record_id: str, **values: Any) -> dict[str, Any]:
    return {"source_system": system, "record_id": record_id, **values}


def find(records: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    value_lower = value.lower()
    return next(
        (
            item
            for item in records
            if str(item.get("id", "")).lower() == value_lower
            or str(item.get("key", "")).lower() == value_lower
        ),
        None,
    )


def filtered(
    records: list[dict[str, Any]],
    *,
    customer_id: str | None = None,
    status: str | None = None,
    visibility: str | None = None,
) -> list[dict[str, Any]]:
    return [
        item
        for item in records
        if (customer_id is None or item.get("customer_id") == customer_id)
        and (status is None or item.get("status") == status)
        and (visibility is None or item.get("visibility") == visibility)
    ]


def relevance_score(query: str, *parts: str) -> float:
    tokens = lambda value: {
        token for token in re.findall(r"[a-z0-9][a-z0-9._-]+", value.lower()) if len(token) > 1
    }
    query_tokens = tokens(query)
    if not query_tokens:
        return 0
    overlap = query_tokens & tokens(" ".join(parts))
    exact_bonus = 2 if query.lower() in " ".join(parts).lower() else 0
    return round((len(overlap) + exact_bonus) / len(query_tokens), 4)


def range_start(time_range: str) -> datetime:
    amount = int(time_range[:-1])
    delta = timedelta(hours=amount) if time_range[-1] == "h" else timedelta(days=amount)
    return datetime(2026, 7, 29, 12, tzinfo=UTC) - delta


def metric_series(
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


class SourceClient:
    def __init__(self, connector: str, base_url: str) -> None:
        self.connector = connector
        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, timeout=10)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self.client.get(
                path,
                params={key: value for key, value in (params or {}).items() if value is not None},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} returned HTTP "
                f"{error.response.status_code}: {_error_text(error.response)}"
            ) from None
        except httpx.RequestError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} is unavailable at {self.base_url}: {error}"
            ) from None

    def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = self.client.post(path, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} returned HTTP "
                f"{error.response.status_code}: {_error_text(error.response)}"
            ) from None
        except httpx.RequestError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} is unavailable at {self.base_url}: {error}"
            ) from None


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("detail") or body.get("message") or body.get("error"))
    except ValueError:
        return response.text[:200]
