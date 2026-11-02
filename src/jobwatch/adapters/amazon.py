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

API_HOST = "www.amazon.jobs"
PAGE_SIZE = 100
HITS_CAP = 10000
DEFAULT_MAX_PAGES = 100
_LOCALE_RE = re.compile(r"^[a-z]{2}$", re.I)


class AmazonAdapter:
    name = "amazon"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == API_HOST or host == "amazon.jobs"

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {API_HOST, "amazon.jobs"}:
            raise AdapterError(f"could not parse Amazon jobs host from {url}")
        identity = {"ats": self.name, "host": API_HOST}
        parts = [part for part in parsed.path.split("/") if part]
        if parts and _LOCALE_RE.match(parts[0]):
            identity["locale"] = parts[0].lower()
        return identity

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "amazon.jobs" not in html.lower() and not self.matches_url(url):
            return None
        if "amazon.jobs" not in html.lower() and "amazon.jobs" not in url.lower():
            return None
        try:
            fallback = url if self.matches_url(url) else "https://www.amazon.jobs/en/search"
            return self.parse_identity(fallback)
        except AdapterError:
            return {"ats": self.name, "host": API_HOST}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        host, locale = _identity(company, self)
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        offset = 0
        hits: int | None = None
        pages = 0
        while pages < max_pages and offset < HITS_CAP:
            payload = _page(client, company, host, locale, offset)
            error = payload.get("error")
            rows = payload.get("jobs") or []
            if not isinstance(rows, list):
                rows = []
            if error and not rows:
                if out and "10000" in str(error):
                    company.extra["amazon_truncated"] = True
                    break
                raise AdapterError(f"{company.id}: Amazon search error: {error}")
            if isinstance(payload.get("hits"), int):
                hits = payload["hits"]
                company.extra["amazon_hits"] = hits
                if hits >= HITS_CAP:
                    company.extra["amazon_truncated"] = True
            if not rows:
                break
            added = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                mapped = _map(company, item, include_content=include_content)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            offset += len(rows)
            pages += 1
            if added == 0:
                break
            if hits is not None and offset >= hits:
                break
            if len(rows) < PAGE_SIZE:
                break
        else:
            if hits is not None and offset < min(hits, HITS_CAP):
                company.extra["amazon_truncated"] = True
        return out


def _identity(company: Company, adapter: AmazonAdapter) -> tuple[str, str]:
    host = company.host
    locale = company.locale
    if not host or not locale:
        parsed = adapter.parse_identity(company.careers_url)
        host = host or parsed.get("host")
        locale = locale or parsed.get("locale")
    if host == "amazon.jobs":
        host = API_HOST
    return host or API_HOST, (locale or "en").lower()


def _page(
    client: httpx.Client,
    company: Company,
    host: str,
    locale: str,
    offset: int,
) -> dict[str, Any]:
    response = request(
        client,
        "GET",
        f"https://{host}/{locale}/search.json",
        params={"offset": offset, "result_limit": PAGE_SIZE},
    )
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Amazon HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Amazon HTTP {response.status_code} at offset {offset}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AdapterError(f"{company.id}: Amazon response was not JSON") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"{company.id}: Amazon response was not an object")
    return payload


def _map(company: Company, item: dict[str, Any], *, include_content: bool) -> NormalizedJob | None:
    job_id = item.get("id_icims") or item.get("id")
    title = (item.get("title") or "").strip()
    if job_id is None or not title:
        return None
    path = item.get("job_path") if isinstance(item.get("job_path"), str) else ""
    if path.startswith("/"):
        url = f"https://{API_HOST}{path}"
    elif path.startswith("http"):
        url = path
    else:
        url = f"https://{API_HOST}/en/jobs/{job_id}"
    labels, places, workplace = _locations(item)
    description = _description(item) if include_content else None
    posted = item.get("posted_date")
    team = item.get("team")
    return NormalizedJob(
        company_id=company.id,
        source="amazon",
        job_id=str(job_id),
        title=title,
        url=url,
        location=labels[0] if labels else None,
        places=places,
        department=team if isinstance(team, str) and team else None,
        workplace=workplace,
        posted_at=posted if isinstance(posted, str) else None,
        description=description,
        fingerprint=posted if isinstance(posted, str) else None,
    )


def _locations(item: dict[str, Any]) -> tuple[list[str], list, str | None]:
    labels: list[str] = []
    places = []
    workplace = None
    raw = item.get("locations")
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            label = entry.get("normalizedLocation") or entry.get("location")
            if not isinstance(label, str) or not label.strip():
                continue
            label = label.strip()
            labels.append(label)
            country = entry.get("countryIso2a")
            country = country if isinstance(country, str) else None
            places.append(classify_text(label, structured_country=country))
            if workplace is None and isinstance(entry.get("type"), str):
                workplace = entry["type"].lower()
    if not labels:
        label = item.get("location")
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
            code = item.get("country_code")
            country = code if isinstance(code, str) else None
            places.append(classify_text(label.strip(), structured_country=country))
    return labels, places, workplace


def _description(item: dict[str, Any]) -> str | None:
    parts = []
    for key in ("description", "basic_qualifications", "preferred_qualifications"):
        raw = item.get(key)
        if isinstance(raw, str):
            text = html_to_text(raw)
            if text:
                parts.append(text)
    return " ".join(parts) or None
