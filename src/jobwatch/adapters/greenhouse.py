from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards-api.greenhouse.io"}
_FOR_RE = re.compile(r"[?&]for=([A-Za-z0-9_-]+)", re.I)
_EMBED_RE = re.compile(
    r"(?:boards(?:-api)?|job-boards)\.greenhouse\.io/(?:embed/job_board(?:/js)?)?(?:\?for=|/)?([A-Za-z0-9_-]+)",
    re.I,
)
_GRNH = re.compile(r"https?://(?:www\.)?grnh\.se/", re.I)


class GreenhouseAdapter:
    name = "greenhouse"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if host in _HOSTS or host.endswith(".greenhouse.io"):
            return True
        return "greenhouse.io" in host or bool(_GRNH.search(url))

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        query = parse_qs(parsed.query)
        if query.get("for"):
            return {"board": query["for"][0], "ats": self.name}
        parts = [p for p in parsed.path.split("/") if p]
        if host == "boards-api.greenhouse.io" and len(parts) >= 3 and parts[0] == "v1" and parts[1] == "boards":
            return {"board": parts[2], "ats": self.name}
        if parts and parts[0] not in {"embed", "jobs"}:
            return {"board": parts[0], "ats": self.name}
        if "embed" in parts:
            # /embed/job_board?for=token already handled; try next segment
            if len(parts) >= 3 and parts[1] == "job_board":
                pass
        raise AdapterError(f"could not parse Greenhouse board token from {url}")

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "greenhouse" not in html.lower() and "grnh.se" not in html.lower() and "gh_jid" not in html.lower():
            return None
        match = _FOR_RE.search(html) or _EMBED_RE.search(html)
        if match:
            return {"board": match.group(1), "ats": self.name}
        if "greenhouse" in html.lower() or "gh_jid" in html.lower():
            return {"ats": self.name}
        return None

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        board = company.board
        if not board:
            board = self.parse_identity(company.careers_url).get("board")
        if not board:
            raise AdapterError(f"{company.id}: Greenhouse board token is missing")
        api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
        params = {"content": "true"} if include_content else None
        response = request(client, "GET", api, params=params)
        if response.status_code == 404:
            raise AdapterError(f"{company.id}: Greenhouse board {board!r} not found (404)")
        if response.status_code >= 400:
            raise AdapterError(f"{company.id}: Greenhouse HTTP {response.status_code}")
        payload = response.json()
        jobs_raw = payload.get("jobs") or []
        out: list[NormalizedJob] = []
        for item in jobs_raw:
            mapped = _map(company, item)
            if mapped:
                out.append(mapped)
        return out


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    job_id = item.get("id")
    title = (item.get("title") or "").strip()
    url = item.get("absolute_url") or ""
    if job_id is None or not title or not url:
        return None
    location = None
    loc = item.get("location")
    if isinstance(loc, dict):
        location = loc.get("name")
    elif isinstance(loc, str):
        location = loc
    department = None
    depts = item.get("departments") or []
    if depts and isinstance(depts[0], dict):
        department = depts[0].get("name")
    raw_content = item.get("content")
    description = html_to_text(raw_content) if isinstance(raw_content, str) else None
    posted = item.get("updated_at") or item.get("first_published")
    return NormalizedJob(
        company_id=company.id,
        source="greenhouse",
        job_id=str(job_id),
        title=title,
        url=url,
        location=location,
        department=department,
        posted_at=item.get("first_published") or item.get("updated_at"),
        description=description,
        fingerprint=str(posted) if posted else None,
    )
