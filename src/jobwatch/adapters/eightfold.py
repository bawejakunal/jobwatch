from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

PAGE_SIZE = 10
DEFAULT_MAX_PAGES = 250

# Public careers hosts whose Eightfold domain is not in the URL.
_DEFAULT_DOMAIN = {
    "apply.careers.microsoft.com": "microsoft.com",
    "explore.jobs.netflix.net": "netflix.com",
}


class EightfoldAdapter:
    name = "eightfold"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if host in _DEFAULT_DOMAIN or host.endswith(".eightfold.ai"):
            return True
        path = urlparse(url).path
        return "/api/pcsx/" in path or path.endswith("/api/apply/v2/jobs")

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            raise AdapterError(f"could not parse Eightfold host from {url}")
        identity: dict[str, str] = {"ats": self.name, "host": host}
        domain = (parse_qs(parsed.query).get("domain") or [None])[0]
        if not domain and host in _DEFAULT_DOMAIN:
            domain = _DEFAULT_DOMAIN[host]
        if domain:
            identity["board"] = domain
        return identity

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        lower = html.lower()
        if "eightfold" not in lower and "pcsx" not in lower and "/api/apply/v2/jobs" not in lower:
            return None
        try:
            identity = self.parse_identity(url)
        except AdapterError:
            identity = {"ats": self.name}
        return identity

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        host, domain = _identity(company, self)
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        total: int | None = None
        truncated = False
        style: str | None = None

        for page in range(max_pages):
            start = page * PAGE_SIZE
            if style is None:
                style, payload = _first_page(client, company, host, domain)
                company.extra["_eightfold"] = {"host": host, "board": domain, "style": style}
            else:
                payload = _page(client, company, host, domain, start, style)
            if page == 0:
                raw_total = payload.get("count")
                if isinstance(raw_total, int):
                    total = raw_total
            rows = payload.get("positions") or []
            if not isinstance(rows, list) or not rows:
                break
            added = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                mapped = _map(company, item, host=host, include_content=include_content)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            if added == 0:
                break
            if total is not None and start + len(rows) >= total:
                break
            if len(rows) < PAGE_SIZE:
                break
        else:
            if total is not None and len(out) < total:
                truncated = True

        if truncated:
            company.extra["eightfold_truncated"] = True
        return out

    def enrich_one(self, company: Company, client: httpx.Client, job: NormalizedJob) -> str | None:
        ident = company.extra.get("_eightfold") or {}
        host = ident.get("host") or company.host
        domain = ident.get("board") or company.board
        if not host or not domain:
            return None
        response = request(
            client,
            "GET",
            f"https://{host}/api/pcsx/position_details",
            params={"domain": domain, "position_id": job.detail_ref or job.job_id},
        )
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        html = data.get("jobDescription")
        return html_to_text(html) if isinstance(html, str) else None


def _identity(company: Company, adapter: EightfoldAdapter) -> tuple[str, str]:
    host = company.host
    domain = company.board
    if not host or not domain:
        parsed = adapter.parse_identity(company.careers_url)
        host = host or parsed.get("host")
        domain = domain or parsed.get("board")
    if not host or not domain:
        raise AdapterError(
            f"{company.id}: Eightfold needs host and domain "
            "(set host and board, or a careers URL with ?domain=)"
        )
    return host, domain


def _first_page(
    client: httpx.Client,
    company: Company,
    host: str,
    domain: str,
) -> tuple[str, dict[str, Any]]:
    response = request(
        client,
        "GET",
        f"https://{host}/api/pcsx/search",
        params={"domain": domain, "query": "", "start": 0, "num": PAGE_SIZE},
    )
    if response.status_code == 403:
        return "v2", _page(client, company, host, domain, 0, "v2")
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Eightfold HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Eightfold HTTP {response.status_code} for pcsx search")
    return "pcsx", _pcsx_data(company, response)


def _page(
    client: httpx.Client,
    company: Company,
    host: str,
    domain: str,
    start: int,
    style: str,
) -> dict[str, Any]:
    if style == "pcsx":
        response = request(
            client,
            "GET",
            f"https://{host}/api/pcsx/search",
            params={"domain": domain, "query": "", "start": start, "num": PAGE_SIZE},
        )
    else:
        response = request(
            client,
            "GET",
            f"https://{host}/api/apply/v2/jobs",
            params={"domain": domain, "start": start, "num": PAGE_SIZE, "sort_by": "timestamp"},
        )
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Eightfold HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Eightfold HTTP {response.status_code} at start={start}")
    if style == "pcsx":
        return _pcsx_data(company, response)
    return _json_object(company, response)


def _pcsx_data(company: Company, response: httpx.Response) -> dict[str, Any]:
    payload = _json_object(company, response)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AdapterError(f"{company.id}: Eightfold PCSX payload has no data object")
    return data


def _json_object(company: Company, response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AdapterError(f"{company.id}: Eightfold response was not JSON") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"{company.id}: Eightfold response was not an object")
    return payload


def _map(
    company: Company,
    item: dict[str, Any],
    *,
    host: str,
    include_content: bool,
) -> NormalizedJob | None:
    job_id = item.get("id") or item.get("displayJobId") or item.get("display_job_id")
    title = (item.get("name") or item.get("posting_name") or "").strip()
    url = _job_url(host, item)
    if job_id is None or not title or not url:
        return None
    labels = _location_labels(item)
    places = [classify_text(label) for label in labels]
    description = None
    if include_content and isinstance(item.get("job_description"), str):
        description = html_to_text(item["job_description"])
    posted = _posted(item)
    return NormalizedJob(
        company_id=company.id,
        source="eightfold",
        job_id=str(job_id),
        title=title,
        url=url,
        location=labels[0] if labels else None,
        places=places,
        department=item.get("department") if isinstance(item.get("department"), str) else None,
        posted_at=posted,
        description=description,
        fingerprint=posted,
        detail_ref=str(job_id),
    )


def _job_url(host: str, item: dict[str, Any]) -> str:
    raw = item.get("canonicalPositionUrl") or item.get("positionUrl") or item.get("publicUrl") or ""
    if not isinstance(raw, str) or not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/"):
        return f"https://{host}{raw}"
    return ""


def _location_labels(item: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("standardizedLocations", "locations"):
        raw = item.get(key)
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str) and entry.strip():
                    labels.append(entry.strip())
                elif isinstance(entry, dict):
                    name = entry.get("name") or entry.get("location")
                    if isinstance(name, str) and name.strip():
                        labels.append(name.strip())
        elif isinstance(raw, str) and raw.strip():
            labels.append(raw.strip())
    single = item.get("location")
    if isinstance(single, str) and single.strip() and single.strip() not in labels:
        labels.append(single.strip())
    # Preserve order while dropping duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _posted(item: dict[str, Any]) -> str | None:
    raw = item.get("postedTs")
    if raw is None:
        raw = item.get("t_create") or item.get("t_update")
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw > 10_000_000_000 else raw
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
