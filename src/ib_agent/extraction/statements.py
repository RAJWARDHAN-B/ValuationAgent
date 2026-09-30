"""Tidy XBRL facts -> income statement, balance sheet, and cash flow by fiscal year plus LTM."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import Literal

from ib_agent.config import ValuationDefaults
from ib_agent.errors import ExtractionError
from ib_agent.extraction.xbrl import Component, TagMap, TagMapEntry, qualify
from ib_agent.models.financials import (
    Fact,
    FactRef,
    FinancialStatements,
    LineItemValue,
    Period,
    Statement,
    StatementKind,
    Unit,
)

ALLOWED_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})
FY_DAYS = (355, 375)
YTD_DAYS = (80, 290)
MIN_FY_GAP_DAYS = 300
DATE_SLACK_DAYS = 7
LTM_LABEL = "LTM"

PeriodKey = tuple[date | None, date]


class _FactIndex:
    """Deduplicated facts: one per (tag, unit, start, end), latest `filed` wins (restatements)."""

    def __init__(self, facts: Iterable[Fact]) -> None:
        latest: dict[tuple[str, str, date | None, date], Fact] = {}
        for fact in facts:
            if fact.form not in ALLOWED_FORMS:
                continue
            key = (fact.qname, fact.unit, fact.start, fact.end)
            current = latest.get(key)
            if current is None or (fact.filed, fact.accession) > (current.filed, current.accession):
                latest[key] = fact
        self._by_key = latest
        self.facts = list(latest.values())

    def get(self, qname: str, unit: str, key: PeriodKey) -> Fact | None:
        return self._by_key.get((qname, unit, key[0], key[1]))


def build_statements(
    facts: Iterable[Fact], tag_map: TagMap, defaults: ValuationDefaults
) -> FinancialStatements:
    index = _FactIndex(facts)
    duration_qnames = {q for _, e in tag_map.items() if not e.is_instant for q in e.qnames}

    fiscal_years = _fiscal_years(index, duration_qnames, defaults.history_years)
    if not fiscal_years:
        raise ExtractionError("No annual (10-K) XBRL facts found for the mapped line items.")
    latest_fy = fiscal_years[-1]
    ytd = _ytd_periods(index, duration_qnames, latest_fy)

    duration_keys: list[PeriodKey] = [(p.start, p.end) for p in fiscal_years]
    instant_keys: list[PeriodKey] = [(None, p.end) for p in fiscal_years]
    if ytd is not None:
        duration_keys += [(ytd[0].start, ytd[0].end), (ytd[1].start, ytd[1].end)]
        instant_keys.append((None, ytd[0].end))

    resolved: dict[str, dict[PeriodKey, LineItemValue]] = {}
    for item, entry in tag_map.items():
        keys = instant_keys if entry.is_instant else duration_keys
        values = {k: v for k in keys if (v := _resolve(entry, index, k, resolved)) is not None}
        resolved[item] = values

    if ytd is None:
        ltm = Period(label=LTM_LABEL, kind="LTM", start=latest_fy.start, end=latest_fy.end)
    else:
        ltm = Period(
            label=LTM_LABEL, kind="LTM", start=ytd[1].end + timedelta(days=1), end=ytd[0].end
        )

    rows: dict[str, dict[str, dict[str, LineItemValue]]] = {"IS": {}, "BS": {}, "CF": {}}
    units: dict[str, dict[str, Unit]] = {"IS": {}, "BS": {}, "CF": {}}
    for item, entry in tag_map.items():
        values = resolved[item]
        by_label: dict[str, LineItemValue] = {}
        for fy in fiscal_years:
            value = values.get((None, fy.end) if entry.is_instant else (fy.start, fy.end))
            if value is not None:
                by_label[fy.label] = value
        ltm_value = _ltm_value(entry, values, latest_fy, ytd)
        if ltm_value is not None:
            by_label[LTM_LABEL] = ltm_value
        rows[entry.statement][item] = by_label
        units[entry.statement][item] = entry.unit

    def statement(kind: StatementKind) -> Statement:
        return Statement(kind=kind, units=units[kind], rows=rows[kind])

    periods = [*fiscal_years, ltm]
    income, balance, cash_flow = statement("IS"), statement("BS"), statement("CF")
    return FinancialStatements(
        periods=periods,
        income_statement=income,
        balance_sheet=balance,
        cash_flow=cash_flow,
        derived=_derived(periods, (income, balance, cash_flow), defaults),
        outflow_items=[i for i, e in tag_map.items() if e.sign == "outflow_positive"],
    )


# ---- periods ----


def fiscal_year_label(end: date) -> str:
    # 52/53-week years ending in the first days of January belong to the prior fiscal year.
    year = end.year - 1 if end.month == 1 and end.day <= DATE_SLACK_DAYS else end.year
    return f"FY{year}"


def _fiscal_years(index: _FactIndex, duration_qnames: set[str], limit: int) -> list[Period]:
    """Annual periods labelled by the fact's `end` date; the `fy` field is the filing's year."""
    starts_by_end: dict[date, Counter[date]] = defaultdict(Counter)
    for fact in index.facts:
        days = fact.duration_days
        if (
            fact.start is not None
            and days is not None
            and fact.qname in duration_qnames
            and fact.form.startswith("10-K")
            and FY_DAYS[0] <= days <= FY_DAYS[1]
        ):
            starts_by_end[fact.end][fact.start] += 1

    ends: list[date] = []
    for end in sorted(starts_by_end, reverse=True):
        if not ends or (ends[-1] - end).days >= MIN_FY_GAP_DAYS:
            ends.append(end)
        if len(ends) == limit:
            break

    periods: list[Period] = []
    labels: set[str] = set()
    for end in reversed(ends):
        label = fiscal_year_label(end)
        if label in labels:
            label = f"{label}-{end:%m%d}"
        labels.add(label)
        start = starts_by_end[end].most_common(1)[0][0]
        periods.append(Period(label=label, kind="FY", start=start, end=end))
    return periods


def _ytd_periods(
    index: _FactIndex, duration_qnames: set[str], latest_fy: Period
) -> tuple[Period, Period] | None:
    """Current fiscal YTD after the latest FY, and the same YTD one year earlier."""
    first_day = latest_fy.end + timedelta(days=1)
    current: Counter[tuple[date, date]] = Counter()
    prior: Counter[tuple[date, date]] = Counter()
    for fact in index.facts:
        days = fact.duration_days
        if (
            fact.start is None
            or days is None
            or fact.qname not in duration_qnames
            or not fact.form.startswith("10-Q")
            or not YTD_DAYS[0] <= days <= YTD_DAYS[1]
        ):
            continue
        if abs((fact.start - first_day).days) <= DATE_SLACK_DAYS and fact.end > latest_fy.end:
            current[(fact.start, fact.end)] += 1
        elif abs((fact.start - latest_fy.start).days) <= DATE_SLACK_DAYS:
            prior[(fact.start, fact.end)] += 1
    if not current:
        return None

    cur_end = max(end for _, end in current)
    cur_start = max((n, s) for (s, e), n in current.items() if e == cur_end)[1]
    cur_days = (cur_end - cur_start).days
    matching = {
        k: n for k, n in prior.items() if abs((k[1] - k[0]).days - cur_days) <= DATE_SLACK_DAYS
    }
    if not matching:
        return None
    prior_key = max(matching, key=lambda k: (matching[k], k[1]))
    return (
        Period(label="YTD", kind="YTD", start=cur_start, end=cur_end),
        Period(label="YTD prior", kind="YTD", start=prior_key[0], end=prior_key[1]),
    )


# ---- line items ----


def _ref(fact: Fact) -> FactRef:
    return FactRef(
        tag=fact.qname,
        accession=fact.accession,
        form=fact.form,
        filed=fact.filed,
        start=fact.start,
        end=fact.end,
        value=fact.value,
    )


def _normalize(
    entry: TagMapEntry,
    value: float,
    method: Literal["tag", "sum"],
    sources: list[FactRef],
    formula: str | None,
) -> LineItemValue:
    flipped = entry.sign == "outflow_positive" and value < 0
    return LineItemValue(
        value=-value if flipped else value,
        method=method,
        sources=sources,
        sign_flipped=flipped,
        formula=formula,
    )


def _formula(components: list[Component]) -> str:
    parts = [f"{'-' if c.sign < 0 else '+'} {c.name}" for c in components]
    return " ".join(parts).removeprefix("+ ")


def _resolve(
    entry: TagMapEntry,
    index: _FactIndex,
    key: PeriodKey,
    resolved: dict[str, dict[PeriodKey, LineItemValue]],
) -> LineItemValue | None:
    for qname in entry.qnames:
        fact = index.get(qname, entry.unit, key)
        if fact is not None:
            return _normalize(entry, fact.value, "tag", [_ref(fact)], None)

    components = entry.components
    if not components:
        return None
    total = 0.0
    sources: list[FactRef] = []
    found = False
    for component in components:
        line: LineItemValue | None
        if component.is_item:
            line = resolved[component.name].get(key)
        else:
            fact = index.get(qualify(component.name), entry.unit, key)
            line = (
                None
                if fact is None
                else LineItemValue(value=fact.value, method="tag", sources=[_ref(fact)])
            )
        if line is None:
            if not component.optional:
                return None
            continue
        found = True
        total += component.sign * line.value
        sources.extend(line.sources)
    if not found:
        return None
    return _normalize(entry, total, "sum", sources, _formula(components))


def _ltm_value(
    entry: TagMapEntry,
    values: dict[PeriodKey, LineItemValue],
    latest_fy: Period,
    ytd: tuple[Period, Period] | None,
) -> LineItemValue | None:
    if ytd is None:
        fy_start = None if entry.is_instant else latest_fy.start
        return values.get((fy_start, latest_fy.end))
    current, prior = ytd
    if entry.is_instant:
        return values.get((None, current.end))
    cur = values.get((current.start, current.end))
    if entry.ltm == "latest":
        return cur
    fy = values.get((latest_fy.start, latest_fy.end))
    old = values.get((prior.start, prior.end))
    if cur is None or fy is None or old is None:
        return None
    return LineItemValue(
        value=fy.value + cur.value - old.value,
        method="ltm",
        sources=[*fy.sources, *cur.sources, *old.sources],
        sign_flipped=fy.sign_flipped or cur.sign_flipped or old.sign_flipped,
        formula="FY + YTD - prior YTD",
    )


# ---- derived metrics ----


def _derived(
    periods: list[Period],
    statements: tuple[Statement, Statement, Statement],
    defaults: ValuationDefaults,
) -> Statement:
    def get(item: str, label: str) -> float | None:
        for statement in statements:
            line = statement.line(item, label)
            if line is not None:
                return line.value
        return None

    rows: dict[str, dict[str, LineItemValue]] = {}

    def put(item: str, label: str, value: float, formula: str) -> None:
        rows.setdefault(item, {})[label] = LineItemValue(
            value=value, method="derived", formula=formula
        )

    previous_fy: str | None = None
    for period in periods:
        label = period.label
        ebit, da = get("operating_income", label), get("depreciation_amortization", label)
        if ebit is not None and da is not None:
            put("ebitda", label, ebit + da, "operating_income + depreciation_amortization")

        st_debt, lt_debt = get("short_term_debt", label), get("long_term_debt", label)
        debt: float | None = None
        if st_debt is not None or lt_debt is not None:
            debt = (st_debt or 0.0) + (lt_debt or 0.0)
            put("total_debt", label, debt, "short_term_debt + long_term_debt")

        cash, sti = get("cash", label), get("short_term_investments", label) or 0.0
        if debt is not None and cash is not None:
            put("net_debt", label, debt - cash - sti, "total_debt - cash - short_term_investments")

        tca, tcl = get("total_current_assets", label), get("total_current_liabilities", label)
        if tca is not None and tcl is not None and cash is not None:
            nwc = (tca - cash - sti) - (tcl - (st_debt or 0.0))
            put(
                "net_working_capital",
                label,
                nwc,
                "(total_current_assets - cash - short_term_investments)"
                " - (total_current_liabilities - short_term_debt)",
            )

        tax, pretax = get("income_tax_expense", label), get("pretax_income", label)
        if tax is not None and pretax is not None and pretax > 0:
            rate = min(max(tax / pretax, 0.0), defaults.max_tax_rate)
            put("effective_tax_rate", label, rate, "clip(income_tax_expense / pretax_income)")

        if period.kind == "FY" and previous_fy is not None:
            ufcf = _historical_ufcf(rows, get, label, previous_fy, defaults.statutory_tax_rate)
            if ufcf is not None:
                put("ufcf", label, ufcf, "EBIT * (1 - tax rate) + D&A - capex - change in NWC")
        if period.kind == "FY":
            previous_fy = label

    units: dict[str, Unit] = {item: "USD" for item in rows}
    if "effective_tax_rate" in units:
        units["effective_tax_rate"] = "ratio"
    return Statement(kind="derived", units=units, rows=rows)


def _historical_ufcf(
    rows: dict[str, dict[str, LineItemValue]],
    get: Callable[[str, str], float | None],
    label: str,
    previous: str,
    statutory_tax_rate: float,
) -> float | None:
    """Needs the prior fiscal year's NWC; falls back to the statutory rate if pretax <= 0."""
    nwc = rows.get("net_working_capital", {})
    ebit, da = get("operating_income", label), get("depreciation_amortization", label)
    capex = get("capex", label)
    if ebit is None or da is None or capex is None or label not in nwc or previous not in nwc:
        return None
    rate_line = rows.get("effective_tax_rate", {}).get(label)
    rate = rate_line.value if rate_line is not None else statutory_tax_rate
    return ebit * (1 - rate) + da - capex - (nwc[label].value - nwc[previous].value)
