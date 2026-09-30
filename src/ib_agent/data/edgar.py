"""SEC EDGAR client: mandatory User-Agent, host allow-list, rate limit, retries, disk cache."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx
from tenacity.wait import wait_base

from ib_agent.config import Defaults, Settings
from ib_agent.data.cache import FileCache
from ib_agent.data.http import CachedHttpClient
from ib_agent.data.rate_limit import RateLimiter
from ib_agent.errors import ConfigError, EdgarError
from ib_agent.models.company import FilingRef
from ib_agent.models.sources import SourceRecord

SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        cache: FileCache,
        *,
        requests_per_second: float = 5.0,
        timeout_seconds: float = 30.0,
        max_attempts: int = 5,
        metadata_ttl_seconds: float = 86_400.0,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        if "@" not in user_agent:
            raise ConfigError("SEC User-Agent must include a contact email address.")
        self._http = CachedHttpClient(
            SEC_HOSTS,
            cache,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            service_name="SEC",
            error_type=EdgarError,
            requests_per_second=requests_per_second,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            http_client=http_client,
            rate_limiter=rate_limiter,
            retry_wait=retry_wait,
        )
        self.metadata_ttl_seconds = metadata_ttl_seconds

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @classmethod
    def from_settings(
        cls, settings: Settings, defaults: Defaults, *, refresh: bool = False
    ) -> EdgarClient:
        edgar = defaults.edgar
        return cls(
            settings.require_sec_user_agent(),
            FileCache(settings.cache_dir / "http"),
            requests_per_second=edgar.requests_per_second,
            timeout_seconds=edgar.timeout_seconds,
            max_attempts=edgar.max_attempts,
            metadata_ttl_seconds=0.0 if refresh else edgar.metadata_ttl_hours * 3600,
        )

    def close(self) -> None:
        self._http.close()

    @property
    def sources(self) -> list[SourceRecord]:
        return self._http.sources

    @property
    def network_requests(self) -> int:
        return self._http.network_requests

    # ---- endpoints ----

    def company_tickers(self) -> dict[str, Any]:
        return self.get_json(TICKERS_URL, ttl_seconds=self.metadata_ttl_seconds)

    def submissions(self, cik: int) -> dict[str, Any]:
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        return self.get_json(url, ttl_seconds=self.metadata_ttl_seconds)

    def company_facts(self, cik: int) -> dict[str, Any]:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        return self.get_json(url, ttl_seconds=self.metadata_ttl_seconds)

    def filing_document(self, filing: FilingRef, cik: int) -> bytes:
        # Archived filings are immutable, so they never expire from the cache.
        return self.get_bytes(filing.document_url(cik), ttl_seconds=None)

    # ---- transport ----

    def get_json(self, url: str, *, ttl_seconds: float | None) -> Any:
        return self._http.get_json(url, ttl_seconds=ttl_seconds)

    def get_bytes(self, url: str, *, ttl_seconds: float | None) -> bytes:
        return self._http.get_bytes(url, ttl_seconds=ttl_seconds)
