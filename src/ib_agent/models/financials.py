"""Normalized financial statements. Every value keeps the XBRL facts it came from."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PeriodKind = Literal["FY", "YTD", "LTM"]
Unit = Literal["USD", "shares", "USD/shares", "ratio"]
StatementKind = Literal["IS", "BS", "CF", "derived"]


class Fact(BaseModel):
    """One XBRL fact from the SEC company facts API."""

    model_config = ConfigDict(frozen=True)

    taxonomy: str
    tag: str
    unit: str
    value: float
    start: date | None
    end: date
    fy: int | None = None
    fp: str | None = None
    form: str
    filed: date
    accession: str
    frame: str | None = None

    @property
    def qname(self) -> str:
        return f"{self.taxonomy}:{self.tag}"

    @property
    def duration_days(self) -> int | None:
        return None if self.start is None else (self.end - self.start).days + 1


class Period(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    kind: PeriodKind
    start: date
    end: date


class FactRef(BaseModel):
    tag: str
    accession: str
    form: str
    filed: date
    start: date | None
    end: date
    value: float


class LineItemValue(BaseModel):
    value: float
    method: Literal["tag", "sum", "ltm", "derived"]
    sources: list[FactRef] = Field(default_factory=list)
    sign_flipped: bool = False
    formula: str | None = None

    @property
    def tag(self) -> str | None:
        return self.sources[0].tag if self.method == "tag" and self.sources else None


class Statement(BaseModel):
    kind: StatementKind
    units: dict[str, Unit]
    rows: dict[str, dict[str, LineItemValue]]
    """item -> period label -> value. Items with no value in a period are absent."""

    def line(self, item: str, period: str) -> LineItemValue | None:
        return self.rows.get(item, {}).get(period)


class FinancialStatements(BaseModel):
    periods: list[Period]
    """Fiscal years oldest to newest, then LTM last."""
    income_statement: Statement
    balance_sheet: Statement
    cash_flow: Statement
    derived: Statement
    outflow_items: list[str] = Field(default_factory=list)

    @property
    def fiscal_years(self) -> list[Period]:
        return [p for p in self.periods if p.kind == "FY"]

    @property
    def ltm(self) -> Period:
        return self.periods[-1]

    @property
    def statements(self) -> tuple[Statement, Statement, Statement, Statement]:
        return (self.income_statement, self.balance_sheet, self.cash_flow, self.derived)

    def line(self, item: str, period: str) -> LineItemValue | None:
        for statement in self.statements:
            if item in statement.units:
                return statement.line(item, period)
        return None

    def value(self, item: str, period: str) -> float | None:
        line = self.line(item, period)
        return None if line is None else line.value
