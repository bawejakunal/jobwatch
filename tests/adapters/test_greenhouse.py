from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobwatch.adapters.greenhouse import GreenhouseAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "greenhouse"


@respx.mock
def test_greenhouse_maps_fields_and_ignores_content_by_default() -> None:
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "jobs.json").read_text())
    )
    adapter = GreenhouseAdapter()
    jobs = adapter.fetch(
        company(ats="greenhouse", board="acme", careers_url="https://job-boards.greenhouse.io/acme"),
        httpx.Client(),
    )
    assert route.called
    assert "content=" not in str(route.calls.last.request.url)
    assert [j.job_id for j in jobs] == ["1001", "1002"]
    assert jobs[0].title == "Software Engineer, New Grad"
    assert jobs[0].url == "https://job-boards.greenhouse.io/acme/jobs/1001"
    assert jobs[0].location == "Remote - US"
    assert jobs[0].posted_at == "2026-09-24T17:26:49-04:00"
    assert jobs[0].description is None


@respx.mock
def test_greenhouse_content_true_when_asked() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "jobs_content.json").read_text())
    )
    jobs = GreenhouseAdapter().fetch(
        company(ats="greenhouse", board="acme", careers_url="https://boards.greenhouse.io/acme"),
        httpx.Client(),
        include_content=True,
    )
    assert jobs[0].department == "Engineering"
    assert "new graduates" in (jobs[0].description or "")
    assert "content=true" in str(respx.calls.last.request.url)


def test_greenhouse_parse_embed_for_param() -> None:
    ident = GreenhouseAdapter().parse_identity(
        "https://boards.greenhouse.io/embed/job_board?for=discord"
    )
    assert ident["board"] == "discord"
