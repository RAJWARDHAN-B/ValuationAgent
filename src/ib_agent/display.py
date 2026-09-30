"""Console / CSV formatting for statements and QA reports."""

from __future__ import annotations

import csv
from pathlib import Path

from ib_agent.models.financials import FinancialStatements, Statement, Unit
from ib_agent.models.qa import QAReport

MISSING = "-"
STATEMENT_TITLES = {
    "IS": ("income_statement", "Income statement"),
    "BS": ("balance_sheet", "Balance sheet"),
    "CF": ("cash_flow", "Cash flow"),
    "derived": ("derived", "Derived metrics"),
}


def format_value(value: float | None, unit: Unit) -> str:
    """USD and shares in millions, EPS in dollars, ratios as percentages."""
    if value is None:
        return MISSING
    if unit == "USD/shares":
        return f"{value:,.2f}"
    if unit == "ratio":
        return f"{value:.1%}"
    return f"{value / 1e6:,.1f}"


def statement_table(statement: Statement, labels: list[str]) -> str:
    _, title = STATEMENT_TITLES[statement.kind]
    width = max([len(item) for item in statement.units] + [len(title) + 10])
    lines = [f"{title + ' ($mm)':<{width}}" + "".join(f"{label:>12}" for label in labels)]
    for item, unit in statement.units.items():
        cells = []
        for label in labels:
            line = statement.line(item, label)
            cells.append(format_value(None if line is None else line.value, unit))
        lines.append(f"{item:<{width}}" + "".join(f"{cell:>12}" for cell in cells))
    return "\n".join(lines)


def qa_table(report: QAReport) -> str:
    lines = [f"{c.status.upper():<5} {c.id:<24} {c.message}" for c in report.checks]
    lines.append(f"QA: {'passed' if report.passed else 'FAILED'}")
    return "\n".join(lines)


def write_csvs(fs: FinancialStatements, report: QAReport, directory: Path) -> list[Path]:
    """Raw (unscaled) values, one file per statement plus the QA report."""
    directory.mkdir(parents=True, exist_ok=True)
    labels = [p.label for p in fs.periods]
    written: list[Path] = []
    for statement in fs.statements:
        name, _ = STATEMENT_TITLES[statement.kind]
        path = directory / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["item", "unit", *labels])
            for item, unit in statement.units.items():
                row = statement.rows[item]
                writer.writerow(
                    [item, unit, *(row[lbl].value if lbl in row else "" for lbl in labels)]
                )
        written.append(path)

    path = directory / "qa.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "severity", "status", "message"])
        for check in report.checks:
            writer.writerow([check.id, check.severity, check.status, check.message])
    written.append(path)
    return written
