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

PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 100
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")
TENANT_HOST_RE = re.compile(
    r"^(?P<tenant>[a-z0-9-]+)\.(?P<shard>wd\d+)\.myworkdayjobs\.com$",
    re.I,
)
SHARED_HOST_RE = re.compile(r"^wd\d+\.myworkdaysite\.com$", re.I)
JOB_ID_RE = re.compile(r"_([A-Za-z0-9-]+)$")


class WorkdayAdapter:
    name = "workday"

    def matches_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
            return True
        return "/wday/cxs/" in url.lower()

    def parse_identity(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.split("/") if p]
        identity: dict[str, str] = {"ats": self.name, "host": host}

        if "cxs" in parts:
            i = parts.index("cxs")
            if i + 2 < len(parts):
                identity["tenant"] = parts[i + 1]
                identity["site"] = parts[i + 2]
                return identity

        if parts and parts[0] == "recruiting" and len(parts) >= 3:
            identity["tenant"] = parts[1]
            identity["site"] = parts[2]
            return identity

        host_match = TENANT_HOST_RE.match(host)
        if host_match:
            identity["tenant"] = host_match.group("tenant")

        idx = 0
        if parts and LOCALE_RE.match(parts[0]) and parts[0].lower() not in {"job", "jobs"}:
            # "en" alone is a locale; "d" is a Workday UI prefix, not a locale.
            if "-" in parts[0] or len(parts[0]) == 2:
                identity["locale"] = parts[0]
                idx = 1

        while idx < len(parts) and parts[idx] in {"d", "job", "jobs"}:
            idx += 1
        if idx < len(parts) and parts[idx] not in {"wday"}:
            identity["site"] = parts[idx]

        if identity.get("tenant") and identity.get("site"):
            return identity
        if SHARED_HOST_RE.match(host) and not identity.get("tenant"):
            raise AdapterError(
                f"Workday shared host {host} needs /recruiting/{{tenant}}/{{site}} "
                f"or /wday/cxs/{{tenant}}/{{site}} in the URL: {url}"
            )
        if not identity.get("tenant") or not identity.get("site"):
            raise AdapterError(f"could not parse Workday tenant/site from {url}")
        return identity

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None:
        lower = html.lower()
        if "myworkdayjobs.com" not in lower and "myworkdaysite.com" not in lower and "/wday/cxs/" not in lower:
            return None
        for marker in ("myworkdayjobs.com", "myworkdaysite.com"):
            idx = lower.find(marker)
            if idx >= 0:
                # Walk backward to the https://
                start = html.rfind("http", 0, idx)
                if start >= 0:
                    snippet = html[start : idx + len(marker) + 80]
                    snippet = snippet.split('"', 1)[0].split("'", 1)[0]
                    try:
                        return self.parse_identity(snippet)
                    except AdapterError:
                        pass
        cxs = re.search(
            r"/wday/cxs/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)",
            html,
        )
        if cxs:
            host = (urlparse(url).hostname or "").lower()
            return {
                "ats": self.name,
                "host": host,
                "tenant": cxs.group(1),
                "site": cxs.group(2),
            }
        return {"ats": self.name}

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]:
        ident = _identity(company, self)
        host = ident["host"]
        tenant = ident["tenant"]
        site = ident["site"]
        locale = ident.get("locale") or company.locale
        api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        max_pages = company.max_pages if company.max_pages is not None else DEFAULT_MAX_PAGES
        out: list[NormalizedJob] = []
        seen: set[str] = set()
        total: int | None = None
        warning_cap = False

        for page in range(max_pages):
            offset = page * PAGE_SIZE
            response = request(
                client,
                "POST",
                api,
                json={
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": "",
                },
                headers={"Accept": "application/json", "Accept-Language": "en-US"},
            )
            if response.status_code >= 400:
                raise AdapterError(
                    f"{company.id}: Workday HTTP {response.status_code} for {api} offset={offset}"
                )
            payload = response.json()
            if page == 0:
                raw_total = payload.get("total")
                if isinstance(raw_total, int):
                    total = raw_total
                    if raw_total >= 2000:
                        warning_cap = True
            rows = payload.get("jobPostings") or []
            if not rows:
                break
            new_this_page = 0
            for item in rows:
                path = item.get("externalPath") or ""
                if path in seen:
                    continue
                seen.add(path)
                new_this_page += 1
                mapped = _map(company, item, host=host, site=site, locale=locale)
                if mapped:
                    out.append(mapped)
            if new_this_page == 0:
                break  # wrap: every path already seen
            if total is not None and total > 0 and offset + len(rows) >= total:
                break
            if len(rows) < PAGE_SIZE:
                break

        if warning_cap:
            company.extra["workday_truncated"] = True
        if include_content:
            company.extra["_workday_ident"] = ident
        return out

    def enrich_one(self, company: Company, client: httpx.Client, job: NormalizedJob) -> str | None:
        ident = company.extra.get("_workday_ident") or _identity(company, self)
        path = job.detail_ref
        if not path:
            return None
        if not path.startswith("/"):
            path = "/" + path
        api = f"https://{ident['host']}/wday/cxs/{ident['tenant']}/{ident['site']}{path}"
        response = request(
            client,
            "GET",
            api,
            headers={"Accept": "application/json", "Accept-Language": "en-US"},
        )
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        info = payload.get("jobPostingInfo") or payload
        html = None
        if isinstance(info, dict):
            html = info.get("jobDescription") or info.get("description")
        return html_to_text(html) if isinstance(html, str) else None


def _identity(company: Company, adapter: WorkdayAdapter) -> dict[str, str]:
    ident: dict[str, str] = {}
    if company.host:
        ident["host"] = company.host
    if company.tenant:
        ident["tenant"] = company.tenant
    if company.site:
        ident["site"] = company.site
    if company.locale:
        ident["locale"] = company.locale
    if not (ident.get("host") and ident.get("tenant") and ident.get("site")):
        parsed = adapter.parse_identity(company.careers_url)
        for key, value in parsed.items():
            ident.setdefault(key, value)
    missing = [k for k in ("host", "tenant", "site") if not ident.get(k)]
    if missing:
        raise AdapterError(f"{company.id}: Workday missing {', '.join(missing)}")
    return ident


def _map(
    company: Company,
    item: dict[str, Any],
    *,
    host: str,
    site: str,
    locale: str | None,
) -> NormalizedJob | None:
    title = (item.get("title") or "").strip()
    path = item.get("externalPath") or ""
    if not title or not path:
        return None
    if not path.startswith("/"):
        path = "/" + path
    prefix = f"/{locale}" if locale else ""
    url = f"https://{host}{prefix}/{site}{path}"
    match = JOB_ID_RE.search(path)
    bullets = item.get("bulletFields") or []
    job_id = match.group(1) if match else (str(bullets[0]) if bullets else path)
    posted = item.get("postedOn")
    loc_text = item.get("locationsText")
    places = [classify_text(str(loc_text))] if loc_text else []
    # Deep links often encode country: /job/US-CA-Santa-Clara/...
    if path.startswith("/job/") and not (places and places[0].country):
        code = path.split("/")[2].split("-")[0] if path.count("/") >= 2 else ""
        if len(code) == 2 and code.isalpha():
            places = [classify_text(str(loc_text or code), structured_country=code)]
    return NormalizedJob(
        company_id=company.id,
        source="workday",
        job_id=str(job_id),
        title=title,
        url=url,
        location=str(loc_text) if loc_text else None,
        places=places,
        posted_at=posted,
        fingerprint=f"{title}|{posted or ''}|{path}",
        detail_ref=path,
    )
