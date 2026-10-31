from __future__ import annotations

import random
import time
from collections import defaultdict
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from jobwatch.adapters.base import AdapterError

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

RETRY_STATUSES = {429, 502, 503, 504}


class RateLimiter:
    def __init__(self, rps: float) -> None:
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self._last: dict[str, float] = defaultdict(float)

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        due = self._last[host] + self.min_interval
        if due > now:
            time.sleep(due - now)
        self._last[host] = time.monotonic()


def build_client(*, timeout: float = 25.0, headers: dict[str, str] | None = None) -> httpx.Client:
    merged = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        merged.update(headers)
    return httpx.Client(
        timeout=httpx.Timeout(timeout),
        headers=merged,
        follow_redirects=True,
        http2=False,
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        return max(0.0, when.timestamp() - time.time())


def request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    retries: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    host = httpx.URL(url).host or ""
    last_exc: Exception | None = None
    for attempt in range(retries):
        if limiter:
            limiter.wait(host)
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            time.sleep(_backoff(attempt))
            continue
        if response.status_code in RETRY_STATUSES and attempt + 1 < retries:
            wait = _retry_after_seconds(response)
            time.sleep(wait if wait is not None else _backoff(attempt))
            continue
        return response
    raise AdapterError(f"request failed after {retries} tries: {url} ({last_exc})", retryable=True)


def _backoff(attempt: int) -> float:
    return (0.4 * (2**attempt)) + random.uniform(0, 0.2)
