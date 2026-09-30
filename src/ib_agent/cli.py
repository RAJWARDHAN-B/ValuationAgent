"""Command-line interface: `ib-agent`."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from ib_agent import __version__
from ib_agent.agents.extraction import run_extraction
from ib_agent.agents.research import ResearchResult, run_research
from ib_agent.config import Defaults, Settings, get_settings, load_defaults
from ib_agent.data.edgar import EdgarClient
from ib_agent.display import qa_table, statement_table, write_csvs
from ib_agent.errors import IBAgentError
from ib_agent.extraction.xbrl import load_tag_map

QA_FAILED_EXIT_CODE = 2

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Autonomous AI investment banking analyst: SEC filings to DCF model and pitch deck.",
)


def build_edgar_client(settings: Settings, defaults: Defaults, *, refresh: bool) -> EdgarClient:
    return EdgarClient.from_settings(settings, defaults, refresh=refresh)


@app.callback()
def main() -> None:
    """Autonomous AI investment banking analyst."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def fetch(
    ticker: Annotated[str, typer.Argument(help="US-listed ticker, e.g. MSFT")],
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Ignore cached SEC metadata (filings stay cached).")
    ] = False,
) -> None:
    """Download and cache SEC data for TICKER: profile, XBRL facts, latest 10-K / 10-Q."""
    try:
        settings = get_settings()
        defaults = load_defaults(settings.config_dir)
        with build_edgar_client(settings, defaults, refresh=refresh) as client:
            result = run_research(client, ticker)
            _print_research(result, client.network_requests)
    except IBAgentError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def extract(
    ticker: Annotated[str, typer.Argument(help="US-listed ticker, e.g. MSFT")],
    csv_dir: Annotated[
        Path | None,
        typer.Option("--csv", help="Also write raw statement values and QA results as CSV here."),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Ignore cached SEC metadata (filings stay cached).")
    ] = False,
) -> None:
    """Build IS / BS / CF (fiscal years + LTM) from XBRL and run QA. Exit code 2 on QA failure."""
    try:
        settings = get_settings()
        defaults = load_defaults(settings.config_dir)
        tag_map = load_tag_map(settings.config_dir / "xbrl_tag_map.yaml")
        with build_edgar_client(settings, defaults, refresh=refresh) as client:
            research = run_research(client, ticker)
        result = run_extraction(research, defaults, tag_map, as_of=date.today())
    except IBAgentError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    company = research.company
    fs = result.statements
    labels = [p.label for p in fs.periods]
    typer.echo(f"{company.name} ({company.ticker})  CIK {company.cik:010d}")
    typer.echo(f"LTM period: {fs.ltm.start} to {fs.ltm.end}")
    for statement in fs.statements:
        typer.echo("")
        typer.echo(statement_table(statement, labels))
    typer.echo("")
    typer.echo(qa_table(result.qa))
    if csv_dir is not None:
        for path in write_csvs(fs, result.qa, csv_dir):
            typer.echo(f"Wrote {path}")
    if not result.qa.passed:
        raise typer.Exit(code=QA_FAILED_EXIT_CODE)


def _print_research(result: ResearchResult, network_requests: int) -> None:
    company = result.company
    ten_k, ten_q = result.latest_10k, result.latest_10q
    concepts = len(result.company_facts.get("facts", {}).get("us-gaap", {}))
    cache_hits = sum(s.from_cache for s in result.sources)

    typer.echo(f"{company.name} ({company.ticker})  CIK {company.cik:010d}")
    typer.echo(f"SIC {company.sic} {company.sic_description or ''}  FYE {company.fiscal_year_end}")
    typer.echo(
        f"Latest 10-K: {ten_k.accession_number} filed {ten_k.filing_date} "
        f"(period {ten_k.report_date})"
    )
    if ten_q is not None:
        typer.echo(
            f"Latest 10-Q: {ten_q.accession_number} filed {ten_q.filing_date} "
            f"(period {ten_q.report_date})"
        )
    else:
        typer.echo("Latest 10-Q: none since the latest 10-K")
    typer.echo(f"XBRL us-gaap concepts: {concepts}")
    typer.echo(f"HTTP requests: {network_requests}  cache hits: {cache_hits}")
