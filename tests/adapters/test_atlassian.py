from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.atlassian import JOBS_URL, AtlassianAdapter
from jobwatch.adapters.base import AdapterError
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "atlassian"
ADAPTER = AtlassianAdapter()


def test_parse_identity() -> None:
    found = ADAPTER.parse_identity("https://www.atlassian.com/company/careers/all-jobs")
    assert found == {"ats": "atlassian", "host": "www.atlassian.com"}
    assert not ADAPTER.matches_url("https://www.atlassian.com/software/jira")


@respx.mock
def test_reads_embedded_job_list() -> None:
    html = (FIXTURES / "all_jobs.html").read_text()
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, text=html))
    jobs = ADAPTER.fetch(company(id="atlassian", ats="atlassian"), httpx.Client(), include_content=True)
    assert [job.title for job in jobs] == [
        "Senior Software Engineer",
        "Software Engineer, 2027 Graduate",
    ]
    assert jobs[0].job_id == "26751"
    assert jobs[0].url == "https://careers-apac-atlassian.icims.com/jobs/26751/senior-software-engineer/job"
    assert jobs[0].location == "Bengaluru - India - Bengaluru"
    assert jobs[0].department == "Engineering"
    assert jobs[0].description is None
    assert jobs[1].places[0].country == "US"


@respx.mock
def test_missing_payload_is_an_error() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, text="<html>careers</html>"))
    with pytest.raises(AdapterError, match="no job payload"):
        ADAPTER.fetch(company(id="atlassian", ats="atlassian"), httpx.Client())


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get(JOBS_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(company(id="atlassian", ats="atlassian"), httpx.Client())
    assert exc.value.retryable is True
