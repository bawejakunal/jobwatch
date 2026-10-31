from __future__ import annotations

import re

from jobwatch.models import Company, FilterSettings, NormalizedJob

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

JUNIOR_YEARS = re.compile(
    r"\b(?:0|1)\s*[-–]\s*(?:1|2)\s*years?\b"
    r"|\b(?:0|1)\s+to\s+(?:1|2)\s*years?\b"
    r"|\bless than (?:one|1|two|2) years?\b"
    r"|\bnew[\s-]+grad",
    re.I,
)
SENIOR_YEARS = re.compile(
    r"\b(?:[5-9]|1\d)\s*\+\s*years?\b"
    r"|\b(?:[5-9]|1\d)\s+(?:or more |plus )?\s*years?\s+(?:of )?(?:experience|exp)\b",
    re.I,
)

_WORDY = re.compile(r"^[a-z0-9]{1,4}$", re.I)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


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


def title_excluded(job: NormalizedJob, company: Company, settings: FilterSettings) -> bool:
    exclude = company.exclude_title if company.exclude_title is not None else settings.exclude_title
    title = _normalize(job.title)
    strong = any(_hit(title, token) for token in STRONG_INCLUDE) or preferred_year_hit(
        title, settings.preferred_grad_year
    )
    return bool(exclude) and _any_hit(title, exclude) and not strong


def title_included(job: NormalizedJob, company: Company, settings: FilterSettings) -> bool:
    include = company.include_title if company.include_title is not None else settings.include_title
    title = _normalize(job.title)
    return bool(include) and _any_hit(title, include)


def needs_description(job: NormalizedJob, company: Company, settings: FilterSettings) -> bool:
    """True when a detail fetch could change the keep/drop decision."""
    if company.scope == "all":
        return False
    if job.description:
        return False
    if title_excluded(job, company, settings):
        return False
    if title_included(job, company, settings):
        return False
    return True


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

    if company.scope == "all":
        return True, year_hit, "scope"

    include = company.include_title if company.include_title is not None else settings.include_title
    desc_include = settings.include_description or include
    exclude = company.exclude_title if company.exclude_title is not None else settings.exclude_title
    strong = any(_hit(title, token) for token in STRONG_INCLUDE) or preferred_year_hit(title, year)

    if exclude and _any_hit(title, exclude) and not strong:
        return False, year_hit, None

    if include and _any_hit(title, include):
        if settings.check_description and job.description and _description_too_senior(job.description):
            return False, year_hit, None
        return True, year_hit, "title"

    if settings.check_description and body:
        desc_hit = _any_hit(body, desc_include) or preferred_year_hit(body, year) or bool(
            JUNIOR_YEARS.search(job.description or "")
        )
        if desc_hit and not _description_too_senior(job.description or ""):
            return True, year_hit, "description"
    return False, year_hit, None


def _description_too_senior(description: str) -> bool:
    if JUNIOR_YEARS.search(description):
        return False
    return SENIOR_YEARS.search(description) is not None


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
