from __future__ import annotations

import re

from jobwatch.models import Company, FilterSettings, NormalizedJob
from jobwatch.rolesearch import INDEX

STRONG_INCLUDE = (
    "new grad",
    "new-grad",
    "new graduate",
    "university grad",
    "university graduate",
    "college grad",
    "recent grad",
    "recent graduate",
    "entry level",
    "entry-level",
    "early career",
    "early-career",
    "intern",
    "internship",
    "graduate program",
    "graduate programme",
    "campus",
)

_WORDY = re.compile(r"^[a-z0-9]{1,4}$", re.I)
_STAFF_ROLE = re.compile(
    r"\bmembers?\s+of\s+technical\s+staff\b|\btechnical\s+staff\b|\bmts\b",
    re.I,
)


def _normalize(text: str) -> str:
    text = text.casefold().replace("\u2013", "-").replace("\u2014", "-").replace("\u2011", "-")
    return re.sub(r"\s+", " ", text).strip()


def _hit(haystack: str, needle: str) -> bool:
    needle = needle.strip()
    if not needle:
        return False
    folded = needle.casefold()
    if _WORDY.match(folded) or folded in {"lead", "intern", "junior", "campus"}:
        return re.search(rf"\b{re.escape(folded)}\b", haystack) is not None
    if folded == "jr" or folded == "jr.":
        return re.search(r"\bjr\.?\b", haystack) is not None
    if folded == "vp" or folded.startswith("vp "):
        return re.search(r"\bvp\b", haystack) is not None
    return folded in haystack


def _any_hit(haystack: str, needles: list[str]) -> bool:
    return any(_hit(haystack, n) for n in needles)


def preferred_year_hit(text: str, year: int) -> bool:
    if year <= 0:
        return False
    return re.search(rf"\b{year}\b", text) is not None


def _phrases(company: Company, settings: FilterSettings, name: str) -> list[str]:
    override = getattr(company, name)
    if override is not None:
        return override
    return getattr(settings, name)


def _lists(
    company: Company, settings: FilterSettings
) -> tuple[list[str], list[str], list[str], list[str]]:
    return (
        _phrases(company, settings, "role_title_keep"),
        _phrases(company, settings, "role_title_drop"),
        _phrases(company, settings, "role_title_software"),
        _phrases(company, settings, "role_body_software"),
    )


def _role_applies(company: Company) -> bool:
    """scope: all keeps every posting, except YC, where it only skips the new-grad label."""
    return not (company.scope == "all" and company.ats != "yc")


def _decide(
    title: str,
    department: str | None,
    body: str,
    lists: tuple[list[str], list[str], list[str], list[str]],
    *,
    search_body: bool,
) -> str:
    """Return title, description, exclude, or miss."""
    keep, drop, software, body_terms = lists
    with INDEX:
        INDEX.load(title, department, body if search_body else "")
        if INDEX.matches("title", keep):
            return "title"
        if INDEX.matches("title", drop):
            return "exclude"
        if INDEX.matches("title", software):
            return "title"
        department_is_software = bool(department) and INDEX.matches("department", software)
        if department_is_software and not INDEX.matches("department", drop):
            return "title"
        if search_body and body and INDEX.matches("body", body_terms):
            return "description"
    return "miss"


def _seniority_excluded(title: str, exclude: list[str]) -> bool:
    scrubbed = _normalize(_STAFF_ROLE.sub(" ", title))
    return bool(exclude) and _any_hit(scrubbed, exclude)


def _early_career(title: str, year: int) -> bool:
    return any(_hit(title, token) for token in STRONG_INCLUDE) or preferred_year_hit(title, year)


def needs_description(job: NormalizedJob, company: Company, settings: FilterSettings) -> bool:
    """True when a detail fetch could change the keep/drop decision."""
    if not _role_applies(company):
        return False
    if job.description or not settings.check_description:
        return False
    decision = _decide(
        job.title,
        job.department,
        "",
        _lists(company, settings),
        search_body=False,
    )
    return decision == "miss"


def is_new_grad(job: NormalizedJob, company: Company, settings: FilterSettings) -> bool:
    keep, _, _ = classify(job, company, settings)
    return keep


def classify(
    job: NormalizedJob, company: Company, settings: FilterSettings
) -> tuple[bool, bool, str | None]:
    """Return (keep, preferred_year_hit, match_on)."""
    year = settings.preferred_grad_year
    title = _normalize(job.title)
    body = _normalize(job.description) if job.description else ""
    year_hit = preferred_year_hit(title, year) or (bool(body) and preferred_year_hit(body, year))

    if not _role_applies(company):
        return True, year_hit, "scope"

    exclude = company.exclude_title if company.exclude_title is not None else settings.exclude_title
    decision = _decide(
        job.title,
        job.department,
        body,
        _lists(company, settings),
        search_body=bool(settings.check_description and body),
    )
    if decision not in {"title", "description"}:
        return False, year_hit, None
    if _seniority_excluded(title, exclude) and not _early_career(title, year):
        return False, year_hit, None
    return True, year_hit, decision


def apply_filter(
    jobs: list[NormalizedJob],
    company: Company,
    settings: FilterSettings,
) -> list[NormalizedJob]:
    kept: list[NormalizedJob] = []
    for job in jobs:
        keep, year_hit, match_on = classify(job, company, settings)
        job.year_match = year_hit
        job.match_on = match_on
        if keep:
            kept.append(job)
    kept.sort(key=lambda j: (not j.year_match, j.company_id, j.title.casefold()))
    return kept
