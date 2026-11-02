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

API_HOST = "ats.rippling.com"
DEFAULT_MAX_PAGES = 100
_BOARD_RE = re.compile(r"https?://ats\.rippling\.com/(?:api/v2/board/)?([A-Za-z0-9_-]+)", re.I)


class RipplingAdapter:
    name = "rippling"

    def matches_url(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower() == API_HOST

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host != API_HOST:
            raise AdapterError(f"could not parse Rippling board from {url}")
        parts = [part for part in parsed.path.split("/") if part]
        board = ""
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "v2" and parts[2] == "board":
            board = parts[3]
        elif parts and parts[0] not in {"api", "jobs"}:
            board = parts[0]
        if not board:
            raise AdapterError(f"could not parse Rippling board from {url}")
        return {"ats": self.name, "host": host, "board": board}

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "ats.rippling.com" not in html.lower():
            return None
        match = _BOARD_RE.search(html)
        if not match:
            try:
                return self.parse_identity(url)
            except AdapterError:
                return {"ats": self.name, "host": API_HOST}
        return {"ats": self.name, "host": API_HOST, "board": match.group(1)}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content  # descriptions are a per-job call; enrich_one fetches those
        board = _board(company, self)
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        total_pages: int | None = None
        page = 0
        while page < max_pages:
            payload = _page(client, company, board, page)
            if isinstance(payload.get("totalPages"), int):
                total_pages = payload["totalPages"]
            items = payload.get("items") or []
            if not isinstance(items, list) or not items:
                break
            added = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                mapped = _map(company, item)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            page += 1
            if added == 0:
                break
            if total_pages is not None and page >= total_pages:
                break
            page_size = payload.get("pageSize")
            if isinstance(page_size, int) and len(items) < page_size:
                break
        else:
            if total_pages is not None and page < total_pages:
                company.extra["rippling_truncated"] = True
        return out

    def enrich_one(self, company: Company, client: httpx.Client, job: NormalizedJob) -> str | None:
        board = company.board
        if not board:
            return None
        job_id = job.detail_ref or job.job_id
        response = request(client, "GET", f"https://{API_HOST}/api/v2/board/{board}/jobs/{job_id}")
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        description = payload.get("description")
        if not isinstance(description, dict):
            return None
        parts = [
            html_to_text(description.get(key))
            for key in ("role", "company")
            if isinstance(description.get(key), str)
        ]
        text = " ".join(part for part in parts if part)
        return text or None


def _board(company: Company, adapter: RipplingAdapter) -> str:
    board = company.board
    if not board:
        board = adapter.parse_identity(company.careers_url).get("board")
    if not board:
        raise AdapterError(f"{company.id}: Rippling board slug is missing")
    return board


def _page(client: httpx.Client, company: Company, board: str, page: int) -> dict[str, Any]:
    response = request(
        client,
        "GET",
        f"https://{API_HOST}/api/v2/board/{board}/jobs",
        params={"page": page},
    )
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: Rippling HTTP 429", retryable=True)
    if response.status_code == 404:
        raise AdapterError(f"{company.id}: Rippling board {board!r} not found (404)")
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: Rippling HTTP {response.status_code} on page {page}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AdapterError(f"{company.id}: Rippling response was not JSON") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"{company.id}: Rippling response was not an object")
    return payload


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    job_id = item.get("id")
    title = (item.get("name") or "").strip()
    url = item.get("url")
    if not isinstance(job_id, str) or not title or not isinstance(url, str) or not url:
        return None
    labels: list[str] = []
    places = []
    workplace = None
    locations = item.get("locations")
    if isinstance(locations, list):
        for entry in locations:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            label = name.strip()
            labels.append(label)
            code = entry.get("countryCode")
            country = code if isinstance(code, str) else None
            places.append(classify_text(label, structured_country=country))
            if workplace is None and isinstance(entry.get("workplaceType"), str):
                workplace = entry["workplaceType"].lower()
    department = item.get("department")
    department_name = department.get("name") if isinstance(department, dict) else None
    return NormalizedJob(
        company_id=company.id,
        source="rippling",
        job_id=job_id,
        title=title,
        url=url,
        location=labels[0] if labels else None,
        places=places,
        department=department_name if isinstance(department_name, str) else None,
        workplace=workplace,
        detail_ref=job_id,
    )
