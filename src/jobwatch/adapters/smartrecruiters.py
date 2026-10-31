from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

PAGE_SIZE = 100
_HOSTS = {
    "jobs.smartrecruiters.com",
    "careers.smartrecruiters.com",
    "api.smartrecruiters.com",
}


class SmartRecruitersAdapter:
    name = "smartrecruiters"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in _HOSTS or host.endswith(".smartrecruiters.com")

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.split("/") if p]
        if host == "api.smartrecruiters.com":
            # /v1/companies/{company}/postings
            if "companies" in parts:
                i = parts.index("companies")
                if i + 1 < len(parts):
                    return {"board": parts[i + 1], "ats": self.name}
        if parts:
            return {"board": parts[0], "ats": self.name}
        raise AdapterError(f"could not parse SmartRecruiters company id from {url}")

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        lower = html.lower()
        if "smartrecruiters.com" not in lower:
            return None
        for marker in (
            "jobs.smartrecruiters.com/",
            "careers.smartrecruiters.com/",
            "api.smartrecruiters.com/v1/companies/",
        ):
            idx = lower.find(marker)
            if idx >= 0:
                rest = html[idx + len(marker) :]
                board = rest.split("/", 1)[0].split('"', 1)[0].split("'", 1)[0].split("?", 1)[0]
                if board:
                    return {"board": board, "ats": self.name}
        return {"ats": self.name}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        board = company.board or self.parse_identity(company.careers_url).get("board")
        if not board:
            raise AdapterError(f"{company.id}: SmartRecruiters company id is missing")
        out: list[NormalizedJob] = []
        offset = 0
        total: int | None = None
        max_pages = company.max_pages if company.max_pages is not None else 50
        for _ in range(max_pages):
            api = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
            response = request(
                client,
                "GET",
                api,
                params={"limit": PAGE_SIZE, "offset": offset, "destination": "PUBLIC"},
            )
            if response.status_code == 404:
                raise AdapterError(f"{company.id}: SmartRecruiters company {board!r} not found (404)")
            if response.status_code >= 400:
                raise AdapterError(f"{company.id}: SmartRecruiters HTTP {response.status_code}")
            payload = response.json()
            if total is None and isinstance(payload.get("totalFound"), int):
                total = payload["totalFound"]
            rows = payload.get("content") or []
            if not rows:
                break
            for item in rows:
                mapped = _map(company, item, board)
                if mapped:
                    out.append(mapped)
            offset += len(rows)
            if total is not None and offset >= total:
                break
            if len(rows) < PAGE_SIZE and total is None:
                break
        if include_content:
            company.extra["_sr_board"] = board
        return out

    def enrich_one(self, company: Company, client: httpx.Client, job: NormalizedJob) -> str | None:
        board = company.board or company.extra.get("_sr_board")
        if not board:
            try:
                board = self.parse_identity(company.careers_url).get("board")
            except AdapterError:
                board = None
        if not board:
            return None
        api = f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{job.job_id}"
        response = request(client, "GET", api)
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return _detail_text(payload)


def _map(company: Company, item: dict[str, Any], board: str) -> NormalizedJob | None:
    job_id = item.get("id")
    title = (item.get("name") or "").strip()
    if not job_id or not title:
        return None
    identifier = board
    company_block = item.get("company") or {}
    if isinstance(company_block, dict) and company_block.get("identifier"):
        identifier = company_block["identifier"]
    url = f"https://jobs.smartrecruiters.com/{identifier}/{job_id}"
    loc = item.get("location") or {}
    location = _format_location(loc) if isinstance(loc, dict) else None
    dept = item.get("department") or {}
    function = item.get("function") or {}
    department = None
    if isinstance(dept, dict):
        department = dept.get("label")
    if not department and isinstance(function, dict):
        department = function.get("label")
    return NormalizedJob(
        company_id=company.id,
        source="smartrecruiters",
        job_id=str(job_id),
        title=title,
        url=url,
        location=location,
        department=department,
        posted_at=item.get("releasedDate"),
        fingerprint=item.get("releasedDate") or str(job_id),
        detail_ref=str(job_id),
    )


def _detail_text(payload: dict[str, Any]) -> str | None:
    chunks: list[str] = []
    job_ad = payload.get("jobAd") or {}
    sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
    if isinstance(sections, dict):
        for section in sections.values():
            if isinstance(section, dict) and section.get("text"):
                chunks.append(str(section["text"]))
    for key in ("jobDescription", "description"):
        value = payload.get(key)
        if isinstance(value, str):
            chunks.append(value)
    if not chunks:
        return None
    return html_to_text("\n".join(chunks))


def _format_location(loc: dict[str, Any]) -> str | None:
    if loc.get("remote") is True and not loc.get("city"):
        return "Remote"
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    text = ", ".join(str(p) for p in parts if p)
    if loc.get("remote") is True and text:
        return f"{text} (Remote)"
    return text or None
