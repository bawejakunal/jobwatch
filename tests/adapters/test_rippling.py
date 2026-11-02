from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.rippling import RipplingAdapter
from jobwatch.models import NormalizedJob
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rippling"
ADAPTER = RipplingAdapter()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_identity() -> None:
    public = ADAPTER.parse_identity("https://ats.rippling.com/rippling/jobs")
    assert public == {"ats": "rippling", "host": "ats.rippling.com", "board": "rippling"}
    api = ADAPTER.parse_identity("https://ats.rippling.com/api/v2/board/rippling/jobs?page=0")
    assert api["board"] == "rippling"
    assert ADAPTER.matches_url("https://ats.rippling.com/rippling/jobs")
    assert not ADAPTER.matches_url("https://app.rippling.com/")


def test_sniff_html() -> None:
    html = '<a href="https://ats.rippling.com/rippling/jobs">Jobs</a>'
    found = ADAPTER.sniff_html("https://www.rippling.com/careers", html)
    assert found is not None
    assert found["board"] == "rippling"


@respx.mock
def test_pages_until_total_pages() -> None:
    route = respx.get("https://ats.rippling.com/api/v2/board/rippling/jobs")
    route.side_effect = [
        httpx.Response(200, json=_load("jobs_page0.json")),
        httpx.Response(200, json=_load("jobs_page1.json")),
    ]
    jobs = ADAPTER.fetch(
        company(
            id="rippling",
            ats="rippling",
            board="rippling",
            host="ats.rippling.com",
            careers_url="https://ats.rippling.com/rippling/jobs",
        ),
        httpx.Client(),
        include_content=True,
    )
    assert [job.title for job in jobs] == ["Business Operations Manager", "Software Engineer"]
    assert jobs[0].location == "San Francisco, CA"
    assert jobs[0].department == "Bizops"
    assert jobs[0].workplace == "on_site"
    assert jobs[0].places[0].country == "US"
    assert jobs[0].description is None
    assert jobs[0].url.endswith("/84d388b6-7656-434c-8862-0312eb6b97ac")


@respx.mock
def test_max_pages_sets_truncated() -> None:
    respx.get("https://ats.rippling.com/api/v2/board/rippling/jobs").mock(
        return_value=httpx.Response(200, json=_load("jobs_page0.json"))
    )
    target = company(id="rippling", ats="rippling", board="rippling", max_pages=1)
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert len(jobs) == 1
    assert target.extra["rippling_truncated"] is True


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get("https://ats.rippling.com/api/v2/board/rippling/jobs").mock(
        return_value=httpx.Response(429)
    )
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(company(id="rippling", ats="rippling", board="rippling"), httpx.Client())
    assert exc.value.retryable is True


@respx.mock
def test_enrich_one() -> None:
    respx.get(
        "https://ats.rippling.com/api/v2/board/rippling/jobs/84d388b6-7656-434c-8862-0312eb6b97ac"
    ).mock(return_value=httpx.Response(200, json=_load("job_detail.json")))
    job = NormalizedJob(
        company_id="rippling",
        source="rippling",
        job_id="84d388b6-7656-434c-8862-0312eb6b97ac",
        title="Business Operations Manager",
        url="https://ats.rippling.com/rippling/jobs/84d388b6-7656-434c-8862-0312eb6b97ac",
    )
    body = ADAPTER.enrich_one(
        company(id="rippling", ats="rippling", board="rippling"),
        httpx.Client(),
        job,
    )
    assert body == "Own operations for the team. Rippling builds HR software."
