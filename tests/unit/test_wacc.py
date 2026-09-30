import pytest

from ib_agent.valuation.wacc import WaccInputs, build_wacc, cost_of_debt


def test_cost_of_debt_clips_both_bounds_and_handles_no_debt() -> None:
    assert cost_of_debt(interest_expense=1, average_debt=100, risk_free_rate=0.04) == 0.04
    assert cost_of_debt(interest_expense=30, average_debt=100, risk_free_rate=0.04) == 0.12
    assert cost_of_debt(interest_expense=0, average_debt=0, risk_free_rate=0.04) == 0.055


def test_wacc_build_matches_hand_calculation() -> None:
    result = build_wacc(
        WaccInputs(
            risk_free_rate=0.04,
            beta=1.2,
            equity_risk_premium=0.05,
            cost_of_debt=0.06,
            tax_rate=0.25,
            share_price=10,
            diluted_shares=80,
            total_debt=200,
        )
    )

    assert result.market_equity == 800
    assert result.equity_weight == pytest.approx(0.8)
    assert result.debt_weight == pytest.approx(0.2)
    assert result.cost_of_equity == pytest.approx(0.10)
    assert result.wacc == pytest.approx(0.089)


def test_wacc_can_include_operating_leases() -> None:
    inputs = WaccInputs(
        risk_free_rate=0.04,
        beta=1.0,
        equity_risk_premium=0.05,
        cost_of_debt=0.06,
        tax_rate=0.25,
        share_price=10,
        diluted_shares=80,
        total_debt=200,
        operating_lease_liabilities=100,
        include_operating_leases=True,
    )

    assert build_wacc(inputs).debt == 300