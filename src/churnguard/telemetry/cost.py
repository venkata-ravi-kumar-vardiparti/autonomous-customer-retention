"""Static per-model price table and pure cost arithmetic. No network lookups.

Prices are USD per 1,000,000 tokens, the convention model providers publish
prices in, so a price-table entry can be checked against a rate card by eye.
compute_cost_usd is a total function of (model, tokens_in, tokens_out) - it
never reads the clock, the network, or any other process-global state.
"""

from __future__ import annotations

from dataclasses import dataclass

USD_PER_MILLION_TOKENS_DIVISOR = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    input_per_million_usd: float
    output_per_million_usd: float


PRICE_TABLE_USD_PER_MILLION_TOKENS: dict[str, ModelPrice] = {
    "gpt-4.1": ModelPrice(input_per_million_usd=2.00, output_per_million_usd=8.00),
    "gpt-4.1-mini": ModelPrice(input_per_million_usd=0.40, output_per_million_usd=1.60),
    "gpt-4.1-nano": ModelPrice(input_per_million_usd=0.10, output_per_million_usd=0.40),
    "gpt-4o": ModelPrice(input_per_million_usd=2.50, output_per_million_usd=10.00),
    "gpt-4o-mini": ModelPrice(input_per_million_usd=0.15, output_per_million_usd=0.60),
}


class UnknownModelPriceError(KeyError):
    """Raised when a model has no entry in the price table.

    Deliberately fatal, not a silent zero-cost default - a missing price
    entry must not masquerade as a free call in a cost report.
    """


def compute_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    if model not in PRICE_TABLE_USD_PER_MILLION_TOKENS:
        raise UnknownModelPriceError(f"no price entry for model {model!r}")
    price = PRICE_TABLE_USD_PER_MILLION_TOKENS[model]
    cost = (
        tokens_in / USD_PER_MILLION_TOKENS_DIVISOR * price.input_per_million_usd
        + tokens_out / USD_PER_MILLION_TOKENS_DIVISOR * price.output_per_million_usd
    )
    return round(cost, 6)


__all__ = [
    "PRICE_TABLE_USD_PER_MILLION_TOKENS",
    "ModelPrice",
    "UnknownModelPriceError",
    "compute_cost_usd",
]
