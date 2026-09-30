from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ib_agent.config import load_defaults
from ib_agent.errors import ExtractionError
from ib_agent.extraction.statements import build_statements, fiscal_year_label
from ib_agent.extraction.xbrl import load_tag_map, tidy_facts
from ib_agent.models.financials import FinancialStatements

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
TAG_MAP = load_tag_map(CONFIG_DIR / "xbrl_tag_map.yaml")
DEFAULTS = load_defaults(CONFIG_DIR).valuation
PRETAX = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
)

FactSpec = tuple[str, str, dict[str, Any]]


def fact(
    tag: str,
    val: float,
    end: str,
    start: str | None = None,
    *,
    form: str = "10-K",
    filed: str = "2025-02-15",
    accn: str = "0000000001-25-000005",
    fy: int = 2024,
    unit: str = "USD",
) -> FactSpec:
    entry: dict[str, Any] = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": "FY" if form.startswith("10-K") else "Q",
        "form": form,
        "filed": filed,
    }
    if start is not None:
        entry["start"] = start
    return tag, unit, entry


def annual(tag: str, year: int, val: float, **kwargs: Any) -> FactSpec:
    return fact(tag, val, f"{year}-12-31", f"{year}-01-01", **kwargs)


def build(*specs: FactSpec) -> FinancialStatements:
    us_gaap: dict[str, Any] = {}
    for tag, unit, entry in specs:
        us_gaap.setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(entry)
    facts = tidy_facts({"facts": {"us-gaap": us_gaap}}, TAG_MAP)
    return build_statements(facts, TAG_MAP, DEFAULTS)


def test_tag_fallback_per_period() -> None:
    fs = build(
        annual("Revenues", 2022, 80),
        annual("RevenueFromContractWithCustomerExcludingAssessedTax", 2023, 90),
        annual("Revenues", 2023, 91),
    )
    fy22, fy23 = fs.line("revenue", "FY2022"), fs.line("revenue", "FY2023")
    assert fy22 is not None and fy22.value == 80 and fy22.tag == "us-gaap:Revenues"
    assert fy23 is not None and fy23.value == 90
    assert fy23.tag == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def test_restatement_latest_filed_wins() -> None:
    fs = build(
        annual("Revenues", 2023, 905, filed="2024-02-20", accn="0000000001-24-000004", fy=2023),
        annual("Revenues", 2023, 900, filed="2025-02-15", accn="0000000001-25-000005", fy=2024),
    )
    line = fs.line("revenue", "FY2023")
    assert line is not None and line.value == 900
    assert line.sources[0].accession == "0000000001-25-000005"


def test_comparative_fact_labelled_by_end_date_not_fy() -> None:
    # The FY2024 10-K carries the FY2023 comparative with fy=2024.
    fs = build(annual("Revenues", 2023, 90, fy=2024), annual("Revenues", 2024, 100, fy=2024))
    assert [p.label for p in fs.fiscal_years] == ["FY2023", "FY2024"]
    assert fs.value("revenue", "FY2023") == 90


def test_quarterly_facts_in_10k_are_ignored() -> None:
    fs = build(
        annual("Revenues", 2024, 100),
        fact("Revenues", 28, "2024-12-31", "2024-10-01"),
    )
    assert [p.label for p in fs.fiscal_years] == ["FY2024"]
    assert fs.value("revenue", "FY2024") == 100


def test_non_periodic_forms_are_ignored() -> None:
    fs = build(annual("Revenues", 2024, 100), annual("Revenues", 2023, 1, form="8-K"))
    assert [p.label for p in fs.fiscal_years] == ["FY2024"]


def test_history_limited_to_configured_years() -> None:
    fs = build(*(annual("Revenues", year, year) for year in range(2017, 2025)))
    assert [p.label for p in fs.fiscal_years] == [f"FY{y}" for y in range(2020, 2025)]


def test_no_annual_facts_raises() -> None:
    with pytest.raises(ExtractionError, match="No annual"):
        build(fact("Revenues", 28, "2024-12-31", "2024-10-01"))


def test_ltm_roll_forward_by_hand() -> None:
    q = {"form": "10-Q", "filed": "2025-08-01", "accn": "0000000001-25-000020", "fy": 2025}
    shares = {"unit": "shares"}
    fs = build(
        annual("Revenues", 2024, 1000),
        fact("Revenues", 520, "2025-06-30", "2025-01-01", **q),
        fact("Revenues", 480, "2024-06-30", "2024-01-01", **q),
        annual("WeightedAverageNumberOfDilutedSharesOutstanding", 2024, 100, **shares),
        fact(
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            98,
            "2025-06-30",
            "2025-01-01",
            **q,
            **shares,
        ),
        fact(
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            101,
            "2024-06-30",
            "2024-01-01",
            **q,
            **shares,
        ),
        fact("Assets", 500, "2024-12-31"),
        fact("Assets", 550, "2025-06-30", **q),
    )
    assert (fs.ltm.start, fs.ltm.end) == (date(2024, 7, 1), date(2025, 6, 30))
    ltm_revenue = fs.line("revenue", "LTM")
    # LTM = FY2024 + 1H2025 - 1H2024 = 1000 + 520 - 480
    assert ltm_revenue is not None and ltm_revenue.value == 1040 and ltm_revenue.method == "ltm"
    assert len(ltm_revenue.sources) == 3
    assert fs.value("diluted_shares", "LTM") == 98
    assert fs.value("total_assets", "LTM") == 550


