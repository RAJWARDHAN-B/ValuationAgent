# How to Run

This guide covers everything that currently works: building the containers, fetching SEC data, extracting financial statements with QA checks, and running the tests. For design and roadmap, see [implementation.md](implementation.md).

## 1. Prerequisites

- **Docker** with Compose v2 (Docker Desktop, Rancher Desktop, or Docker Engine). Check with `docker compose version`.
- **An SEC User-Agent**: your name and a contact email. The SEC requires it on every request and blocks anonymous traffic.
- Optional: **Python 3.12+** if you want to run without Docker (see §6).

## 2. One-Time Setup

```bash
cd ValuationAgent
cp .env.example .env
```

Edit `.env` and set your contact details:

```dotenv
SEC_USER_AGENT="Jane Doe jane.doe@example.com"
```

Leave `LLM_PROVIDER=fake`. No LLM is used yet.

Build the images (the first build downloads LibreOffice and takes a while):

```bash
make build
```

Rebuild with `make build` after pulling new code or changing `src/`, `config/`, or `pyproject.toml`. The `analyst` image bakes in the package and `config/`. The `tests` service bind-mounts `src/`, `tests/`, and `config/`, so tests always see your working copy.

## 3. Commands

All commands run inside the `analyst` container. `T` is the ticker (default `MSFT`).

| Task | Make | Direct Compose equivalent |
|---|---|---|
| Show help | — | `docker compose run --rm analyst --help` |
| Print version | — | `docker compose run --rm analyst version` |
| Fetch and cache SEC data | `make fetch T=AAPL` | `docker compose run --rm analyst fetch AAPL` |
| Extract statements + QA | `make extract T=AAPL` | `docker compose run --rm analyst extract AAPL` |
| Open a shell in the image | `make shell` | `docker compose run --rm --entrypoint bash analyst` |

### `fetch TICKER [--refresh]`

Resolves the ticker to a CIK, loads the company profile, refuses banks / insurers / REITs (SIC 6000–6799), and downloads the XBRL company facts plus the latest 10-K and any newer 10-Q.

```text
$ make fetch T=MSFT
MICROSOFT CORP (MSFT)  CIK 0000789019
SIC 7372 Services-Prepackaged Software  FYE 0630
Latest 10-K: ... filed ... (period ...)
Latest 10-Q: ... filed ... (period ...)
XBRL us-gaap concepts: ...
HTTP requests: 5  cache hits: 0
```

Run it again and `HTTP requests` drops to 0: every response is served from the cache. `--refresh` re-downloads metadata (tickers, submissions, company facts). Filing documents never change, so they stay cached.

### `extract TICKER [--csv DIR] [--refresh]`

Runs `fetch`, then builds the income statement, balance sheet, and cash flow statement for up to 5 fiscal years plus LTM, followed by derived metrics (EBITDA, total debt, net debt, net working capital, effective tax rate, historical UFCF) and the QA report.

- Amounts are shown in **$ millions**. Shares are in millions and EPS is in dollars.
- `--csv DIR` also writes `income_statement.csv`, `balance_sheet.csv`, `cash_flow.csv`, `derived.csv`, and `qa.csv` with raw (unscaled) values. Inside Docker, use a path under `/app/outputs` so the files show up in `./outputs` on the host:

  ```bash
  docker compose run --rm analyst extract MSFT --csv /app/outputs/MSFT
  ```

- **Exit codes:** `0` = QA passed (warnings allowed), `1` = error (bad ticker, missing `SEC_USER_AGENT`, unsupported company, SEC failure), `2` = at least one QA check failed. The statements are still printed when QA fails.

## 4. Tests and Lint

```bash
make test   # pytest in the `tests` container, network disabled (network_mode: none)
make lint   # ruff check + ruff format --check + mypy
make fmt    # auto-fix lint issues and format
```

Tests use recorded fixtures under `tests/fixtures/` and never touch the network. Tests marked `live` are excluded by default.

Run a subset:

```bash
docker compose run --rm tests pytest tests/unit/test_statements.py -q
docker compose run --rm tests pytest -k ltm -q
```

## 5. Where Things Live

| Path | Contents |
|---|---|
| `.env` | Your settings (gitignored) |
| `config/defaults.yaml` | EDGAR client and valuation defaults |
| `config/xbrl_tag_map.yaml` | Canonical line items → ordered XBRL tags |
| `ib_cache` Docker volume (`/app/.cache`) | HTTP cache of SEC responses |
| `./outputs` (`/app/outputs`) | Generated files |

Clear the SEC cache:

```bash
docker volume rm ib-agent_ib_cache
```

## 6. Running Without Docker (Optional)

Requires Python 3.12 or newer (the macOS system Python 3.9 won't work).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

export SEC_USER_AGENT="Jane Doe jane.doe@example.com"   # or rely on .env in the repo root
ib-agent fetch MSFT
ib-agent extract MSFT

pytest
ruff check src tests && ruff format --check src tests && mypy
```

Without Docker, the cache goes to `./.cache` and outputs go to `./outputs`. Override with `IB_AGENT_CACHE_DIR`, `IB_AGENT_OUTPUT_DIR`, and `IB_AGENT_CONFIG_DIR`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `SEC_USER_AGENT must be set ...` | Set it in `.env` with a real email, then re-run. No rebuild needed |
| `Invalid ticker` | Tickers are 1–10 characters: letters, digits, `.` or `-`. Share classes like `BRK.B` are fine |
| `... is not supported` (SIC 6xxx) | Banks, insurers, and REITs are refused by design |
| `SEC returned HTTP 403` | The SEC is rejecting your User-Agent. Use a real name and email |
| `extract` exits with code 2 | A QA check failed. Read the QA table; common causes are XBRL tags missing from `config/xbrl_tag_map.yaml` or a balance sheet that doesn't tie |
| `make extract` says `No such command 'extract'`, or changes to `src/` / `config/` aren't picked up | Run `make build` again (the `analyst` image bakes in the code and `config/`) |
| `make analyze` fails | Not implemented yet (Phase 8) |
