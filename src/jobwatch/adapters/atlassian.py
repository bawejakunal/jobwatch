from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob

JOBS_URL = "https://www.atlassian.com/company/careers/all-jobs"


class AtlassianAdapter:
    """Atlassian's iCIMS portals redirect to the marketing page, which embeds the list."""

    name = "atlassian"

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"www.atlassian.com", "atlassian.com"}:
            return False
        return "/company/careers" in parsed.path

    def parse_identity(self, url: str) -> dict[str, str]:
        if not self.matches_url(url):
            raise AdapterError(f"could not parse Atlassian careers URL from {url}")
        return {"ats": self.name, "host": "www.atlassian.com"}

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "applyUrl" not in html or "atlassian" not in html.lower():
            return None
        return {"ats": self.name, "host": "www.atlassian.com"}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content  # the embedded list has no description
        response = request(client, "GET", JOBS_URL)
        if response.status_code == 429:
            raise AdapterError(f"{company.id}: Atlassian HTTP 429", retryable=True)
        if response.status_code >= 400:
            raise AdapterError(f"{company.id}: Atlassian HTTP {response.status_code}")
        rows = _rows(company, response.text)
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        for item in rows:
            mapped = _map(company, item)
            if not mapped or mapped.job_id in seen:
                continue
            seen.add(mapped.job_id)
            out.append(mapped)
        if not out:
            raise AdapterError(f"{company.id}: Atlassian careers page has no job records")
        return out


def _rows(company: Company, html: str) -> list[dict[str, Any]]:
    start = 0
    while True:
        found = html.find('"applyUrl"', start)
        if found < 0:
            break
        window_start = max(0, found - 800)
        marker = html.rfind('"value":[', window_start, found)
        if marker < 0:
            start = found + len('"applyUrl"')
            continue
        data_at = marker + len('"value":')
        try:
            data, _ = json.JSONDecoder().raw_decode(html[data_at:])
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{company.id}: Atlassian job list was not JSON") from exc
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        start = found + len('"applyUrl"')
    raise AdapterError(f"{company.id}: Atlassian careers page has no job payload")


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else item
    job_id = fields.get("id")
    title = fields.get("title")
    url = fields.get("applyUrl")
    if not isinstance(job_id, str) or not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    url = url.split("?", 1)[0]
    labels = []
    places = []
    locations = fields.get("locations")
    if isinstance(locations, list):
        for entry in locations:
            if isinstance(entry, str) and entry.strip():
                label = entry.strip()
                labels.append(label)
                places.append(classify_text(label))
    category = fields.get("category")
    return NormalizedJob(
        company_id=company.id,
        source="atlassian",
        job_id=job_id,
        title=title.strip(),
        url=url,
        location=labels[0] if labels else None,
        places=places,
        department=category if isinstance(category, str) else None,
    )
