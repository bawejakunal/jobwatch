from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from jobwatch.adapters.base import AdapterError, NeedsBrowserError
from jobwatch.http import request
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

JOB_HREF_RE = re.compile(r"/jobs/(\d+)/", re.I)
PAGE_OF_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)
CHALLENGE_MARKERS = (
    "cf-challenge",
    "cf-browser-verification",
    "just a moment",
    "attention required",
    "cloudflare",
    "enable javascript",
)


class IcimsAdapter:
    name = "icims"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith(".icims.com") or host == "icims.com"

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise AdapterError(f"could not parse iCIMS portal from {url}")
        portal = f"{parsed.scheme}://{parsed.netloc}"
        return {"portal": portal, "ats": self.name}

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        lower = html.lower()
        if "icims.com" not in lower and "icims_" not in lower:
            return None
        match = re.search(r"https?://[a-z0-9.-]+\.icims\.com", html, re.I)
        if match:
            return {"portal": match.group(0), "ats": self.name}
        if "icims.com" in lower or "icims_" in lower:
            parsed = urlparse(url)
            portal = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
            ident: dict[str, str] = {"ats": self.name}
            if portal:
                ident["portal"] = portal
            return ident
        return None

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content  # detail fetch happens in enrich_one, gated by the pipeline
        portal = company.portal
        if not portal:
            portal = self.parse_identity(company.careers_url).get("portal")
        if not portal:
            raise AdapterError(f"{company.id}: iCIMS portal origin is missing")
        portal = portal.rstrip("/")
        max_pages = company.max_pages if company.max_pages is not None else 40
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        total_pages: int | None = None

        for pr in range(max_pages):
            url = f"{portal}/jobs/search"
            response = request(
                client,
                "GET",
                url,
                params={"ss": "1", "in_iframe": "1", "pr": pr},
            )
            if response.status_code in {401, 403, 405, 407, 429} and response.status_code != 429:
                raise NeedsBrowserError(
                    f"{company.id}: iCIMS portal returned HTTP {response.status_code}. "
                    "Classic iframe search is blocked (bot protection or Attract). "
                    "Use a Playwright recipe; see PLAN.md §5.6."
                )
            if response.status_code == 429:
                raise AdapterError(f"{company.id}: iCIMS rate-limited (429)", retryable=True)
            if response.status_code >= 400:
                raise AdapterError(f"{company.id}: iCIMS HTTP {response.status_code}")

            html = response.text
            if _looks_like_challenge(html):
                raise NeedsBrowserError(
                    f"{company.id}: iCIMS returned a challenge/interstitial page "
                    "(Cloudflare or JS wall). Use a Playwright recipe; see PLAN.md §5.6."
                )
            page_jobs, page_total = parse_search_html(html, portal=portal, company_id=company.id)
            if page_total:
                total_pages = page_total
            if not page_jobs:
                break
            added = 0
            for job in page_jobs:
                if job.job_id in seen:
                    continue
                seen.add(job.job_id)
                out.append(job)
                added += 1
            if added == 0:
                break
            if total_pages is not None and (pr + 1) >= total_pages:
                break
        return out

    def enrich_one(self, company: Company, client: httpx.Client, job: NormalizedJob) -> str | None:
        portal = (company.portal or "").rstrip("/")
        if not portal:
            try:
                portal = self.parse_identity(company.careers_url).get("portal") or ""
            except AdapterError:
                return None
        url = f"{portal}/jobs/{job.job_id}/job"
        response = request(client, "GET", url, params={"in_iframe": "1"})
        if response.status_code >= 400:
            return None
        return parse_job_description(response.text)


def parse_search_html(html: str, *, portal: str, company_id: str) -> tuple[list[NormalizedJob], int | None]:
    soup = BeautifulSoup(html, "lxml")
    total_pages = _page_count(soup, html)
    jobs: list[NormalizedJob] = []
    seen: set[str] = set()

    cards = soup.select("li.iCIMS_JobCardItem, div.iCIMS_JobCardItem, .iCIMS_JobsTable li")
    if not cards:
        cards = soup.select("a.iCIMS_Anchor[href*='/jobs/']")

    for card in cards:
        link = card if card.name == "a" else card.select_one("a[href*='/jobs/']")
        if link is None:
            continue
        href = link.get("href") or ""
        match = JOB_HREF_RE.search(href)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        title = _title_from_card(card, link)
        if not title:
            continue
        location = _location_from_card(card)
        jobs.append(
            NormalizedJob(
                company_id=company_id,
                source="icims",
                job_id=job_id,
                title=title,
                url=urljoin(portal + "/", href),
                location=location,
                fingerprint=title,
                detail_ref=job_id,
            )
        )
    return jobs, total_pages


def parse_job_description(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type")
            type_ok = types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types)
            if type_ok and item.get("description"):
                return html_to_text(str(item["description"]))
    body = soup.select_one(".iCIMS_JobContent, .iCIMS_InfoMsg, [itemprop=description]")
    if body:
        return html_to_text(str(body))
    return None


def _title_from_card(card, link) -> str:
    heading = link.find("h3") if hasattr(link, "find") else None
    if heading and heading.get_text(strip=True):
        return heading.get_text(strip=True)
    title_attr = (link.get("title") or "").strip()
    if title_attr:
        # "32123 - Staff Scientist"
        if " - " in title_attr:
            return title_attr.split(" - ", 1)[1].strip()
        return title_attr
    text = link.get_text(" ", strip=True)
    text = re.sub(r"^title\s+", "", text, flags=re.I).strip()
    return text


def _location_from_card(card) -> str | None:
    if not hasattr(card, "select"):
        return None
    for tag in card.select("div.iCIMS_JobHeaderTag"):
        field = tag.select_one(".iCIMS_JobHeaderField")
        data = tag.select_one(".iCIMS_JobHeaderData")
        if not data:
            continue
        label = (field.get_text(" ", strip=True) if field else "").casefold()
        if "location" in label or tag.select_one(".glyphicons-map-marker"):
            text = data.get_text(" ", strip=True)
            return text or None
    return None


def _page_count(soup: BeautifulSoup, html: str) -> int | None:
    header = soup.select_one(".iCIMS_SubHeader_Jobs, h2.iCIMS_SubHeader")
    blob = header.get_text(" ", strip=True) if header else ""
    match = PAGE_OF_RE.search(blob) or PAGE_OF_RE.search(html)
    if match:
        try:
            return int(match.group(2))
        except ValueError:
            return None
    return None


def _looks_like_challenge(html: str) -> bool:
    lower = html.lower()
    if any(marker in lower for marker in CHALLENGE_MARKERS) and "icims_job" not in lower:
        return True
    return False
