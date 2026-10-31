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
    r"\b(?:0|1|2)\s*[-–to]{1,3}\s*(?:1|2|3)\s*years?\b|\bnew[\s-]+grad",
    re.I,
)
SENIOR_YEARS = re.compile(
    r"\b(?:[5-9]|1\d)\s*\+\s*years?\b|\b(?:[5-9]|1\d)\s+(?:or more |plus )?\s*years?\s+(?:of )?(?:experience|exp)\b",
    re.I,
)

_WORDY = re.compile(r"^[a-z]{1,4}$", re.I)


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


def is_new_grad(job: NormalizedJob, company: Company, settings: FilterSettings) -> bool:
    if company.scope == "all":
        return True

    include = company.include_title if company.include_title is not None else settings.include_title
    exclude = company.exclude_title if company.exclude_title is not None else settings.exclude_title
    title = _normalize(job.title)
    strong = any(_hit(title, token) for token in STRONG_INCLUDE)

    if exclude and _any_hit(title, exclude) and not strong:
        return False
    if include and _any_hit(title, include):
        if settings.check_description and job.description and _description_too_senior(job.description):
            return False
        return True
    if settings.check_description and job.description:
        body = _normalize(job.description)
        if _any_hit(body, include) and not _description_too_senior(job.description):
            return True
    return False


def _description_too_senior(description: str) -> bool:
    if JUNIOR_YEARS.search(description):
        return False
    return SENIOR_YEARS.search(description) is not None


def apply_filter(
    jobs: list[NormalizedJob],
    company: Company,
    settings: FilterSettings,
) -> list[NormalizedJob]:
    return [job for job in jobs if is_new_grad(job, company, settings)]
