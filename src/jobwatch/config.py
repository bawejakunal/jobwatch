from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from jobwatch.locations import LocationSettings, parse_location_settings
from jobwatch.models import AppConfig, Company, Defaults, FilterSettings, Scope

DEFAULT_INCLUDE = [
    "new grad",
    "new graduate",
    "new-grad",
    "university grad",
    "university graduate",
    "college grad",
    "recent grad",
    "recent graduate",
    "entry level",
    "entry-level",
    "early career",
    "early-career",
    "junior",
    "intern",
    "internship",
    "campus",
    "graduate program",
    "graduate programme",
    "2027",
]

# Stricter than titles: bare "university" / "campus" in a JD is noise.
DEFAULT_DESCRIPTION_INCLUDE = [
    "new grad",
    "new graduate",
    "new-grad",
    "university grad",
    "university graduate",
    "college grad",
    "college graduate",
    "recent grad",
    "recent graduate",
    "entry level",
    "entry-level",
    "early career",
    "early-career",
    "junior",
    "intern",
    "internship",
    "graduate program",
    "graduate programme",
]

DEFAULT_EXCLUDE = [
    "senior",
    "staff",
    "principal",
    "director",
    "head of",
    "architect",
    "distinguished",
    "vp",
    "manager",
    "lead",
]

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_COMPANIES = PACKAGE_ROOT / "companies.example.yaml"


def example_companies_text() -> str:
    if EXAMPLE_COMPANIES.is_file():
        return EXAMPLE_COMPANIES.read_text()
    bundled = Path(__file__).resolve().parent / "data" / "companies.example.yaml"
    if bundled.is_file():
        return bundled.read_text()
    from importlib.resources import files

    return files("jobwatch.data").joinpath("companies.example.yaml").read_text()


