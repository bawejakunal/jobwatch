"""US hiring companies from the public YC directory.

The directory search key is embedded in the companies page and expires every
couple of days, so each sync reads a fresh key instead of storing one.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.yc import DEFAULT_MAX_AGE_DAYS, JOBS_ORIGIN
from jobwatch.detect import _slugify
from jobwatch.http import request
from jobwatch.models import Company

DIRECTORY_URL = (
    "https://www.ycombinator.com/companies"
    "?isHiring=true&regions=United%20States%20of%20America"
)
ALGOLIA_INDEX = "YCCompany_production"
HIRING_FILTER = 'isHiring:true AND regions:"United States of America"'
_ALGOLIA_OPTS = re.compile(r"window\.AlgoliaOpts\s*=\s*(\{.*?\});")


def fetch_us_hiring_companies(
    client: httpx.Client,
    *,
    hits_per_page: int = 1000,
) -> list[tuple[str, str]]:
    """Return ``(slug, name)`` for companies on the US hiring directory.

    The public index only returns the first 1,000 hits of one query. The US
    hiring set is larger than that, so a truncated page is split into Remote
    and not-Remote queries, which together cover the same filter.
    """
    app_id, api_key = _algolia_credentials(client)
    url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
        "Referer": "https://www.ycombinator.com/companies",
    }
    return _collect(client, url, headers, HIRING_FILTER, hits_per_page, split=True)


def _collect(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    filters: str,
    hits_per_page: int,
    *,
    split: bool,
) -> list[tuple[str, str]]:
    found, total = _query_pages(client, url, headers, filters, hits_per_page)
    if total is None or len(found) >= total:
        return _dedupe(found)
    if not split:
        raise AdapterError(
            f"YC directory returned {len(found)} of {total} companies; the public search cap hid the rest"
        )
    remote = _collect(
        client,
        url,
        headers,
        f'{filters} AND regions:"Remote"',
        hits_per_page,
        split=False,
    )
    onsite = _collect(
        client,
        url,
        headers,
        f'{filters} AND NOT regions:"Remote"',
        hits_per_page,
        split=False,
    )
    return _dedupe(remote + onsite)


def _query_pages(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    filters: str,
    hits_per_page: int,
) -> tuple[list[tuple[str, str]], int | None]:
    found: list[tuple[str, str]] = []
    page = 0
    total: int | None = None
    while page < 20:
        response = request(
            client,
            "POST",
            url,
            headers=headers,
            json={
                "query": "",
                "page": page,
                "hitsPerPage": hits_per_page,
                "filters": filters,
                "attributesToRetrieve": ["name", "slug"],
            },
        )
        if response.status_code >= 400:
            raise AdapterError(f"YC directory HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError("YC directory returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise AdapterError("YC directory returned an unexpected payload")
        hits = payload.get("hits") or []
        if not isinstance(hits, list):
            raise AdapterError("YC directory response is missing hits")
        if total is None:
            raw_total = payload.get("nbHits")
            total = int(raw_total) if isinstance(raw_total, int) else None
        for hit in hits:
            pair = _hit_pair(hit)
            if pair:
                found.append(pair)
        if not hits or (total is not None and len(found) >= total) or len(hits) < hits_per_page:
            break
        page += 1
    return found, total


def _dedupe(hits: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for slug, name in hits:
        if slug in seen:
            continue
        seen.add(slug)
        out.append((slug, name))
    return out


def merge_yc_companies(
    companies: list[Company],
    hits: list[tuple[str, str]],
) -> tuple[list[Company], int]:
    """Replace ``ats: yc`` entries. Skip slugs already used by another ATS.

    Returns the new company list and how many directory slugs were skipped
    because that id already belongs to a non-YC company.
    """
    kept = [company for company in companies if company.ats != "yc"]
    reserved = {company.id for company in kept}
    added: list[Company] = []
    seen: set[str] = set()
    skipped = 0
    for slug, name in hits:
        company_id = _slugify(slug)
        if not company_id or company_id in reserved or company_id in seen:
            skipped += 1
            continue
        seen.add(company_id)
        added.append(_directory_company(slug, name, company_id))
    added.sort(key=lambda company: (company.name.casefold(), company.id))
    return kept + added, skipped


def _directory_company(slug: str, name: str, company_id: str) -> Company:
    display = name.strip() or company_id.replace("-", " ").title()
    return Company(
        id=company_id,
        name=display,
        careers_url=f"{JOBS_ORIGIN}/companies/{slug}/jobs",
        ats="yc",
        board=slug,
        scope="all",
        max_age_days=DEFAULT_MAX_AGE_DAYS,
    )


def _algolia_credentials(client: httpx.Client) -> tuple[str, str]:
    response = request(client, "GET", DIRECTORY_URL, headers={"Accept": "text/html"})
    if response.status_code >= 400:
        raise AdapterError(f"YC companies page HTTP {response.status_code}")
    match = _ALGOLIA_OPTS.search(response.text)
    if not match:
        raise AdapterError("YC companies page did not include an Algolia search key")
    try:
        opts: dict[str, Any] = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise AdapterError("YC Algolia options were not valid JSON") from exc
    app_id = str(opts.get("app") or "")
    api_key = str(opts.get("key") or "")
    if not app_id or not api_key:
        raise AdapterError("YC Algolia options are missing app or key")
    return app_id, api_key


def _hit_pair(hit: object) -> tuple[str, str] | None:
    if not isinstance(hit, dict):
        return None
    slug = str(hit.get("slug") or "").strip()
    if not slug:
        return None
    name = str(hit.get("name") or "").strip()
    return slug, name
