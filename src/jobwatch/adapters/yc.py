from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from jobwatch.adapters.base import AdapterError
from jobwatch.http import request
from jobwatch.locations import classify_text
from jobwatch.models import Company, NormalizedJob
from jobwatch.textutil import html_to_text

# Rails time_ago_in_words buckets. "3 months" is about 90 days; "4 months" is older.
DEFAULT_MAX_AGE_DAYS = 90
JOBS_ORIGIN = "https://www.ycombinator.com"
_JOBS_PATH = re.compile(r"^/companies/([^/]+)/jobs/?$", re.I)
_DATA_PAGE = re.compile(r"""data-page=["']([^"']+)["']""", re.I)
_RELATIVE_AGE = re.compile(
    r"""
    ^\s*
    (?:(?P<qual>about|over|almost|less\s+than)\s+)?
    (?:(?P<num>\d+|a|an)\s+)?
    (?P<unit>seconds?|minutes?|hours?|days?|months?|years?)
    (?:\s+ago)?
    \s*$
    """,
    re.I | re.VERBOSE,
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class YcAdapter:
    name = "yc"

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host != "ycombinator.com":
            return False
        return _JOBS_PATH.match(parsed.path) is not None

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        match = _JOBS_PATH.match(parsed.path)
        if not match:
            raise AdapterError(f"could not parse YC company slug from {url}")
        return {"board": match.group(1), "ats": self.name}

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        del url
        match = re.search(r"ycombinator\.com/companies/([^/\"'\s]+)/jobs", html, re.I)
        if not match:
            return None
        slug = match.group(1)
        if slug.lower() in {"", "jobs"}:
            return None
        return {"board": slug, "ats": self.name}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        del include_content
        slug = company.board or self.parse_identity(company.careers_url).get("board")
        if not slug:
            raise AdapterError(f"{company.id}: YC company slug is missing")
        url = company.careers_url
        if not self.matches_url(url):
            url = f"{JOBS_ORIGIN}/companies/{slug}/jobs"
        response = request(client, "GET", url, headers={"Accept": "text/html"})
        if response.status_code == 404:
            raise AdapterError(f"{company.id}: YC jobs page {url} not found (404)")
        if response.status_code >= 400:
            raise AdapterError(f"{company.id}: YC HTTP {response.status_code}")
        try:
            postings = extract_job_postings(response.text)
        except ValueError as exc:
            raise AdapterError(f"{company.id}: {exc}") from exc
        max_days = DEFAULT_MAX_AGE_DAYS if company.max_age_days is None else company.max_age_days
        out: list[NormalizedJob] = []
        for item in postings:
            mapped = _map(company, item)
            if mapped is None:
                continue
            if not within_max_age(mapped.posted_at, max_days):
                continue
            out.append(mapped)
        return out

    def enrich_one(self, company: Company, client: httpx.Client, job: NormalizedJob) -> str | None:
        del company
        if not job.url:
            return None
        try:
            response = request(client, "GET", job.url, headers={"Accept": "text/html"})
        except AdapterError:
            return None
        if response.status_code >= 400:
            return None
        return json_ld_description(response.text)


def json_ld_description(html: str) -> str | None:
    """Plain text of a schema.org JobPosting description, if the page has one."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _json_ld_nodes(payload):
            kind = node.get("@type")
            if kind != "JobPosting" and not (isinstance(kind, list) and "JobPosting" in kind):
                continue
            description = node.get("description")
            if isinstance(description, str):
                return html_to_text(description)
    return None


def _json_ld_nodes(payload: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            nodes.extend(_json_ld_nodes(item))
        return nodes
    if not isinstance(payload, dict):
        return nodes
    nodes.append(payload)
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            nodes.extend(_json_ld_nodes(item))
    return nodes


def extract_job_postings(html: str) -> list[dict[str, Any]]:
    raw = _data_page(html)
    if raw is None:
        raise ValueError("YC jobs page has no data-page payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("YC jobs page data-page is not JSON") from exc
    props = payload.get("props") if isinstance(payload, dict) else None
    postings = props.get("jobPostings") if isinstance(props, dict) else None
    if not isinstance(postings, list):
        raise ValueError("YC jobs page is missing jobPostings")
    return [item for item in postings if isinstance(item, dict)]


def within_max_age(created_at: str | None, max_days: int, *, now: datetime | None = None) -> bool:
    """True when a Rails relative age (or ISO timestamp) is within max_days.

    Month phrases use 30-day buckets, so ``3 months`` is kept at 90 days and
    ``4 months`` is not. ``over N`` counts as the next bucket. Missing or
    unrecognized ages are not recent.
    """
    if created_at is None:
        return False
    text = str(created_at).strip()
    if not text:
        return False
    iso_days = _iso_age_days(text, now or datetime.now(timezone.utc))
    if iso_days is not None:
        return iso_days <= max_days
    relative = _relative_age_days(text)
    if relative is None:
        return False
    return relative <= max_days


def _data_page(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    node = soup.find(attrs={"data-page": True})
    if node is not None:
        value = node.get("data-page")
        if isinstance(value, str) and value.strip():
            return value
    match = _DATA_PAGE.search(html)
    if not match:
        return None
    return unescape(match.group(1))


def _map(company: Company, item: dict[str, Any]) -> NormalizedJob | None:
    title = str(item.get("title") or "").strip()
    job_id = item.get("id")
    raw_url = str(item.get("url") or "").strip()
    if not job_id or not title or not raw_url:
        return None
    url = raw_url if raw_url.startswith("http") else urljoin(JOBS_ORIGIN, raw_url)
    location = str(item.get("location") or "").strip() or None
    places = [classify_text(location)] if location else []
    created = item.get("createdAt")
    posted_at = str(created).strip() if created else None
    return NormalizedJob(
        company_id=company.id,
        source="yc",
        job_id=str(job_id),
        title=title,
        url=url,
        location=location,
        places=places,
        department=item.get("prettyRole") or item.get("roleSpecificType"),
        posted_at=posted_at,
        fingerprint=str(job_id),
    )


def _relative_age_days(text: str) -> int | None:
    match = _RELATIVE_AGE.match(text)
    if not match:
        return None
    unit = match.group("unit").lower()
    if unit.endswith("s"):
        unit = unit[:-1]
    raw_num = match.group("num")
    if raw_num is None or raw_num.lower() in {"a", "an"}:
        count = 1
    else:
        count = int(raw_num)
    qual = (match.group("qual") or "").lower()
    if unit in {"second", "minute", "hour"}:
        return 0
    per = {"day": 1, "month": 30, "year": 365}[unit]
    days = count * per
    if qual == "over":
        days += per
    return days


def _iso_age_days(text: str, now: datetime) -> int | None:
    if not _ISO_DATE.match(text):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = (now - parsed.astimezone(timezone.utc)).total_seconds()
    if seconds < 0:
        return 0
    return int(seconds // 86400)
