from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobwatch.adapters.ashby import AshbyAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ashby"


@respx.mock
def test_ashby_drops_unlisted_and_maps_fields() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "jobs.json").read_text())
    )
    jobs = AshbyAdapter().fetch(
        company(ats="ashby", board="acme", careers_url="https://jobs.ashbyhq.com/acme"),
        httpx.Client(),
    )
    assert len(jobs) == 1
    assert jobs[0].job_id == "7458d4e9-da2e-47bd-98cb-adfda43d42b2"
    assert jobs[0].title == "Software Engineer, Early Career"
    assert jobs[0].workplace == "Remote"
    assert jobs[0].department == "Engineering"


def test_ashby_parse_api_url() -> None:
    ident = AshbyAdapter().parse_identity("https://api.ashbyhq.com/posting-api/job-board/openai")
    assert ident["board"] == "openai"
