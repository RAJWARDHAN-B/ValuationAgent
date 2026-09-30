"""Market beta estimation and leverage adjustments."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel


class BetaResult(BaseModel):
    raw: float
    adjusted: float
    r_squared: float
    n_obs: int
    frequency: Literal["monthly", "weekly"]


def blume_adjust(beta: float) -> float:
    return 0.67 * beta + 0.33


def regress_beta(
    asset_returns: Sequence[float],
    market_returns: Sequence[float],
    *,
    frequency: Literal["monthly", "weekly"] = "monthly",
) -> BetaResult:
    if len(asset_returns) != len(market_returns):
        raise ValueError("Asset and market returns must be aligned and equal in length")
    if len(asset_returns) < 2:
        raise ValueError("At least two aligned return observations are required")

    asset_mean = sum(asset_returns) / len(asset_returns)
    market_mean = sum(market_returns) / len(market_returns)
    market_variance = sum((value - market_mean) ** 2 for value in market_returns)
    if market_variance == 0:
        raise ValueError("Market returns must have non-zero variance")

    covariance = sum(
        (asset - asset_mean) * (market - market_mean)
        for asset, market in zip(asset_returns, market_returns, strict=True)
    )
    raw = covariance / market_variance
    asset_variance = sum((value - asset_mean) ** 2 for value in asset_returns)
    r_squared = 0.0 if asset_variance == 0 else covariance**2 / (asset_variance * market_variance)
    return BetaResult(
        raw=raw,
        adjusted=blume_adjust(raw),
        r_squared=r_squared,
        n_obs=len(asset_returns),
        frequency=frequency,
    )


def unlever(beta_levered: float, tax_rate: float, debt_to_equity: float) -> float:
    _validate_capital_structure(tax_rate, debt_to_equity)
    return beta_levered / (1 + (1 - tax_rate) * debt_to_equity)


def relever(beta_unlevered: float, tax_rate: float, debt_to_equity: float) -> float:
    _validate_capital_structure(tax_rate, debt_to_equity)
    return beta_unlevered * (1 + (1 - tax_rate) * debt_to_equity)


def _validate_capital_structure(tax_rate: float, debt_to_equity: float) -> None:
    if not 0 <= tax_rate <= 1:
        raise ValueError("Tax rate must be between 0 and 1")
    if debt_to_equity < 0:
        raise ValueError("Debt-to-equity must be non-negative")