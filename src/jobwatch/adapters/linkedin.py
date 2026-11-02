from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 10
DEFAULT_MAX_PAGES = 40


class LinkedInAdapter:
    name = "linkedin"

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host.endswith("linkedin.com"):
            return False
        return "/jobs" in parsed.path

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host.endswith("linkedin.com"):
            raise AdapterError(f"could not parse LinkedIn jobs URL from {url}")
        identity = {"ats": self.name, "host": "www.linkedin.com"}
        company_id = (parse_qs(parsed.query).get("f_C") or [None])[0]
        if company_id:
            identity["board"] = company_id.split(",")[0]
        return identity

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        if "linkedin.com/jobs" not in html.lower() and not self.matches_url(url):
            return None
        try:
            if self.matches_url(url):
                return self.parse_identity(url)
        except AdapterError:
            pass
        return {"ats": self.name, "host": "www.linkedin.com"}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content  # guest cards are title and location only
        company_id = _company_id(company, self)
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        start = 0
        pages = 0
        while pages < max_pages:
            cards = _page(client, company, company_id, start)
            if not cards:
                if pages == 0:
                    raise AdapterError(f"{company.id}: LinkedIn guest search returned no jobs")
                break
            added = 0
            for card in cards:
                mapped = _map(company, card)
                if not mapped or mapped.job_id in seen:
                    continue
                seen.add(mapped.job_id)
                out.append(mapped)
                added += 1
            start += len(cards)
            pages += 1
            if added == 0 or len(cards) < PAGE_SIZE:
                break
        else:
            company.extra["linkedin_truncated"] = True
        return out


def _company_id(company: Company, adapter: LinkedInAdapter) -> str:
    board = company.board
    if not board:
        board = adapter.parse_identity(company.careers_url).get("board")
    if not board:
        raise AdapterError(f"{company.id}: LinkedIn company id (f_C) is missing")
    return str(board)


def _page(
    client: httpx.Client,
    company: Company,
    company_id: str,
    start: int,
) -> list[dict[str, Any]]:
    response = request(
        client,
        "GET",
        GUEST_URL,
        params={"f_C": company_id, "start": start, "location": "Worldwide"},
    )
    if response.status_code == 429:
        raise AdapterError(f"{company.id}: LinkedIn HTTP 429", retryable=True)
    if response.status_code >= 400:
        raise AdapterError(f"{company.id}: LinkedIn HTTP {response.status_code} at start {start}")
    return _cards(response.text)


def _cards(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    cards: list[dict[str, Any]] = []
    for node in soup.select("[data-entity-urn]"):
        urn = node.get("data-entity-urn") or ""
        job_id = urn.rsplit(":", 1)[-1]
        title_el = node.select_one("h3.base-search-card__title")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        loc_el = node.select_one(".job-search-card__location")
        location = loc_el.get_text(" ", strip=True) if loc_el else None
        link = node.select_one("a.base-card__full-link")
        href = (link.get("href") if link else "") or ""
        posted_el = node.select_one("time")
        posted = posted_el.get("datetime") if posted_el else None
        if not job_id or not title:
            continue
        cards.append(
            {
                "id": job_id,
                "title": title,
                "url": href.split("?", 1)[0],
                "location": location,
                "posted": posted,
            }
        )
    return cards


def _map(company: Company, card: dict[str, Any]) -> NormalizedJob | None:
    url = card["url"]
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    location = card.get("location")
    posted = card.get("posted")
    return NormalizedJob(
        company_id=company.id,
        source="linkedin",
        job_id=str(card["id"]),
        title=card["title"],
        url=url,
        location=location if isinstance(location, str) else None,
        places=[classify_text(location)] if isinstance(location, str) and location else [],
        posted_at=posted if isinstance(posted, str) else None,
        fingerprint=posted if isinstance(posted, str) else None,
    )
