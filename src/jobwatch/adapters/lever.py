from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text, normalize_country
from jobwatch.models import Company, NormalizedJob

_GLOBAL_JOBS = {"jobs.lever.co"}
_EU_JOBS = {"jobs.eu.lever.co"}
_GLOBAL_API = {"api.lever.co"}
_EU_API = {"api.eu.lever.co"}
PAGE_SIZE = 100


class LeverAdapter:
    name = "lever"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in _GLOBAL_JOBS | _EU_JOBS | _GLOBAL_API | _EU_API or host.endswith(".lever.co")

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.split("/") if p]
        region = "eu" if host in _EU_JOBS | _EU_API else "global"
        if host in _GLOBAL_API | _EU_API:
            # /v0/postings/{site}
            if len(parts) >= 3 and parts[0] == "v0" and parts[1] == "postings":
                return {"board": parts[2], "region": region, "ats": self.name}
        if parts:
            return {"board": parts[0], "region": region, "ats": self.name}
        raise AdapterError(f"could not parse Lever site name from {url}")

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        lower = html.lower()
        if "jobs.eu.lever.co" in lower:
            match = _site_from_html(html, eu=True)
            return {"ats": self.name, "region": "eu", **({"board": match} if match else {})}
        if "jobs.lever.co" in lower or "api.lever.co" in lower:
            match = _site_from_html(html, eu=False)
            return {"ats": self.name, "region": "global", **({"board": match} if match else {})}
        return None

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content
        board = company.board or self.parse_identity(company.careers_url).get("board")
        if not board:
            raise AdapterError(f"{company.id}: Lever site name is missing")
        region = (company.region or "global").lower()
        jobs = _fetch_region(company, client, board, region)
        if not jobs and not company.region:
            other = "eu" if region == "global" else "global"
            jobs = _fetch_region(company, client, board, other)
            if jobs:
                company.region = other
        return jobs


def _api_host(region: str) -> str:
    return "https://api.eu.lever.co" if region == "eu" else "https://api.lever.co"


def _fetch_region(
    company: Company,
    client: httpx.Client,
    board: str,
    region: str,
) -> list[NormalizedJob]:
    out: list[NormalizedJob] = []
    skip = 0
    max_pages = company.max_pages if company.max_pages is not None else 50
    for _ in range(max_pages):
        url = f"{_api_host(region)}/v0/postings/{board}"
        response = request(
            client,
            "GET",
            url,
            params={"mode": "json", "skip": skip, "limit": PAGE_SIZE},
        )
        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            raise AdapterError(f"{company.id}: Lever HTTP {response.status_code} ({region})")
        payload = response.json()
        if not isinstance(payload, list):
            raise AdapterError(f"{company.id}: Lever returned a non-list body")
        for item in payload:
            mapped = _map(company, item)
            if mapped:
                out.append(mapped)
        if len(payload) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    return out


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    job_id = item.get("id")
    title = (item.get("text") or "").strip()
    url = item.get("hostedUrl") or item.get("applyUrl") or ""
    if not job_id or not title or not url:
        return None
    cats = item.get("categories") or {}
    country = normalize_country(item.get("country"))
    extras = cats.get("allLocations") or []
    texts: list[str] = []
    if isinstance(extras, list) and extras:
        texts = [str(x) for x in extras if x]
    elif cats.get("location"):
        texts = [str(cats["location"])]
    location = ", ".join(texts) if texts else None
    places = [
        classify_text(text, structured_country=country if i == 0 else None)
        for i, text in enumerate(texts)
    ]
    posted = item.get("createdAt")
    posted_at = None
    if isinstance(posted, (int, float)):
        posted_at = datetime.fromtimestamp(posted / 1000, tz=timezone.utc).isoformat()
    return NormalizedJob(
        company_id=company.id,
        source="lever",
        job_id=str(job_id),
        title=title,
        url=url,
        location=location,
        places=places,
        department=cats.get("team") or cats.get("department"),
        workplace=item.get("workplaceType"),
        posted_at=posted_at,
        description=item.get("descriptionPlain"),
        fingerprint=posted_at or str(job_id),
    )


def _site_from_html(html: str, *, eu: bool) -> str | None:
    host = "jobs.eu.lever.co" if eu else "jobs.lever.co"
    marker = f"{host}/"
    idx = html.lower().find(marker)
    if idx < 0:
        return None
    rest = html[idx + len(marker) :]
    site = rest.split("/", 1)[0].split('"', 1)[0].split("'", 1)[0]
    site = site.split("?", 1)[0].strip()
    return site or None
