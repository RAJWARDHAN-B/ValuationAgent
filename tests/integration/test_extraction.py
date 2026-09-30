from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ib_agent.agents.extraction import run_extraction
from ib_agent.agents.research import run_research
from ib_agent.config import load_defaults
from ib_agent.extraction.xbrl import load_tag_map

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
M = 1_000_000


@pytest.fixture
def acme(make_client):
    client, _ = make_client()
    research = run_research(client, "ACME")
    return run_extraction(
        research,
        load_defaults(CONFIG_DIR),
        load_tag_map(CONFIG_DIR / "xbrl_tag_map.yaml"),
        as_of=date(2025, 6, 1),
    )


def test_fixture_company_statements(acme) -> None:
    fs = acme.statements
    assert [p.label for p in fs.periods] == ["FY2021", "FY2022", "FY2023", "FY2024", "LTM"]
    assert (fs.ltm.start, fs.ltm.end) == (date(2024, 4, 1), date(2025, 3, 31))

    expected = {
        ("revenue", "FY2021"): 700 * M,
        ("revenue", "FY2023"): 900 * M,  # restated in the FY2024 10-K
        ("revenue", "FY2024"): 1000 * M,
        ("operating_income", "FY2024"): 200 * M,
        ("net_income", "FY2024"): 150 * M,
        ("eps_diluted", "FY2024"): 1.50,
        ("depreciation_amortization", "FY2024"): 50 * M,
        ("capex", "FY2024"): 60 * M,
        ("total_assets", "FY2024"): 1136 * M,
        ("stockholders_equity", "FY2024"): 741 * M,
        ("short_term_debt", "FY2024"): 20 * M,
        ("cash", "LTM"): 281 * M,
        ("revenue", "LTM"): 1030 * M,
        ("operating_income", "LTM"): 206 * M,
        ("capex", "LTM"): 61 * M,
        ("eps_diluted", "LTM"): pytest.approx(1.55),
    }
    actual = {key: fs.value(*key) for key in expected}
    assert actual == expected


def test_fixture_company_sources(acme) -> None:
    fs = acme.statements
    fy21 = fs.line("revenue", "FY2021")
    fy23 = fs.line("revenue", "FY2023")
    assert fy21 is not None and fy21.tag == "us-gaap:Revenues"
    assert fy23 is not None and fy23.sources[0].accession == "0000000001-25-000005"
    total_equity = fs.line("total_equity", "FY2024")
    assert total_equity is not None and total_equity.method == "sum"


def test_fixture_company_derived(acme) -> None:
    fs = acme.statements
    assert fs.value("ebitda", "FY2024") == 250 * M
    assert fs.value("total_debt", "FY2024") == 320 * M
    assert fs.value("net_debt", "FY2024") == 1 * M
    assert fs.value("net_working_capital", "FY2024") == 165 * M
    assert fs.value("effective_tax_rate", "FY2024") == pytest.approx(38 / 188)
    # 200 * (1 - 38/188) + 50 - 60 - (165 - 150)
    assert fs.value("ufcf", "FY2024") == pytest.approx((200 * 150 / 188 - 25) * M)
    assert fs.value("ufcf", "FY2021") is None


def test_fixture_company_passes_qa(acme) -> None:
    assert acme.qa.passed
    assert {c.id: c.status for c in acme.qa.checks} == {
        "balance_sheet_balances": "pass",
        "gross_profit_tie": "pass",
        "ebitda_ge_ebit": "pass",
        "cash_flow_tie": "pass",
        "required_items": "pass",
        "history_depth": "pass",
        "sign_conventions": "pass",
        "sector_eligibility": "pass",
        "stale_data": "pass",
    }
