"""Extraction agent: company facts -> normalized statements, gated by QA checks."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from ib_agent.agents.research import ResearchResult
from ib_agent.config import Defaults
from ib_agent.extraction.checks import run_qa
from ib_agent.extraction.statements import build_statements
from ib_agent.extraction.xbrl import TagMap, tidy_facts
from ib_agent.models.financials import FinancialStatements
from ib_agent.models.qa import QAReport


class ExtractionResult(BaseModel):
    statements: FinancialStatements
    qa: QAReport


def run_extraction(
    research: ResearchResult, defaults: Defaults, tag_map: TagMap, *, as_of: date
) -> ExtractionResult:
    facts = tidy_facts(research.company_facts, tag_map)
    statements = build_statements(facts, tag_map, defaults.valuation)
    return ExtractionResult(
        statements=statements, qa=run_qa(statements, research.company, as_of=as_of)
    )