def default_home() -> Path:
    raw = os.environ.get("JOBWATCH_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "jobwatch"


def load_env(cwd: Path | None = None) -> None:
    cwd = cwd or Path.cwd()
    load_dotenv(cwd / ".env", override=False)
    load_dotenv(default_home() / ".env", override=False)


def find_config_path(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("JOBWATCH_CONFIG")
    if env:
        return Path(env).expanduser()
    candidates = [
        Path.cwd() / "companies.yaml",
        Path.cwd() / ".jobwatch" / "companies.yaml",
        default_home() / "companies.yaml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_db_path(explicit: str | None = None, config_path: Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("JOBWATCH_DB")
    if env:
        return Path(env).expanduser()
    if config_path and config_path.parent == Path.cwd():
        return Path.cwd() / "jobwatch.sqlite"
    if config_path and config_path.parent == Path.cwd() / ".jobwatch":
        return Path.cwd() / ".jobwatch" / "jobwatch.sqlite"
    return default_home() / "jobwatch.sqlite"


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    defaults_raw = raw.get("defaults") or {}
    defaults = Defaults(
        include_title=list(defaults_raw.get("include_title") or DEFAULT_INCLUDE),
        exclude_title=list(defaults_raw.get("exclude_title") or DEFAULT_EXCLUDE),
        check_description=bool(defaults_raw.get("check_description", True)),
        description_exclude=list(defaults_raw.get("description_exclude") or []),
        include_description=list(defaults_raw.get("include_description") or DEFAULT_DESCRIPTION_INCLUDE),
        preferred_grad_year=int(defaults_raw.get("preferred_grad_year") or 2027),
        max_enrich=int(defaults_raw.get("max_enrich") or 80),
        locations=parse_location_settings(defaults_raw.get("locations")),
        rate_limit_rps=float(defaults_raw.get("rate_limit_rps") or 1.5),
        timeout_s=float(defaults_raw.get("timeout_s") or 25.0),
        max_pages=defaults_raw.get("max_pages"),
    )
    companies = [_company(item, defaults) for item in raw.get("companies") or []]
    return AppConfig(defaults=defaults, companies=companies, path=str(path))


def filter_settings(
    config: AppConfig,
    *,
    check_description: bool | None = None,
    any_location: bool = False,
) -> FilterSettings:
    locations = config.defaults.locations
    if not isinstance(locations, LocationSettings):
        locations = parse_location_settings(locations)
    if any_location:
        locations = LocationSettings(enabled=False, countries=list(locations.countries), mode=locations.mode)
    return FilterSettings(
        include_title=config.defaults.include_title,
        exclude_title=config.defaults.exclude_title,
        check_description=(
            config.defaults.check_description if check_description is None else check_description
        ),
        description_exclude=config.defaults.description_exclude,
        include_description=config.defaults.include_description or list(DEFAULT_DESCRIPTION_INCLUDE),
        preferred_grad_year=config.defaults.preferred_grad_year,
        max_enrich=config.defaults.max_enrich,
        locations=locations,
    )


def _company(item: dict[str, Any], defaults: Defaults) -> Company:
    scope: Scope = item.get("scope") or "new_grad"
    if scope not in {"new_grad", "all"}:
        scope = "new_grad"
    return Company(
        id=str(item["id"]),
        name=str(item.get("name") or item["id"]),
        careers_url=str(item["careers_url"]),
        ats=item.get("ats"),
        board=item.get("board"),
        host=item.get("host"),
        tenant=item.get("tenant"),
        site=item.get("site"),
        locale=item.get("locale"),
        portal=item.get("portal"),
        region=item.get("region"),
        scope=scope,
        enabled=bool(item.get("enabled", True)),
        include_title=item.get("include_title"),
        exclude_title=item.get("exclude_title"),
        locations=parse_location_settings(item["locations"]) if "locations" in item else None,
        timeout_s=item.get("timeout_s") or defaults.timeout_s,
        max_pages=item.get("max_pages", defaults.max_pages),
        max_age_days=_optional_int(item.get("max_age_days")),
        extra={k: v for k, v in item.items() if k not in _KNOWN},
    )


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)  # type: ignore[arg-type]


_KNOWN = {
    "id",
    "name",
    "careers_url",
    "ats",
    "board",
    "host",
    "tenant",
    "site",
    "locale",
    "portal",
    "region",
    "scope",
    "enabled",
    "include_title",
    "exclude_title",
    "timeout_s",
    "max_pages",
    "max_age_days",
    "locations",
}


def _locations_to_dict(raw: object | None) -> dict[str, object]:
    settings = raw if isinstance(raw, LocationSettings) else parse_location_settings(raw)
    return {
        "mode": settings.mode,
        "countries": list(settings.countries),
    }


def dump_config(config: AppConfig, path: Path) -> None:
    payload: dict[str, Any] = {
        "defaults": {
            "include_title": config.defaults.include_title,
            "exclude_title": config.defaults.exclude_title,
            "include_description": config.defaults.include_description,
            "check_description": config.defaults.check_description,
            "description_exclude": config.defaults.description_exclude,
            "preferred_grad_year": config.defaults.preferred_grad_year,
            "max_enrich": config.defaults.max_enrich,
            "locations": _locations_to_dict(config.defaults.locations),
            "rate_limit_rps": config.defaults.rate_limit_rps,
            "timeout_s": config.defaults.timeout_s,
            "max_pages": config.defaults.max_pages,
        },
        "companies": [_company_to_dict(c, config.defaults) for c in config.companies],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=4096))


def _company_to_dict(company: Company, defaults: Defaults | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": company.id,
        "name": company.name,
        "careers_url": company.careers_url,
    }
    for key in (
        "ats",
        "board",
        "host",
        "tenant",
        "site",
        "locale",
        "portal",
        "region",
        "scope",
        "enabled",
        "include_title",
        "exclude_title",
        "locations",
        "timeout_s",
        "max_pages",
        "max_age_days",
    ):
        value = getattr(company, key)
        if value is None:
            continue
        if key == "enabled" and value is True:
            continue
        if key == "scope" and value == "new_grad":
            continue
        if key == "timeout_s" and defaults is not None and value == defaults.timeout_s:
            continue
        if key == "max_pages" and defaults is not None and value == defaults.max_pages:
            continue
        if key == "locations" and value is not None:
            data[key] = _locations_to_dict(value)
            continue
        data[key] = value
    return data


def append_company(path: Path, company: Company) -> AppConfig:
    if path.is_file():
        config = load_config(path)
    else:
        config = AppConfig(
            defaults=Defaults(
                include_title=list(DEFAULT_INCLUDE),
                exclude_title=list(DEFAULT_EXCLUDE),
                include_description=list(DEFAULT_DESCRIPTION_INCLUDE),
            ),
            companies=[],
            path=str(path),
        )
    existing = {c.id for c in config.companies}
    if company.id in existing:
        config.companies = [company if c.id == company.id else c for c in config.companies]
    else:
        config.companies.append(company)
    dump_config(config, path)
    return config


def write_example(dest: Path, source: Path | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source and source.is_file():
        dest.write_text(source.read_text())
        return
    dest.write_text(example_companies_text())
