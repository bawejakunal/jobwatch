from __future__ import annotations

from jobwatch.filter import is_new_grad
from jobwatch.models import FilterSettings, NormalizedJob
from tests.conftest import company


def _job(title: str, description: str | None = None) -> NormalizedJob:
    return NormalizedJob(
        company_id="acme",
        source="greenhouse",
        job_id="1",
        title=title,
        url="https://example.com/1",
        description=description,
    )


def test_include_and_exclude(filter_settings: FilterSettings) -> None:
    c = company()
    assert is_new_grad(_job("Software Engineer, New Grad"), c, filter_settings)
    assert is_new_grad(_job("2026 University Graduate"), c, filter_settings)
    assert is_new_grad(_job("Engineering Intern"), c, filter_settings)
    assert not is_new_grad(_job("Senior Software Engineer"), c, filter_settings)
    assert not is_new_grad(_job("Software Engineer"), c, filter_settings)


def test_strong_include_beats_seniority(filter_settings: FilterSettings) -> None:
    c = company()
    assert is_new_grad(_job("Staff Software Engineer, University Graduate"), c, filter_settings)


def test_lead_word_boundary(filter_settings: FilterSettings) -> None:
    c = company()
    assert is_new_grad(_job("Leadership Development Program, New Grad"), c, filter_settings)
    assert not is_new_grad(_job("Engineering Lead"), c, filter_settings)


def test_scope_all(filter_settings: FilterSettings) -> None:
    c = company(scope="all")
    assert is_new_grad(_job("Principal Engineer"), c, filter_settings)


def test_description_check(filter_settings: FilterSettings) -> None:
    filter_settings.check_description = True
    c = company()
    junior = _job("Junior Engineer", "0-2 years of experience preferred")
    seniorish = _job("Junior Engineer", "10+ years of experience required")
    hidden = _job("Software Engineer", "This is a new grad rotation. 0-2 years.")
    assert is_new_grad(junior, c, filter_settings)
    assert not is_new_grad(seniorish, c, filter_settings)
    assert is_new_grad(hidden, c, filter_settings)
