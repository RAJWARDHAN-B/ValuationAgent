from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from ib_agent import __version__, cli
from ib_agent.config import Settings

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "fetch" in result.output and "version" in result.output


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def _use_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def test_fetch_rejects_invalid_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, sec_user_agent="T t@example.com")
    result = runner.invoke(cli.app, ["fetch", "../etc"])
    assert result.exit_code == 1
    assert "Invalid ticker" in result.output


def test_fetch_requires_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, sec_user_agent=None)
    result = runner.invoke(cli.app, ["fetch", "ACME"])
    assert result.exit_code == 1
    assert "SEC_USER_AGENT" in result.output


def test_fetch_end_to_end(monkeypatch: pytest.MonkeyPatch, make_client, tmp_path: Path) -> None:
    _use_settings(monkeypatch, sec_user_agent="T t@example.com", cache_dir=tmp_path)
    client, _ = make_client()
    monkeypatch.setattr(cli, "build_edgar_client", lambda *a, **k: client)

    result = runner.invoke(cli.app, ["fetch", "ACME"])

    assert result.exit_code == 0, result.output
    assert "Acme Widgets Inc. (ACME)  CIK 0000000001" in result.output
    assert "Latest 10-K: 0000000001-25-000005" in result.output
    assert "HTTP requests: 5  cache hits: 0" in result.output


COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"


def test_extract_end_to_end(monkeypatch: pytest.MonkeyPatch, make_client, tmp_path: Path) -> None:
    _use_settings(monkeypatch, sec_user_agent="T t@example.com", cache_dir=tmp_path)
    client, _ = make_client()
    monkeypatch.setattr(cli, "build_edgar_client", lambda *a, **k: client)

    result = runner.invoke(cli.app, ["extract", "ACME", "--csv", str(tmp_path / "csv")])

    assert result.exit_code == 0, result.output
    assert "LTM period: 2024-04-01 to 2025-03-31" in result.output
    revenue_row = next(line for line in result.output.splitlines() if line.startswith("revenue "))
    assert revenue_row.split()[1:] == ["700.0", "800.0", "900.0", "1,000.0", "1,030.0"]
    assert "QA: passed" in result.output
    assert sorted(p.name for p in (tmp_path / "csv").iterdir()) == [
        "balance_sheet.csv",
        "cash_flow.csv",
        "derived.csv",
        "income_statement.csv",
        "qa.csv",
    ]
    income = (tmp_path / "csv" / "income_statement.csv").read_text().splitlines()
    assert income[0] == "item,unit,FY2021,FY2022,FY2023,FY2024,LTM"
    assert income[1] == "revenue,USD,700000000.0,800000000.0,900000000.0,1000000000.0,1030000000.0"


def test_extract_exits_2_when_qa_fails(
    monkeypatch: pytest.MonkeyPatch, make_client, tmp_path: Path
) -> None:
    _use_settings(monkeypatch, sec_user_agent="T t@example.com", cache_dir=tmp_path)
    client, fake = make_client()
    payload = json.loads(fake.routes[COMPANY_FACTS_URL].content)
    payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][3]["val"] = 2_000_000_000
    fake.routes[COMPANY_FACTS_URL] = httpx.Response(200, content=json.dumps(payload).encode())
    monkeypatch.setattr(cli, "build_edgar_client", lambda *a, **k: client)

    result = runner.invoke(cli.app, ["extract", "ACME"])

    assert result.exit_code == 2, result.output
    assert "FAIL  balance_sheet_balances" in result.output
    assert "QA: FAILED" in result.output
