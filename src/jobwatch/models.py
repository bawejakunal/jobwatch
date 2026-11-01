from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from jobwatch.rolesearch import (
    default_role_body_software,
    default_role_title_drop,
    default_role_title_keep,
    default_role_title_software,
)

ATSName = Literal[
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "smartrecruiters",
    "icims",
    "yc",
    "browser",
]

Scope = Literal["new_grad", "all"]


@dataclass(slots=True)
class NormalizedJob:
    company_id: str
    source: str
    job_id: str
    title: str
    url: str
    location: str | None = None
    places: list = field(default_factory=list)
    location_ambiguous: bool = False
    department: str | None = None
    workplace: str | None = None
    posted_at: str | None = None
    description: str | None = None
    fingerprint: str | None = None
    detail_ref: str | None = None
    year_match: bool = False
    match_on: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class Company:
    id: str
    name: str
    careers_url: str
    ats: str | None = None
    board: str | None = None
    host: str | None = None
    tenant: str | None = None
    site: str | None = None
    locale: str | None = None
    portal: str | None = None
    region: str | None = None
    scope: Scope = "new_grad"
    enabled: bool = True
    include_title: list[str] | None = None
    exclude_title: list[str] | None = None
    role_title_keep: list[str] | None = None
    role_title_drop: list[str] | None = None
    role_title_software: list[str] | None = None
    role_body_software: list[str] | None = None
    locations: object | None = None
    timeout_s: float | None = None
    max_pages: int | None = None
    max_age_days: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def display_name(self) -> str:
        return self.name or self.id


@dataclass(slots=True)
class Detection:
    ats: ATSName
    careers_url: str
    identity: dict[str, str]
    confidence: Literal["url", "html", "hint", "probe"] = "url"
    notes: str = ""


@dataclass(slots=True)
class FilterSettings:
    include_title: list[str]
    exclude_title: list[str]
    check_description: bool = True
    description_exclude: list[str] = field(default_factory=list)
    include_description: list[str] = field(default_factory=list)
    role_title_keep: list[str] = field(default_factory=default_role_title_keep)
    role_title_drop: list[str] = field(default_factory=default_role_title_drop)
    role_title_software: list[str] = field(default_factory=default_role_title_software)
    role_body_software: list[str] = field(default_factory=default_role_body_software)
    preferred_grad_year: int = 2027
    max_enrich: int = 80
    locations: object | None = None


@dataclass(slots=True)
class Defaults:
    include_title: list[str]
    exclude_title: list[str]
    check_description: bool = True
    description_exclude: list[str] = field(default_factory=list)
    include_description: list[str] = field(default_factory=list)
    role_title_keep: list[str] = field(default_factory=default_role_title_keep)
    role_title_drop: list[str] = field(default_factory=default_role_title_drop)
    role_title_software: list[str] = field(default_factory=default_role_title_software)
    role_body_software: list[str] = field(default_factory=default_role_body_software)
    preferred_grad_year: int = 2027
    max_enrich: int = 80
    locations: object | None = None
    rate_limit_rps: float = 1.5
    timeout_s: float = 25.0
    max_pages: int | None = None


@dataclass(slots=True)
class AppConfig:
    defaults: Defaults
    companies: list[Company]
    path: str | None = None


@dataclass(slots=True)
class CompanyResult:
    company: Company
    jobs: list[NormalizedJob] = field(default_factory=list)
    new_jobs: list[NormalizedJob] = field(default_factory=list)
    error: str | None = None
    needs_browser: bool = False
    warning: str | None = None
    enriched: int = 0
    cached_descriptions: int = 0
