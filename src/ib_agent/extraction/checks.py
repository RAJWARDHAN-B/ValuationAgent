"""QA checks on extracted statements (implementation.md §8.3)."""

from __future__ import annotations

from datetime import date

from ib_agent.models.company import CompanyProfile
from ib_agent.models.financials import FinancialStatements
from ib_agent.models.qa import QACheck, QAReport, Severity

BALANCE_TOLERANCE = 0.005
TIE_TOLERANCE = 0.005
MIN_HISTORY_YEARS = 3
STALE_AFTER_DAYS = 456  # ~15 months
REQUIRED_ITEMS = (
    "revenue",
    "operating_income",
    "depreciation_amortization",
    "capex",
    "income_tax_expense",
    "cash",
    "total_debt",
    "diluted_shares",
)


def _result(
    check_id: str,
    severity: Severity,
    problems: list[str],
    ok_message: str,
    values: dict[str, float | str | None] | None = None,
) -> QACheck:
    return QACheck(
        id=check_id,
        severity=severity,
        status=severity if problems else "pass",
        message="; ".join(problems) if problems else ok_message,
        values=values or {},
    )


def _skip(check_id: str, severity: Severity, message: str) -> QACheck:
    return QACheck(id=check_id, severity=severity, status="skip", message=message)


def check_balance_sheet(fs: FinancialStatements) -> QACheck:
    problems: list[str] = []
    worst = 0.0
    for period in fs.periods:
        assets = fs.value("total_assets", period.label)
        liabilities = fs.value("total_liabilities", period.label)
        equity = fs.value("total_equity", period.label)
        if assets is None or liabilities is None or equity is None or assets == 0:
            problems.append(f"{period.label}: total assets, liabilities, or equity missing")
            continue
        diff = abs(assets - (liabilities + equity)) / abs(assets)
        worst = max(worst, diff)
        if diff >= BALANCE_TOLERANCE:
            problems.append(
                f"{period.label}: assets differ from liabilities + equity by {diff:.2%}"
            )
    return _result(
        "balance_sheet_balances",
        "fail",
        problems,
        "Assets = liabilities + equity in every period",
        {"max_relative_difference": worst},
    )


def check_gross_profit(fs: FinancialStatements) -> QACheck:
    problems: list[str] = []
    checked = 0
    for period in fs.periods:
        gross = fs.line("gross_profit", period.label)
        revenue = fs.value("revenue", period.label)
        cogs = fs.value("cost_of_revenue", period.label)
        if gross is None or gross.method == "sum" or revenue is None or cogs is None:
            continue
        checked += 1
        diff = abs(revenue - cogs - gross.value) / abs(revenue) if revenue else 0.0
        if diff >= TIE_TOLERANCE:
            problems.append(
                f"{period.label}: revenue - cost of revenue misses gross profit by {diff:.2%}"
            )
    if not checked:
        return _skip("gross_profit_tie", "warn", "Gross profit or cost of revenue not tagged")
    return _result("gross_profit_tie", "warn", problems, "Revenue - cost of revenue = gross profit")


def check_ebitda(fs: FinancialStatements) -> QACheck:
    problems: list[str] = []
    checked = 0
    for period in fs.periods:
        ebitda = fs.value("ebitda", period.label)
        ebit = fs.value("operating_income", period.label)
        if ebitda is None or ebit is None:
            continue
        checked += 1
        if ebitda < ebit:
            problems.append(f"{period.label}: EBITDA below EBIT (negative D&A)")
    if not checked:
        return _skip("ebitda_ge_ebit", "fail", "EBITDA not available")
    return _result("ebitda_ge_ebit", "fail", problems, "EBITDA >= EBIT in every period")


def check_cash_flow(fs: FinancialStatements) -> QACheck:
    problems: list[str] = []
    checked = 0
    for period in fs.periods:
        flows = [fs.value(i, period.label) for i in ("cfo", "cfi", "cff")]
        change = fs.value("net_change_in_cash", period.label)
        if change is None or any(f is None for f in flows):
            continue
        checked += 1
        cfo, cfi, cff = (f or 0.0 for f in flows)
        fx = fs.value("fx_effect", period.label) or 0.0
        scale = max(abs(cfo), abs(cfi), abs(cff)) or 1.0
        diff = abs(cfo + cfi + cff + fx - change) / scale
        if diff >= TIE_TOLERANCE:
            problems.append(
                f"{period.label}: CFO + CFI + CFF + FX misses change in cash by {diff:.2%}"
            )
    if not checked:
        return _skip("cash_flow_tie", "warn", "Cash flow totals or change in cash not tagged")
    return _result("cash_flow_tie", "warn", problems, "CFO + CFI + CFF + FX = change in cash")


def check_required_items(fs: FinancialStatements) -> QACheck:
    fiscal_years = fs.fiscal_years
    latest = fiscal_years[-1].label if fiscal_years else fs.ltm.label
    missing = [item for item in REQUIRED_ITEMS if fs.value(item, latest) is None]
    problems = [f"{latest} missing: {', '.join(missing)}"] if missing else []
    return _result("required_items", "fail", problems, f"All required items present for {latest}")


def check_history_depth(fs: FinancialStatements) -> QACheck:
    years = [p.label for p in fs.fiscal_years if fs.value("revenue", p.label) is not None]
    problems = (
        [f"Only {len(years)} fiscal year(s) with revenue; need {MIN_HISTORY_YEARS}"]
        if len(years) < MIN_HISTORY_YEARS
        else []
    )
    return _result(
        "history_depth", "fail", problems, f"{len(years)} fiscal years", {"years": len(years)}
    )


def check_signs(fs: FinancialStatements) -> QACheck:
    problems: list[str] = []
    for item in fs.outflow_items:
        lines = [fs.line(item, p.label) for p in fs.periods]
        present = [line for line in lines if line is not None]
        if any(line.value < 0 for line in present):
            problems.append(f"{item}: negative outflow")
        if len({line.sign_flipped for line in present}) > 1:
            problems.append(f"{item}: reported with inconsistent signs across periods")
    return _result("sign_conventions", "fail", problems, "Cash outflows stored as positive values")


def check_sector(profile: CompanyProfile) -> QACheck:
    problems = (
        [f"SIC {profile.sic} is a financial-sector code (6000-6799)"]
        if profile.is_financial_sector
        else []
    )
    return _result("sector_eligibility", "fail", problems, f"SIC {profile.sic} is eligible")


def check_staleness(fs: FinancialStatements, as_of: date) -> QACheck:
    fiscal_years = fs.fiscal_years
    if not fiscal_years:
        return _skip("stale_data", "warn", "No fiscal years")
    latest = fiscal_years[-1]
    age = (as_of - latest.end).days
    problems = (
        [f"Latest fiscal year ended {latest.end} ({age} days before {as_of})"]
        if age > STALE_AFTER_DAYS
        else []
    )
    return _result(
        "stale_data", "warn", problems, f"Latest fiscal year ended {latest.end}", {"age_days": age}
    )


def run_qa(fs: FinancialStatements, profile: CompanyProfile, *, as_of: date) -> QAReport:
    return QAReport(
        checks=[
            check_balance_sheet(fs),
            check_gross_profit(fs),
            check_ebitda(fs),
            check_cash_flow(fs),
            check_required_items(fs),
            check_history_depth(fs),
            check_signs(fs),
            check_sector(profile),
            check_staleness(fs, as_of),
        ]
    )