def test_ltm_equals_latest_fy_without_newer_10q() -> None:
    fs = build(annual("Revenues", 2023, 90), annual("Revenues", 2024, 100))
    assert (fs.ltm.start, fs.ltm.end) == (date(2024, 1, 1), date(2024, 12, 31))
    assert fs.value("revenue", "LTM") == 100


def test_ltm_missing_when_prior_ytd_component_missing() -> None:
    q = {"form": "10-Q", "filed": "2025-05-01", "accn": "0000000001-25-000012", "fy": 2025}
    fs = build(
        annual("Revenues", 2024, 1000),
        fact("Revenues", 270, "2025-03-31", "2025-01-01", **q),
        fact("Revenues", 240, "2024-03-31", "2024-01-01", **q),
        annual("OperatingIncomeLoss", 2024, 200),
        fact("OperatingIncomeLoss", 54, "2025-03-31", "2025-01-01", **q),
    )
    assert fs.value("revenue", "LTM") == 1030
    assert fs.value("operating_income", "LTM") is None


def test_outflows_stored_positive() -> None:
    fs = build(
        annual("PaymentsToAcquirePropertyPlantAndEquipment", 2023, -50),
        annual("PaymentsToAcquirePropertyPlantAndEquipment", 2024, 60),
    )
    fy23, fy24 = fs.line("capex", "FY2023"), fs.line("capex", "FY2024")
    assert fy23 is not None and fy23.value == 50 and fy23.sign_flipped
    assert fy24 is not None and fy24.value == 60 and not fy24.sign_flipped
    assert "capex" in fs.outflow_items


def test_sum_of_fallback_with_optional_components() -> None:
    fs = build(
        annual("Revenues", 2024, 100),
        fact("LongTermDebtCurrent", 20, "2024-12-31"),
        fact("CommercialPaper", 5, "2024-12-31"),
    )
    line = fs.line("short_term_debt", "FY2024")
    assert line is not None and line.value == 25 and line.method == "sum"
    assert {s.tag for s in line.sources} == {
        "us-gaap:LongTermDebtCurrent",
        "us-gaap:CommercialPaper",
    }


def test_sum_of_canonical_items() -> None:
    fs = build(annual("Revenues", 2024, 100), annual("CostOfRevenue", 2024, 60))
    line = fs.line("gross_profit", "FY2024")
    assert line is not None and line.value == 40
    assert line.formula == "revenue - cost_of_revenue"


def test_sum_of_requires_non_optional_components() -> None:
    fs = build(annual("Revenues", 2024, 100), annual("SellingAndMarketingExpense", 2024, 10))
    assert fs.value("sga", "FY2024") is None


def test_derived_items_and_statutory_tax_fallback() -> None:
    def year(y: int, cash: float, tca: float, tcl: float) -> list[FactSpec]:
        end = f"{y}-12-31"
        return [
            annual("Revenues", y, 1000),
            annual("OperatingIncomeLoss", y, 100),
            annual("DepreciationDepletionAndAmortization", y, 30),
            annual("PaymentsToAcquirePropertyPlantAndEquipment", y, 40),
            annual("IncomeTaxExpenseBenefit", y, 5),
            annual(PRETAX, y, -10),
            fact("CashAndCashEquivalentsAtCarryingValue", cash, end),
            fact("AssetsCurrent", tca, end),
            fact("LiabilitiesCurrent", tcl, end),
            fact("LongTermDebtNoncurrent", 200, end),
        ]

    fs = build(*year(2023, 50, 150, 60), *year(2024, 60, 180, 70))

    assert fs.value("ebitda", "FY2024") == 130
    assert fs.value("total_debt", "FY2024") == 200
    assert fs.value("net_debt", "FY2024") == 140
    # NWC = (180 - 60) - 70 = 50; prior (150 - 50) - 60 = 40
    assert fs.value("net_working_capital", "FY2024") == 50
    assert fs.value("effective_tax_rate", "FY2024") is None
    # UFCF = 100 * (1 - 0.21) + 30 - 40 - (50 - 40) = 59
    assert fs.value("ufcf", "FY2024") == pytest.approx(59)
    assert fs.value("ufcf", "FY2023") is None


def test_effective_tax_rate_clipped() -> None:
    fs = build(
        annual("Revenues", 2024, 1000),
        annual("IncomeTaxExpenseBenefit", 2024, 50),
        annual(PRETAX, 2024, 100),
    )
    assert fs.value("effective_tax_rate", "FY2024") == DEFAULTS.max_tax_rate


@pytest.mark.parametrize(
    ("end", "label"),
    [(date(2024, 12, 31), "FY2024"), (date(2023, 1, 1), "FY2022"), (date(2025, 1, 31), "FY2025")],
)
def test_fiscal_year_label(end: date, label: str) -> None:
    assert fiscal_year_label(end) == label
