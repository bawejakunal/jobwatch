from __future__ import annotations

from urllib.parse import urlparse

import httpx

from jobwatch.adapters import ADAPTERS, AdapterError, apply_identity
from jobwatch.http import request
from jobwatch.models import Company, Detection


def detect_from_url(url: str) -> Detection | None:
    url = url.strip()
    for adapter in ADAPTERS.values():
        if not adapter.matches_url(url):
            continue
        try:
            identity = adapter.parse_identity(url)
        except AdapterError:
            identity = {"ats": adapter.name}
        return Detection(
            ats=adapter.name,  # type: ignore[arg-type]
            careers_url=url,
            identity=identity,
            confidence="url",
        )
    return None


def detect_from_html(url: str, html: str) -> Detection | None:
    for adapter in ADAPTERS.values():
        identity = adapter.sniff_html(url, html)
        if not identity:
            continue
        return Detection(
            ats=adapter.name,  # type: ignore[arg-type]
            careers_url=url,
            identity=identity,
            confidence="html",
            notes="detected from page HTML / embeds",
        )
    return None


def detect(url: str, client: httpx.Client | None = None) -> Detection:
    found = detect_from_url(url)
    if found and _identity_complete(found):
        return found

    owns_client = client is None
    if client is None:
        from jobwatch.http import build_client

        client = build_client()
    try:
        response = request(client, "GET", url)
        html = response.text
        final_url = str(response.url)
        # The landing URL may itself be an ATS host after redirects.
        redirected = detect_from_url(final_url)
        if redirected and _identity_complete(redirected):
            redirected.careers_url = url
            redirected.notes = f"followed redirect to {final_url}"
            return redirected
        sniffed = detect_from_html(final_url, html)
        if sniffed:
            sniffed.careers_url = url
            return sniffed
        probed = probe_hosted_boards(url, client)
        if probed:
            return probed
        if found:
            found.notes = "URL matched an ATS host but identity is incomplete; HTML sniff failed"
            return found
        raise AdapterError(
            f"could not detect an ATS for {url}. "
            "Pass a hosted board URL or add a browser recipe (Phase 2)."
        )
    finally:
        if owns_client:
            client.close()


def probe_hosted_boards(url: str, client: httpx.Client) -> Detection | None:
    """When a marketing careers page hides the ATS, try common board slugs.

    harvey.ai/careers does not embed Ashby; jobs.ashbyhq.com/harvey does.
    """
    slugs = _candidate_slugs(url)
    if not slugs:
        return None
    for slug in slugs:
        ashby = _probe_ashby(client, slug)
        if ashby:
            ashby.careers_url = url
            return ashby
        gh = _probe_greenhouse(client, slug)
        if gh:
            gh.careers_url = url
            return gh
        lever = _probe_lever(client, slug)
        if lever:
            lever.careers_url = url
            return lever
    return None


def _candidate_slugs(url: str) -> list[str]:
    host = (urlparse(url).hostname or "").lower()
    if not host or any(
        token in host
        for token in (
            "greenhouse.io",
            "lever.co",
            "ashbyhq.com",
            "myworkdayjobs.com",
            "myworkdaysite.com",
            "smartrecruiters.com",
            "icims.com",
        )
    ):
        return []
    labels = [p for p in host.split(".") if p and p not in {"www", "careers", "jobs", "job"}]
    if not labels:
        return []
    sld = labels[0]
    out = [sld]
    if "-" in sld:
        out.append(sld.replace("-", ""))
    return list(dict.fromkeys(out))


def _probe_ashby(client: httpx.Client, slug: str) -> Detection | None:
    api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        response = request(client, "GET", api)
    except AdapterError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return None
    return Detection(
        ats="ashby",
        careers_url=api,
        identity={"board": slug, "ats": "ashby"},
        confidence="probe",
        notes=f"probed {api} ({len(jobs)} postings)",
    )


def _probe_greenhouse(client: httpx.Client, slug: str) -> Detection | None:
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        response = request(client, "GET", api)
    except AdapterError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return None
    return Detection(
        ats="greenhouse",
        careers_url=api,
        identity={"board": slug, "ats": "greenhouse"},
        confidence="probe",
        notes=f"probed {api} ({len(jobs)} postings)",
    )


def _probe_lever(client: httpx.Client, slug: str) -> Detection | None:
    for region, host in (("global", "https://api.lever.co"), ("eu", "https://api.eu.lever.co")):
        api = f"{host}/v0/postings/{slug}"
        try:
            response = request(client, "GET", api, params={"mode": "json", "limit": 1})
        except AdapterError:
            continue
        if response.status_code != 200:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        if isinstance(payload, list) and payload:
            ident = {"board": slug, "ats": "lever", "region": region}
            return Detection(
                ats="lever",
                careers_url=api,
                identity=ident,
                confidence="probe",
                notes=f"probed {api} ({len(payload)}+ postings)",
            )
    return None


def _identity_complete(detection: Detection) -> bool:
    ident = detection.identity
    if detection.ats == "workday":
        return bool(ident.get("host") and ident.get("tenant") and ident.get("site"))
    if detection.ats == "icims":
        return bool(ident.get("portal"))
    if detection.ats in {"greenhouse", "lever", "ashby", "smartrecruiters"}:
        return bool(ident.get("board"))
    return False


def company_from_detection(
    detection: Detection,
    *,
    name: str | None = None,
    company_id: str | None = None,
) -> Company:
    ident = detection.identity
    slug = company_id or ident.get("board") or ident.get("tenant") or _slug_from_url(detection.careers_url)
    display = name or slug.replace("-", " ").title()
    company = Company(
        id=_slugify(slug),
        name=display,
        careers_url=detection.careers_url,
        ats=detection.ats,
    )
    apply_identity(company, ident)
    return company


def _slug_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "company").split(".")[0]
    return host.removeprefix("careers-").removeprefix("jobs-") or "company"


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "company"
