from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from ib_agent.data.cache import FileCache
from ib_agent.data.http import CachedHttpClient
from ib_agent.errors import HttpClientError


class NoopLimiter:
    def acquire(self) -> None:
        return None


def test_generic_host_is_allowed_and_cached(tmp_path: Path) -> None:
    calls = []
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, content=b'{"ok": true}')
        )
    )
    client = CachedHttpClient(
        {"rates.example.gov"},
        FileCache(tmp_path),
        http_client=http,
        rate_limiter=NoopLimiter(),  # type: ignore[arg-type]
        retry_wait=wait_none(),
    )
    url = "https://rates.example.gov/daily.csv"

    assert client.get_json(url, ttl_seconds=3600) == {"ok": True}
    assert client.get_json(url, ttl_seconds=3600) == {"ok": True}
    assert len(calls) == 1
    assert [source.from_cache for source in client.sources] == [False, True]


def test_generic_client_rejects_hosts_outside_allow_list(tmp_path: Path) -> None:
    client = CachedHttpClient({"rates.example.gov"}, FileCache(tmp_path))

    with pytest.raises(HttpClientError, match="non-HTTP service"):
        client.get_bytes("https://evil.example.com/data", ttl_seconds=None)