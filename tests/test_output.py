from __future__ import annotations

from jobwatch.models import CompanyResult, NormalizedJob
from jobwatch.output import company_progress_lines
from tests.conftest import company


def _job(**kwargs) -> NormalizedJob:
    base = dict(
        company_id="infer",
        source="yc",
        job_id="1",
        title="Founding Engineer",
        url="https://www.ycombinator.com/companies/infer/jobs/aaa",
        location="San Francisco, CA, US",
    )
    base.update(kwargs)
    return NormalizedJob(**base)  # type: ignore[arg-type]


def test_progress_line_counts_new_jobs_and_prints_match() -> None:
    result = CompanyResult(
        company=company(id="infer", name="Infer"),
        new_jobs=[_job()],
    )
    lines = company_progress_lines(42, 1209, result, show_all=False)
    assert lines[0] == "[42/1209] Infer  1 new"
    assert "Founding Engineer" in lines[1]
    assert "https://www.ycombinator.com/companies/infer/jobs/aaa" in lines[1]
    assert "San Francisco, CA, US" in lines[1]


def test_progress_line_with_no_matches() -> None:
    result = CompanyResult(company=company(id="infer", name="Infer"))
    lines = company_progress_lines(1, 3, result, show_all=False)
    assert lines == ["[1/3] Infer  0 new"]


def test_progress_line_open_count_and_warning() -> None:
    result = CompanyResult(
        company=company(id="infer", name="Infer"),
        jobs=[_job(), _job(job_id="2", title="Member of Technical Staff")],
        warning="description enrich capped",
    )
    lines = company_progress_lines(2, 10, result, show_all=True)
    assert lines[0] == "[2/10] Infer  2 open"
    assert lines[-1] == "warning: description enrich capped"


def test_progress_line_failed_skips_jobs() -> None:
    result = CompanyResult(
        company=company(id="infer", name="Infer"),
        new_jobs=[_job()],
        error="YC HTTP 404",
    )
    lines = company_progress_lines(3, 10, result, show_all=False)
    assert lines == ["[3/10] Infer  failed"]
