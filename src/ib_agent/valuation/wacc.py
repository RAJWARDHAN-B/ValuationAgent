"""Weighted average cost of capital calculations."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class WaccInputs(BaseModel):
    risk_free_rate: float
    beta: float
    equity_risk_premium: float
    cost_of_debt: float
    tax_rate: float
    share_price: float = Field(gt=0)
    diluted_shares: float = Field(gt=0)
    total_debt: float = Field(ge=0)
    operating_lease_liabilities: float = Field(default=0, ge=0)
    include_operating_leases: bool = False

    @model_validator(mode="after")
    def validate_rates(self) -> WaccInputs:
        for name in ("risk_free_rate", "equity_risk_premium", "cost_of_debt", "tax_rate"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        return self


class WaccBuild(BaseModel):
    risk_free_rate: float
    beta: float
    equity_risk_premium: float
    cost_of_equity: float
    cost_of_debt: float
    tax_rate: float
    market_equity: float
    debt: float
    equity_weight: float
    debt_weight: float
    wacc: float


def cost_of_debt(
    interest_expense: float,
    average_debt: float,
    risk_free_rate: float,
    *,
    max_spread: float = 0.08,
    no_debt_spread: float = 0.015,
) -> float:
    if average_debt < 0 or interest_expense < 0:
        raise ValueError("Debt and interest expense must be non-negative")
    if max_spread < 0 or no_debt_spread < 0:
        raise ValueError("Debt spreads must be non-negative")
    if not 0 <= risk_free_rate <= 1:
        raise ValueError("Risk-free rate must be between 0 and 1")

    estimated_rate = (
        risk_free_rate + no_debt_spread
        if average_debt <= 1e-9
        else interest_expense / average_debt
    )
    return min(max(estimated_rate, risk_free_rate), risk_free_rate + max_spread)


def build_wacc(inputs: WaccInputs) -> WaccBuild:
    market_equity = inputs.share_price * inputs.diluted_shares
    debt = inputs.total_debt + (
        inputs.operating_lease_liabilities if inputs.include_operating_leases else 0
    )
    capital = market_equity + debt
    if capital <= 0:
        raise ValueError("Market equity plus debt must be positive")

    equity_weight = market_equity / capital
    debt_weight = debt / capital
    cost_of_equity = inputs.risk_free_rate + inputs.beta * inputs.equity_risk_premium
    wacc = (
        equity_weight * cost_of_equity
        + debt_weight * inputs.cost_of_debt * (1 - inputs.tax_rate)
    )
    return WaccBuild(
        risk_free_rate=inputs.risk_free_rate,
        beta=inputs.beta,
        equity_risk_premium=inputs.equity_risk_premium,
        cost_of_equity=cost_of_equity,
        cost_of_debt=inputs.cost_of_debt,
        tax_rate=inputs.tax_rate,
        market_equity=market_equity,
        debt=debt,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        wacc=wacc,
    )