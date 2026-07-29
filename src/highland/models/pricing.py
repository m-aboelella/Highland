from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .contracts import Usage


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    effective_date: date
    input_usd_per_million_tokens: float | None = Field(default=None, ge=0)
    output_usd_per_million_tokens: float | None = Field(default=None, ge=0)
    search_usd_per_unit: float | None = Field(default=None, ge=0)
    notes: str = ""


class PriceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    prices: list[ModelPrice]

    @classmethod
    def load(cls, path: Path) -> PriceCatalog:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def price_for(self, model: str, *, on_date: date | None = None) -> ModelPrice | None:
        selected_date = on_date or datetime.now(UTC).date()
        eligible = [
            price
            for price in self.prices
            if price.model == model and price.effective_date <= selected_date
        ]
        return max(eligible, key=lambda price: price.effective_date) if eligible else None

    def estimate(
        self,
        model: str,
        operation: str,
        usage: Usage,
        *,
        on_date: date | None = None,
    ) -> float | None:
        price = self.price_for(model, on_date=on_date)
        if price is None:
            return None
        if operation == "rerank":
            if usage.search_units is None or price.search_usd_per_unit is None:
                return None
            return usage.search_units * price.search_usd_per_unit
        input_tokens = usage.billed_input_tokens
        if input_tokens is None:
            input_tokens = usage.input_tokens
        output_tokens = usage.billed_output_tokens
        if output_tokens is None:
            output_tokens = usage.output_tokens
        if input_tokens is not None and price.input_usd_per_million_tokens is None:
            return None
        if output_tokens is not None and price.output_usd_per_million_tokens is None:
            return None
        if input_tokens is None and output_tokens is None:
            return None
        return (
            (input_tokens or 0) * (price.input_usd_per_million_tokens or 0)
            + (output_tokens or 0) * (price.output_usd_per_million_tokens or 0)
        ) / 1_000_000
