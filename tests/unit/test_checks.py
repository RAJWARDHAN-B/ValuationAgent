from __future__ import annotations

from datetime import date

import pytest

from ib_agent.extraction import checks
from ib_agent.models.company import CompanyProfile
from ib_agent.models.financials import FinancialStatements, LineItemValue, Period, Statement

LABELS = ("FY2022", "FY2023", "FY2024")
AS_OF = date(2025, 6, 1)
PROFILE = CompanyProfile(cik=1, ticker="ACME", name="Acme", sic=3570)
BASE = {
    "revenue": 1000.0,
    "cost_of_revenue": 600.0,
    "gross_profit": 400.0,
    "operating_income": 200.0,
    "ebitda": 250.0,
    "depreciation_amortization": 50.0,
    "capex": 60.0,
    "income_tax_expense": 38.0,
    "cash": 100.0,
    "total_debt": 300.0,
    "diluted_shares": 100.0,
    "total_assets": 1000.0,
    "total_liabilities": 400.0,
    "total_equity": 600.0,
    "cfo": 200.0,
    "cfi": -60.0,
    "cff": -100.0,
    "net_change_in_cash": 40.0,
}


def make_fs(
    overrides: dict[str, dict[str, float | None]] | None = None,
    *,
    labels: tuple[str, ...] = LABELS,
    method: dict[str, str] | None = None,
    flipped: dict[str, set[str]] | None = None,
) -> FinancialStatements:
    """All periods share BASE values; LTM copies the latest FY. `None` removes a value."""
    periods = [
        Period(label=lbl, kind="FY", start=date(int(lbl[2:]), 1, 1), end=date(int(lbl[2:]), 12, 31))
        for lbl in labels
    ]
    last = periods[-1]
    periods.append(Period(label="LTM", kind="LTM", start=last.start, end=last.end))
    rows: dict[str, dict[str, LineItemValue]] = {}
    for item, base in BASE.items():
        rows[item] = {}
        for period in periods:
            source_label = last.label if period.kind == "LTM" else period.label
            value = (overrides or {}).get(item, {}).get(source_label, base)
            if value is None:
                continue
            rows[item][period.label] = LineItemValue(
                value=value,
                method=(method or {}).get(item, "tag"),  # type: ignore[arg-type]
                sign_flipped=source_label in (flipped or {}).get(item, set()),
            )
    statement = Statement(kind="IS", units=dict.fromkeys(BASE, "USD"), rows=rows)
    empty = Statement(kind="BS", units={}, rows={})
    return FinancialStatements(
        periods=periods,
        income_statement=statement,
        balance_sheet=empty,
        cash_flow=empty,
        derived=empty,
        outflow_items=["capex"],
    )


def test_clean_statements_pass_every_check() -> None:
    report = checks.run_qa(make_fs(), PROFILE, as_of=AS_OF)
    assert report.passed
    assert {c.status for c in report.checks} == {"pass"}


def test_balance_sheet() -> None:
    assert checks.check_balance_sheet(make_fs()).status == "pass"
    unbalanced = checks.check_balance_sheet(make_fs({"total_assets": {"FY2023": 1100.0}}))
    assert unbalanced.status == "fail" and "FY2023" in unbalanced.message
    missing = checks.check_balance_sheet(make_fs({"total_liabilities": {"FY2024": None}}))
    assert missing.status == "fail" and "missing" in missing.message


def test_gross_profit_tie() -> None:
    assert checks.check_gross_profit(make_fs()).status == "pass"
    assert checks.check_gross_profit(make_fs({"gross_profit": {"FY2024": 380.0}})).status == "warn"
    derived = make_fs({"gross_profit": {"FY2024": 380.0}}, method={"gross_profit": "sum"})
    assert checks.check_gross_profit(derived).status == "skip"


def test_ebitda_at_least_ebit() -> None:
    assert checks.check_ebitda(make_fs()).status == "pass"
    assert checks.check_ebitda(make_fs({"ebitda": {"FY2022": 150.0}})).status == "fail"


def test_cash_flow_tie() -> None:
    assert checks.check_cash_flow(make_fs()).status == "pass"
    assert (
        checks.check_cash_flow(make_fs({"net_change_in_cash": {"FY2024": 60.0}})).status == "warn"
    )
    no_change = make_fs({"net_change_in_cash": dict.fromkeys(LABELS)})
    assert checks.check_cash_flow(no_change).status == "skip"


def test_required_items() -> None:
    assert checks.check_required_items(make_fs()).status == "pass"
    result = checks.check_required_items(make_fs({"capex": {"FY2024": None}}))
    assert result.status == "fail" and "capex" in result.message


def test_history_depth() -> None:
    assert checks.check_history_depth(make_fs()).status == "pass"
    assert checks.check_history_depth(make_fs(labels=("FY2023", "FY2024"))).status == "fail"


def test_sign_conventions() -> None:
    assert checks.check_signs(make_fs()).status == "pass"
    assert checks.check_signs(make_fs(flipped={"capex": set(LABELS)})).status == "pass"
    assert checks.check_signs(make_fs({"capex": {"FY2023": -60.0}})).status == "fail"
    mixed = checks.check_signs(make_fs(flipped={"capex": {"FY2022"}}))
    assert mixed.status == "fail" and "inconsistent" in mixed.message


def test_sector_eligibility() -> None:
    assert checks.check_sector(PROFILE).status == "pass"
    bank = PROFILE.model_copy(update={"sic": 6022})
    assert checks.check_sector(bank).status == "fail"


def test_stale_data_only_warns() -> None:
    assert checks.check_staleness(make_fs(), AS_OF).status == "pass"
    report = checks.run_qa(make_fs(), PROFILE, as_of=date(2026, 6, 1))
    stale = next(c for c in report.checks if c.id == "stale_data")
    assert stale.status == "warn"
    assert report.passed


@pytest.mark.parametrize("item", ["total_assets", "ebitda"])
def test_any_failed_check_fails_report(item: str) -> None:
    report = checks.run_qa(make_fs({item: {"FY2024": 1.0}}), PROFILE, as_of=AS_OF)
    assert not report.passed
