import pytest

from ib_agent.valuation.beta import blume_adjust, regress_beta, relever, unlever


def test_regression_recovers_known_slope_and_blume_adjustment() -> None:
    market = [-0.10, -0.05, 0.0, 0.05, 0.10]
    asset = [1.3 * value + 0.02 for value in market]

    result = regress_beta(asset, market)

    assert result.raw == pytest.approx(1.3)
    assert result.adjusted == pytest.approx(1.201)
    assert result.r_squared == pytest.approx(1.0)
    assert result.n_obs == 5
    assert result.frequency == "monthly"


def test_regression_rejects_unaligned_inputs() -> None:
    with pytest.raises(ValueError, match="aligned"):
        regress_beta([0.1, 0.2], [0.1])


def test_blume_adjustment() -> None:
    assert blume_adjust(1.5) == pytest.approx(1.335)


def test_unlever_and_relever_round_trip() -> None:
    unlevered = unlever(beta_levered=1.5, tax_rate=0.25, debt_to_equity=0.5)
    assert relever(unlevered, tax_rate=0.25, debt_to_equity=0.5) == pytest.approx(1.5)