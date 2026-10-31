from __future__ import annotations

from jobwatch.adapters import ADAPTERS, AdapterError, NeedsBrowserError, apply_identity, get_adapter
from jobwatch.detect import detect_from_url
from jobwatch.filter import apply_filter, needs_description
from jobwatch.http import RateLimiter, build_client
from jobwatch.locations import LocationSettings, apply_location_filter, parse_location_settings
from jobwatch.models import AppConfig, Company, CompanyResult, FilterSettings, NormalizedJob
from jobwatch.output import err_console
from jobwatch.store import Store


def scan_companies(
    config: AppConfig,
    store: Store,
    *,
    settings: FilterSettings,
    company_ids: list[str] | None = None,
    apply_new_grad_filter: bool = True,
    verbose: bool = False,
) -> list[CompanyResult]:
    selected = [c for c in config.companies if c.enabled]
    if company_ids:
        wanted = set(company_ids)
        selected = [c for c in selected if c.id in wanted]
        missing = wanted - {c.id for c in selected}
        if missing:
            raise AdapterError(f"unknown or disabled company id(s): {', '.join(sorted(missing))}")

    limiter = RateLimiter(config.defaults.rate_limit_rps)
    timeout = config.defaults.timeout_s
    client = build_client(timeout=timeout)
    _install_limiter(client, limiter)

    results: list[CompanyResult] = []
    scan_id, started = store.start_scan()
    errors: list[dict[str, str]] = []
    fetched = 0
    new_count = 0
    try:
        for company in selected:
            result = _scan_one(
                company,
                client,
                store,
                settings=settings,
                started=started,
                apply_new_grad_filter=apply_new_grad_filter,
                verbose=verbose,
            )
            results.append(result)
            fetched += len(result.jobs)
            new_count += len(result.new_jobs)
            if result.error:
                errors.append({"company": company.id, "error": result.error})
    finally:
        store.finish_scan(
            scan_id,
            companies=len(selected),
            fetched=fetched,
            new_count=new_count,
            errors=errors,
        )
        client.close()
    return results


def _scan_one(
    company: Company,
    client,
    store: Store,
    *,
    settings: FilterSettings,
    started: str,
    apply_new_grad_filter: bool,
    verbose: bool,
) -> CompanyResult:
    result = CompanyResult(company=company)
    try:
        adapter_name = company.ats
        if not adapter_name:
            detected = detect_from_url(company.careers_url)
            if not detected:
                raise AdapterError(
                    f"{company.id}: no ATS set and URL was not recognized. Run `jobwatch add` or `detect`."
                )
            adapter_name = detected.ats
            apply_identity(company, detected.identity)
            company.ats = adapter_name
        if adapter_name == "browser":
            raise NeedsBrowserError(f"{company.id}: ats=browser requires Phase 2 recipes")
        adapter = get_adapter(adapter_name)
        if verbose:
            err_console.print(f"[dim]fetch[/dim] {company.id} via {adapter_name}")
        jobs = adapter.fetch(company, client, include_content=settings.check_description)
        if settings.check_description:
            cached, fetched_detail, truncated = enrich_jobs(
                adapter, company, client, jobs, store, settings, verbose=verbose
            )
            result.cached_descriptions = cached
            result.enriched = fetched_detail
            if truncated:
                result.warning = (
                    f"description enrich capped at {settings.max_enrich} detail fetches; "
                    "cached bodies are reused next run"
                )
        if apply_new_grad_filter:
            jobs = apply_filter(jobs, company, settings)
        loc_settings = _location_settings(company, settings)
        if loc_settings.enabled:
            jobs = apply_location_filter(jobs, loc_settings)
        result.jobs = jobs
        result.new_jobs = store.upsert_jobs(
            company.id,
            adapter_name,
            jobs,
            seen_at=started,
            mark_missing_closed=True,
        )
        if company.extra.get("workday_truncated"):
            extra = (
                "Workday reported total=2000; results are capped. "
                "Facet split is Phase 2 — see PLAN.md §5.4."
            )
            result.warning = f"{result.warning} {extra}".strip() if result.warning else extra
    except NeedsBrowserError as exc:
        result.error = str(exc)
        result.needs_browser = True
        err_console.print(f"[yellow]{company.id}:[/yellow] {exc}")
    except AdapterError as exc:
        result.error = str(exc)
        err_console.print(f"[red]{company.id}:[/red] {exc}")
    except Exception as exc:  # noqa: BLE001 — isolate unknown failures per company
        result.error = f"{type(exc).__name__}: {exc}"
        err_console.print(f"[red]{company.id}:[/red] {result.error}")
    return result


def enrich_jobs(
    adapter,
    company: Company,
    client,
    jobs: list[NormalizedJob],
    store: Store,
    settings: FilterSettings,
    *,
    verbose: bool = False,
) -> tuple[int, int, bool]:
    """Fill descriptions from SQLite cache or adapter.enrich_one. Returns (cached, fetched, truncated)."""
    enrich_one = getattr(adapter, "enrich_one", None)
    cached = 0
    fetched = 0
    truncated = False
    for job in jobs:
        if job.description:
            continue
        body = store.cached_description(company.id, job.source, job.job_id, job.fingerprint)
        if body:
            job.description = body
            cached += 1
            continue
        if not settings.check_description or enrich_one is None:
            continue
        if not needs_description(job, company, settings):
            continue
        if fetched >= settings.max_enrich:
            truncated = True
            continue
        try:
            body = enrich_one(company, client, job)
        except Exception:  # noqa: BLE001 — one detail 404 must not kill the company
            body = None
        if body:
            job.description = body
            fetched += 1
            if verbose:
                err_console.print(f"[dim]enrich[/dim] {company.id} {job.job_id}")
    return cached, fetched, truncated


def _install_limiter(client, limiter: RateLimiter) -> None:
    original = client.request

    def wrapped(method, url, **kwargs):
        host = str(url).split("/")[2] if "://" in str(url) else ""
        limiter.wait(host)
        return original(method, url, **kwargs)

    client.request = wrapped  # type: ignore[method-assign]


def _location_settings(company: Company, settings: FilterSettings) -> LocationSettings:
    if company.locations is not None:
        raw = company.locations
        return raw if isinstance(raw, LocationSettings) else parse_location_settings(raw)
    override = company.extra.get("locations") if company.extra else None
    if override is not None:
        return parse_location_settings(override)
    raw = settings.locations
    if isinstance(raw, LocationSettings):
        return raw
    return parse_location_settings(raw)


def known_ats() -> list[str]:
    return list(ADAPTERS.keys())
