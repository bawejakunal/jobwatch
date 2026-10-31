from __future__ import annotations

from pathlib import Path

import pytest

from jobwatch.models import Company, Defaults, FilterSettings
from jobwatch.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def filter_settings() -> FilterSettings:
    return FilterSettings(
        include_title=list(DEFAULT_INCLUDE),
        exclude_title=list(DEFAULT_EXCLUDE),
        check_description=False,
    )


def company(**kwargs) -> Company:
    base = dict(
        id="acme",
        name="Acme",
        careers_url="https://example.com/jobs",
        enabled=True,
    )
    base.update(kwargs)
    return Company(**base)  # type: ignore[arg-type]


@pytest.fixture
def defaults() -> Defaults:
    return Defaults(
        include_title=list(DEFAULT_INCLUDE),
        exclude_title=list(DEFAULT_EXCLUDE),
    )
