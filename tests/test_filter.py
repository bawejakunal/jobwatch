from __future__ import annotations

from jobwatch.filter import apply_filter, is_new_grad
from jobwatch.models import FilterSettings, NormalizedJob
from tests.conftest import company


def _job(title: str, description: str | None = None, job_id: str = "1") -> NormalizedJob:
    return NormalizedJob(
        company_id="acme",
        source="greenhouse",
        job_id=job_id,
        title=title,
        url="https://example.com/1",
        description=description,
    )


def test_include_and_exclude(filter_settings: FilterSettings) -> None:
    c = company()
    assert is_new_grad(_job("Software Engineer, New Grad"), c, filter_settings)
    assert is_new_grad(_job("2027 University Graduate"), c, filter_settings)
    assert is_new_grad(_job("Engineering Intern"), c, filter_settings)
    assert not is_new_grad(_job("Senior Software Engineer"), c, filter_settings)
    assert not is_new_grad(_job("Software Engineer"), c, filter_settings)
    assert not is_new_grad(_job("Software Engineer 2026"), c, filter_settings)


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
    year_in_body = _job("Software Engineer", "We are hiring the class of 2027.")
    noisy_university = _job("Software Engineer", "Must have a university degree and 8 years of experience.")
    mid = _job("Strategy Associate", "2–3 years at a top-tier consulting firm.")
    plus_one = _job("Sales Development Representative", "1+ years of sales experience overall.")
    assert is_new_grad(junior, c, filter_settings)
    assert not is_new_grad(seniorish, c, filter_settings)
    assert is_new_grad(hidden, c, filter_settings)
    assert is_new_grad(year_in_body, c, filter_settings)
    assert not is_new_grad(noisy_university, c, filter_settings)
    assert not is_new_grad(mid, c, filter_settings)
    assert not is_new_grad(plus_one, c, filter_settings)


def test_2027_ranked_first(filter_settings: FilterSettings) -> None:
    c = company()
    jobs = apply_filter(
        [
            _job("Engineering Intern", job_id="intern"),
            _job("Software Engineer, New Grad 2027", job_id="ng"),
        ],
        c,
        filter_settings,
    )
    assert [j.job_id for j in jobs] == ["ng", "intern"]
    assert jobs[0].year_match is True
    assert jobs[1].year_match is False
