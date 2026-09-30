"""Cached HTTP transport with host allow-listing, rate limiting, and retries."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from ib_agent.data.cache import FileCache
from ib_agent.data.rate_limit import RateLimiter
from ib_agent.errors import HttpClientError
from ib_agent.models.sources import SourceRecord


class _RetryableStatus(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class CachedHttpClient:
    def __init__(
        self,
        allowed_hosts: Collection[str],
        cache: FileCache,
        *,
        headers: Mapping[str, str] | None = None,
        service_name: str = "HTTP service",
        error_type: type[Exception] = HttpClientError,
        requests_per_second: float = 5.0,
        timeout_seconds: float = 30.0,
        max_attempts: int = 5,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._cache = cache
        self._headers = dict(headers or {})
        self._service_name = service_name
        self._error_type = error_type
        self._http = http_client or httpx.Client(timeout=timeout_seconds)
        self._limiter = rate_limiter or RateLimiter(requests_per_second)
        self._max_attempts = max_attempts
        self._retry_wait = retry_wait or wait_exponential_jitter(initial=1, max=30)
        self.sources: list[SourceRecord] = []
        self.network_requests = 0

    def __enter__(self) -> CachedHttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_json(self, url: str, *, ttl_seconds: float | None) -> Any:
        body = self.get_bytes(url, ttl_seconds=ttl_seconds)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise self._error_type(f"Invalid JSON from {url}") from exc

    def get_bytes(self, url: str, *, ttl_seconds: float | None) -> bytes:
        self._check_url(url)
        entry = self._cache.get(url, ttl_seconds)
        if entry is not None:
            self._record(url, entry.fetched_at, from_cache=True)
            return entry.body
        entry = self._cache.put(url, self._fetch(url))
        self._record(url, entry.fetched_at, from_cache=False)
        return entry.body

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self._allowed_hosts:
            raise self._error_type(f"Refusing to fetch non-{self._service_name} URL: {url}")

    def _fetch(self, url: str) -> bytes:
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._retry_wait,
            retry=retry_if_exception_type((httpx.TransportError, _RetryableStatus)),
            reraise=True,
        )
        try:
            for attempt in retrying:
                with attempt:
                    self._limiter.acquire()
                    self.network_requests += 1
                    response = self._http.get(url, headers=self._headers)
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _RetryableStatus(response.status_code)
                    if response.status_code != 200:
                        raise self._error_type(
                            f"{self._service_name} returned HTTP {response.status_code} for {url}"
                        )
                    return response.content
        except (httpx.TransportError, _RetryableStatus) as exc:
            raise self._error_type(
                f"{self._service_name} request failed after {self._max_attempts} attempts "
                f"({exc}): {url}"
            ) from exc
        raise AssertionError("unreachable")

    def _record(self, url: str, fetched_at: float, *, from_cache: bool) -> None:
        self.sources.append(
            SourceRecord(
                url=url,
                retrieved_at=datetime.fromtimestamp(fetched_at, tz=UTC),
                from_cache=from_cache,
            )
        )