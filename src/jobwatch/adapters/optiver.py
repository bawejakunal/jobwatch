from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob

API_URL = "https://www.optiver.com/en/api/v1/jobs"
PAGE_SIZE = 16
DEFAULT_MAX_PAGES = 20
SITE = "https://www.optiver.com"


class OptiverAdapter:
    name = "optiver"

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"www.optiver.com", "optiver.com"}:
            return False
        return "/jobs" in parsed.path or parsed.path.rstrip("/") == "/en/api/v1/jobs"

    def parse_identity(self, url: str) -> dict[str, str]:
        if not self.matches_url(url):
            raise AdapterError(f"could not parse Optiver jobs URL from {url}")
        return {"ats": self.name, "host": "www.optiver.com"}

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "/en/api/v1/jobs" not in html and "optiver.com" not in html.lower():
            return None
        lower = html.lower()
        if "vacancy" not in lower and "/join-us/jobs" not in lower and not self.matches_url(url):
            return None
        return {"ats": self.name, "host": "www.optiver.com"}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content  # the list payload has no description
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        offset = 0
        total: int | None = None
        pages = 0
        while pages < max_pages:
            payload = _page(client, company, offset)
            if isinstance(payload.get("totalCount"), int):
                total = payload["totalCount"]
                company.extra["optiver_total"] = total
            rows = payload.get("items") or []
            if not isinstance(rows, list) or not rows:
                if pages == 0:
                    raise AdapterError(f"{company.id}: Optiver jobs API returned no jobs")
                break
            added = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                mapped = _map(company, item)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            offset += len(rows)
            pages += 1
            if added == 0:
                break
            if total is not None and offset >= total:
                break
            if len(rows) < PAGE_SIZE:
                break
        else:
            if total is None or offset < total:
                company.extra["optiver_truncated"] = True
        return out


def _page(client: httpx.Client, company: Company, offset: int) -> dict[str, Any]:
    response = request(client, "GET", API_URL, params={"from": offset})
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Optiver HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Optiver HTTP {response.status_code} at from={offset}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AdapterError(f"{company.id}: Optiver response was not JSON") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"{company.id}: Optiver response was not an object")
    return payload


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    title = item.get("title")
    href = item.get("href")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(href, str) or not href.startswith("/"):
        return None
    location = item.get("location")
    domain = item.get("domain")
    return NormalizedJob(
        company_id=company.id,
        source="optiver",
        job_id=href.strip("/"),
        title=title.strip(),
        url=f"{SITE}{href}",
        location=location if isinstance(location, str) else None,
        places=[classify_text(location)] if isinstance(location, str) and location else [],
        department=domain if isinstance(domain, str) else None,
    )
