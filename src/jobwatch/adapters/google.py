from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results/"
PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 250
_MARKERS = (
    "AF_initDataCallback({key: 'ds:1'",
    'AF_initDataCallback({key: "ds:1"',
)


class GoogleAdapter:
    name = "google"

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"www.google.com", "google.com"}:
            return False
        return "/about/careers" in parsed.path

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"www.google.com", "google.com"} or "/about/careers" not in parsed.path:
            raise AdapterError(f"could not parse Google careers URL from {url}")
        return {"ats": self.name, "host": "www.google.com"}

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if not any(marker in html for marker in _MARKERS) and "/about/careers" not in html:
            return None
        if "google.com/about/careers" not in html and not self.matches_url(url):
            return None
        return {"ats": self.name, "host": "www.google.com"}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        page = 1
        while page <= max_pages:
            rows = _page(client, company, page)
            if not rows:
                if page == 1:
                    raise AdapterError(f"{company.id}: Google results page has no job records")
                break
            added = 0
            for item in rows:
                mapped = _map(company, item, include_content=include_content)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            if added == 0:
                if page == 1 and not out:
                    raise AdapterError(f"{company.id}: Google results page has no job records")
                break
            if len(rows) < PAGE_SIZE:
                break
            page += 1
        else:
            company.extra["google_truncated"] = True
        return out


def _page(client: httpx.Client, company: Company, page: int) -> list[list[Any]]:
    response = request(client, "GET", RESULTS_URL, params={"page": page})
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Google HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Google HTTP {response.status_code} on page {page}")
    return _rows(company, response.text, page)


def _rows(company: Company, html: str, page: int) -> list[list[Any]]:
    start = -1
    for marker in _MARKERS:
        start = html.find(marker)
        if start >= 0:
            break
    if start < 0:
        if page == 1:
            raise AdapterError(f"{company.id}: Google results page has no job payload")
        return []
    data_at = html.find("data:", start)
    if data_at < 0:
        raise AdapterError(f"{company.id}: Google job payload has no data array")
    try:
        data, _ = json.JSONDecoder().raw_decode(html[data_at + len("data:") :])
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{company.id}: Google job payload was not JSON") from exc
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return []
    rows = []
    for item in data[0]:
        if isinstance(item, list) and item and isinstance(item[0], str) and item[0].isdigit():
            rows.append(item)
    return rows


def _map(company: Company, item: list[Any], *, include_content: bool) -> NormalizedJob | None:
    job_id = item[0]
    title = item[1].strip() if len(item) > 1 and isinstance(item[1], str) else ""
    if not job_id or not title:
        return None
    labels, places = _locations(item)
    posted = _posted(item)
    description = _description(item) if include_content else None
    return NormalizedJob(
        company_id=company.id,
        source="google",
        job_id=job_id,
        title=title,
        url=f"{RESULTS_URL}{job_id}",
        location=labels[0] if labels else None,
        places=places,
        posted_at=posted,
        description=description,
        fingerprint=posted,
    )


def _locations(item: list[Any]) -> tuple[list[str], list]:
    labels: list[str] = []
    places = []
    raw = item[9] if len(item) > 9 else None
    if not isinstance(raw, list):
        return labels, places
    for entry in raw:
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], str):
            continue
        label = entry[0].strip()
        if not label:
            continue
        labels.append(label)
        country = entry[5] if len(entry) > 5 and isinstance(entry[5], str) else None
        places.append(classify_text(label, structured_country=country))
    return labels, places


def _posted(item: list[Any]) -> str | None:
    raw = item[12] if len(item) > 12 else None
    if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
        seconds = raw[0] / 1000 if raw[0] > 10_000_000_000 else raw[0]
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _description(item: list[Any]) -> str | None:
    parts = []
    for index in (10, 4, 3):
        text = html_to_text(_html_field(item, index))
        if text and text not in parts:
            parts.append(text)
    return " ".join(parts) or None


def _html_field(item: list[Any], index: int) -> str | None:
    if index >= len(item):
        return None
    value = item[index]
    if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], str):
        return value[1]
    if isinstance(value, str):
        return value
    return None
