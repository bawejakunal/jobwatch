from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import JobLocation, classify_text, normalize_country
from jobwatch.models import Company, NormalizedJob


class AshbyAdapter:
    name = "ashby"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in {"jobs.ashbyhq.com", "api.ashbyhq.com"} or host.endswith(".ashbyhq.com")

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.split("/") if p]
        if host == "api.ashbyhq.com":
            # /posting-api/job-board/{board}
            if "job-board" in parts:
                i = parts.index("job-board")
                if i + 1 < len(parts):
                    return {"board": parts[i + 1], "ats": self.name}
        if parts and parts[0] not in {"embed", "api"}:
            return {"board": parts[0], "ats": self.name}
        raise AdapterError(f"could not parse Ashby board name from {url}")

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        lower = html.lower()
        if "ashbyhq.com" not in lower and "ashby" not in lower:
            return None
        for marker in ("jobs.ashbyhq.com/", "api.ashbyhq.com/posting-api/job-board/"):
            idx = lower.find(marker)
            if idx >= 0:
                rest = html[idx + len(marker) :]
                board = rest.split("/", 1)[0].split('"', 1)[0].split("'", 1)[0].split("?", 1)[0]
                if board:
                    return {"board": board, "ats": self.name}
        if "ashbyhq" in lower:
            return {"ats": self.name}
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
            raise AdapterError(f"{company.id}: Ashby board name is missing")
        api = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        response = request(client, "GET", api)
        if response.status_code == 404:
            raise AdapterError(f"{company.id}: Ashby board {board!r} not found (404)")
        if response.status_code >= 400:
            raise AdapterError(f"{company.id}: Ashby HTTP {response.status_code}")
        payload = response.json()
        jobs_raw = payload.get("jobs") or []
        out: list[NormalizedJob] = []
        for item in jobs_raw:
            if item.get("isListed") is False:
                continue
            mapped = _map(company, item)
            if mapped:
                out.append(mapped)
        return out


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    url = item.get("jobUrl") or item.get("applyUrl") or ""
    title = (item.get("title") or "").strip()
    job_id = item.get("id")
    if not job_id and url:
        job_id = url.rstrip("/").split("/")[-1]
    if not job_id or not title or not url:
        return None
    places = _ashby_places(item)
    location = ", ".join(p.text for p in places) if places else None
    return NormalizedJob(
        company_id=company.id,
        source="ashby",
        job_id=str(job_id),
        title=title,
        url=url,
        location=location,
        places=places,
        department=item.get("department") or item.get("team"),
        workplace=item.get("workplaceType"),
        posted_at=item.get("publishedAt"),
        description=item.get("descriptionPlain"),
        fingerprint=item.get("publishedAt") or str(job_id),
    )


def _ashby_places(item: dict[str, Any]) -> list[JobLocation]:
    places: list[JobLocation] = []
    seen: set[str] = set()
    primary = item.get("location")
    if primary:
        places.append(_ashby_place(str(primary), item.get("address"), item.get("isRemote")))
        seen.add(str(primary))
    for extra in item.get("secondaryLocations") or []:
        if isinstance(extra, dict):
            text = extra.get("location")
            if not text or str(text) in seen:
                continue
            seen.add(str(text))
            places.append(_ashby_place(str(text), extra.get("address"), False))
        elif isinstance(extra, str) and extra not in seen:
            seen.add(extra)
            places.append(classify_text(extra))
    return places


def _ashby_place(text: str, address: object, is_remote: object) -> JobLocation:
    country = None
    if isinstance(address, dict):
        postal = address.get("postalAddress") if isinstance(address.get("postalAddress"), dict) else address
        if isinstance(postal, dict):
            country = normalize_country(postal.get("addressCountry"))
    place = classify_text(text, structured_country=country)
    if is_remote is True:
        place.remote = True
        if place.country is None:
            place.ambiguous = True
    return place
