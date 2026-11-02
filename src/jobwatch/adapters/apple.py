from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

API_HOST = "jobs.apple.com"
PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 400
_LOCALE_RE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.I)
_SEARCH_FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}


class AppleAdapter:
    name = "apple"

    def matches_url(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower() == API_HOST

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host != API_HOST:
            raise AdapterError(f"could not parse Apple jobs host from {url}")
        identity = {"ats": self.name, "host": host}
        parts = [part for part in parsed.path.split("/") if part]
        if parts and _LOCALE_RE.match(parts[0]):
            identity["locale"] = parts[0].lower()
        return identity

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "jobs.apple.com" not in html.lower() and not self.matches_url(url):
            return None
        try:
            if self.matches_url(url):
                return self.parse_identity(url)
        except AdapterError:
            pass
        return {"ats": self.name, "host": API_HOST, "locale": "en-us"}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        host, locale = _identity(company, self)
        token = _csrf(client, company, host)
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        total: int | None = None
        page = 1
        while page <= max_pages:
            payload = _search(client, company, host, locale, token, page)
            if isinstance(payload.get("totalRecords"), int):
                total = payload["totalRecords"]
                company.extra["apple_total"] = total
            rows = payload.get("searchResults") or []
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                mapped = _map(company, item, locale=locale, include_content=include_content)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            if added == 0:
                break
            if total is not None and page * PAGE_SIZE >= total:
                break
            if len(rows) < PAGE_SIZE:
                break
            page += 1
        else:
            if total is not None and (page - 1) * PAGE_SIZE < total:
                company.extra["apple_truncated"] = True
        return out


def _identity(company: Company, adapter: AppleAdapter) -> tuple[str, str]:
    host = company.host
    locale = company.locale
    if not host or not locale:
        parsed = adapter.parse_identity(company.careers_url)
        host = host or parsed.get("host")
        locale = locale or parsed.get("locale")
    return host or API_HOST, (locale or "en-us").lower()


def _csrf(client: httpx.Client, company: Company, host: str) -> str:
    response = request(client, "GET", f"https://{host}/api/v1/CSRFToken")
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Apple HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Apple CSRF HTTP {response.status_code}")
    token = response.headers.get("X-Apple-CSRF-Token") or response.headers.get("x-apple-csrf-token")
    if not token:
        raise AdapterError(f"{company.id}: Apple CSRF response had no token")
    return token


def _search(
    client: httpx.Client,
    company: Company,
    host: str,
    locale: str,
    token: str,
    page: int,
) -> dict[str, Any]:
    # A POST without this format block returns HTTP 200 and zero jobs.
    body = {
        "query": "",
        "filters": {},
        "page": page,
        "locale": locale,
        "sort": "newest",
        "format": dict(_SEARCH_FORMAT),
    }
    response = request(
        client,
        "POST",
        f"https://{host}/api/v1/search",
        headers={
            "Content-Type": "application/json",
            "X-Apple-CSRF-Token": token,
            "Referer": f"https://{host}/{locale}/search",
        },
        json=body,
    )
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Apple HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Apple search HTTP {response.status_code} on page {page}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AdapterError(f"{company.id}: Apple search response was not JSON") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"{company.id}: Apple search response was not an object")
    res = payload.get("res")
    if not isinstance(res, dict):
        raise AdapterError(f"{company.id}: Apple search response has no res object")
    return res


def _map(
    company: Company,
    item: dict[str, Any],
    *,
    locale: str,
    include_content: bool,
) -> NormalizedJob | None:
    job_id = item.get("id") or item.get("positionId")
    title = (item.get("postingTitle") or "").strip()
    if job_id is None or not title:
        return None
    position_id = item.get("positionId") or job_id
    slug = item.get("transformedPostingTitle")
    url = f"https://{API_HOST}/{locale}/details/{position_id}"
    if isinstance(slug, str) and slug:
        url = f"{url}/{slug}"
    labels, places = _locations(item)
    team = item.get("team")
    department = team.get("teamName") if isinstance(team, dict) else None
    posted = item.get("postDateInGMT")
    summary = item.get("jobSummary") if include_content else None
    description = html_to_text(summary) if isinstance(summary, str) else None
    return NormalizedJob(
        company_id=company.id,
        source="apple",
        job_id=str(job_id),
        title=title,
        url=url,
        location=labels[0] if labels else None,
        places=places,
        department=department if isinstance(department, str) else None,
        posted_at=posted if isinstance(posted, str) else None,
        description=description,
        fingerprint=posted if isinstance(posted, str) else None,
        detail_ref=str(position_id),
    )


def _locations(item: dict[str, Any]) -> tuple[list[str], list]:
    labels: list[str] = []
    places = []
    raw = item.get("locations")
    if not isinstance(raw, list):
        return labels, places
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        parts: list[str] = []
        for key in ("city", "stateProvince", "countryName"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip() and raw.strip() not in parts:
                parts.append(raw.strip())
        if not parts and isinstance(entry.get("name"), str) and entry["name"].strip():
            parts.append(entry["name"].strip())
        label = ", ".join(parts)
        if not label:
            continue
        labels.append(label)
        country = entry.get("countryName") if isinstance(entry.get("countryName"), str) else None
        places.append(classify_text(label, structured_country=country))
    return labels, places
