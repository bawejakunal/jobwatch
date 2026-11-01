from __future__ import annotations

from pathlib import Path

from jobwatch.config import filter_settings, load_config
from jobwatch.filter import apply_filter, is_new_grad, needs_description
from jobwatch.models import FilterSettings, NormalizedJob
from jobwatch.rolesearch import ROLE_TITLE_KEEP
from tests.conftest import company


def _job(
    title: str,
    description: str | None = None,
    job_id: str = "1",
    department: str | None = None,
) -> NormalizedJob:
    return NormalizedJob(
        company_id="acme",
        source="greenhouse",
        job_id=job_id,
        title=title,
        url="https://example.com/1",
        description=description,
        department=department,
    )


def test_software_titles_are_kept_without_a_new_grad_label(filter_settings: FilterSettings) -> None:
    c = company()
    kept = [
        "Software Engineer, New Grad",
        "Engineering Intern",
        "Software Engineer",
        "Software Engineer 2026",
        "Founding Engineer",
        "Backend Engineer",
        "Member of Technical Staff",
        "Forward Deployed Engineer",
        "Forward-Deployed Engineer",
        "Growth Engineer",
        "GTM Engineer",
        "Go-to-Market Engineer",
        "Solutions Engineer",
        "Support Engineer",
        "Technical Support Engineer",
        "Flight Software Intern",
        "Salesforce Engineer",
    ]
    for title in kept:
        assert is_new_grad(_job(title), c, filter_settings), title
    assert not is_new_grad(_job("2027 University Graduate"), c, filter_settings)
    assert not is_new_grad(_job("Senior Software Engineer"), c, filter_settings)


def test_strong_include_beats_seniority(filter_settings: FilterSettings) -> None:
    c = company()
    assert is_new_grad(_job("Staff Software Engineer, University Graduate"), c, filter_settings)
    intern = "Member of Technical Staff, Research (Intern Winter 2027)"
    assert is_new_grad(_job(intern), c, filter_settings)


def test_lead_word_boundary(filter_settings: FilterSettings) -> None:
    c = company()
    assert is_new_grad(_job("Software Engineer, Technical Leadership"), c, filter_settings)
    assert not is_new_grad(_job("Engineering Lead"), c, filter_settings)
    assert not is_new_grad(_job("Leadership Development Program, New Grad"), c, filter_settings)


def test_scope_all_non_yc_keeps_every_title(filter_settings: FilterSettings) -> None:
    c = company(scope="all")
    assert is_new_grad(_job("Principal Engineer"), c, filter_settings)
    assert is_new_grad(_job("Account Executive"), c, filter_settings)


def test_yc_keeps_software_and_drops_business(filter_settings: FilterSettings) -> None:
    c = company(ats="yc", scope="all")
    dropped = [
        "Enterprise Account Executive - EMEA",
        "Enterprise Account Executive - North America",
        "Marketing Manager, Lifecycle",
        "People Operations Generalist",
        "Professional Services Associate (EPM)",
        "Professional Services Manager",
        "Sales Director – NA",
        "Senior Partner Account Manager",
        "Senior Talent Acquisition Partner",
        "Founding Account Executive",
        "Founding GTM",
        "Sales Engineer",
        "Associate Sales Engineer",
        "Customer Success Engineer",
        "Customer Success Manager",
        "Product Manager",
        "Product Designer",
        "Data Scientist",
        "Senior Software Engineer",
        "Engineering Manager",
        "Head of Recruiting",
        "MTS - Founding Designer",
        "MTS - Forward Deployed Mechanical Engineer",
        "Strategist, Agent Development (MBA Grad 2027)",
        "Research Intern",
    ]
    for title in dropped:
        assert not is_new_grad(_job(title), c, filter_settings), title
    assert is_new_grad(_job("Builder", department="Engineering"), c, filter_settings)
    labeled = _job("Account Executive", department="Engineering")
    assert not is_new_grad(labeled, c, filter_settings)


def test_description_rescues_only_a_software_phrase(filter_settings: FilterSettings) -> None:
    filter_settings.check_description = True
    c = company()
    junior = _job("Junior Engineer", "10+ years of experience required")
    assert is_new_grad(junior, c, filter_settings)
    assert is_new_grad(
        _job("APX (New Grad 2027)", "Professional experience in software engineering."),
        c,
        filter_settings,
    )
    assert not is_new_grad(
        _job("Research Intern", "Work with researchers and engineers across the company."),
        c,
        filter_settings,
    )
    assert not is_new_grad(
        _job("University Graduate", "This is a finance rotation for new graduates."),
        c,
        filter_settings,
    )
    assert not is_new_grad(
        _job("Marketing Intern", "You will partner with software engineering."),
        c,
        filter_settings,
    )
    strategy = _job("Strategy Associate", "2–3 years at a top-tier consulting firm.")
    assert not is_new_grad(strategy, c, filter_settings)
    year_in_body = _job("Software Engineer", "We are hiring the class of 2027.")
    assert is_new_grad(year_in_body, c, filter_settings)


def test_undecided_title_needs_a_description(filter_settings: FilterSettings) -> None:
    filter_settings.check_description = True
    c = company()
    yc = company(ats="yc", scope="all")
    campus = company(scope="all")
    assert needs_description(_job("University Graduate"), c, filter_settings)
    assert needs_description(_job("APX (New Grad 2027)"), yc, filter_settings)
    assert not needs_description(_job("Software Engineer"), c, filter_settings)
    assert not needs_description(_job("Account Executive"), yc, filter_settings)
    assert not needs_description(_job("University Graduate"), campus, filter_settings)
    filter_settings.check_description = False
    assert not needs_description(_job("University Graduate"), c, filter_settings)


def test_company_drop_list_replaces_the_default(filter_settings: FilterSettings) -> None:
    c = company(role_title_drop=["engineer"])
    assert not is_new_grad(_job("Software Engineer"), c, filter_settings)
    assert is_new_grad(_job("GTM Engineer"), c, filter_settings)


def test_missing_yaml_keys_use_code_defaults(tmp_path: Path) -> None:
    path = tmp_path / "companies.yaml"
    path.write_text(
        """
defaults:
  include_title: [intern]
companies:
  - id: acme
    name: Acme
    careers_url: https://example.com
    role_title_keep: ["only this phrase"]
"""
    )
    config = load_config(path)
    settings = filter_settings(config)
    assert settings.role_title_keep == list(ROLE_TITLE_KEEP)
    assert config.companies[0].role_title_keep == ["only this phrase"]
    assert config.companies[0].role_title_drop is None


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
