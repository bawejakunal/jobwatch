from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.amazon import AmazonAdapter
from jobwatch.adapters.base import AdapterError
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "amazon"
ADAPTER = AmazonAdapter()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_identity() -> None:
    found = ADAPTER.parse_identity("https://www.amazon.jobs/en/search")
    assert found == {"ats": "amazon", "host": "www.amazon.jobs", "locale": "en"}
    assert ADAPTER.matches_url("https://amazon.jobs/en/search.json")
    assert not ADAPTER.matches_url("https://www.amazon.com/jobs")


@respx.mock
def test_maps_job_and_keeps_description_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jobwatch.adapters.amazon.PAGE_SIZE", 1)
    page = _load("search_page.json")
    page["hits"] = 1
    respx.get("https://www.amazon.jobs/en/search.json").mock(
        return_value=httpx.Response(200, json=page)
    )
    target = company(
        id="amazon",
        ats="amazon",
        host="www.amazon.jobs",
        locale="en",
        careers_url="https://www.amazon.jobs/en/search",
    )
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert jobs[0].title == "Data Center IT Support Engineer"
    assert jobs[0].job_id == "10535680"
    assert jobs[0].url == "https://www.amazon.jobs/en/jobs/10535680/data-center-it-support-engineer"
    assert jobs[0].location == "Tempe, Arizona, USA"
    assert jobs[0].places[0].country == "US"
    assert jobs[0].department == "AWS Infrastructure"
    assert jobs[0].workplace == "onsite"
    assert jobs[0].posted_at == "September 10, 2026"
    assert jobs[0].description is None

    with_body = ADAPTER.fetch(target, httpx.Client(), include_content=True)
    assert with_body[0].description == (
        "Work in a large data center. 1+ years of networking Bachelor's degree"
    )


@respx.mock
def test_pages_by_offset_and_warns_at_hit_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.amazon.PAGE_SIZE", 1)
    first = _load("search_page.json")
    second = json.loads(json.dumps(first))
    second["jobs"][0]["id_icims"] = "10535681"
    second["jobs"][0]["title"] = "Second Role"
    second["jobs"][0]["job_path"] = "/en/jobs/10535681/second-role"
    route = respx.get("https://www.amazon.jobs/en/search.json")
    route.side_effect = [
        httpx.Response(200, json=first),
        httpx.Response(200, json=second),
    ]
    target = company(id="amazon", ats="amazon", host="www.amazon.jobs", locale="en")
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert [job.job_id for job in jobs] == ["10535680", "10535681"]

    capped = dict(first)
    capped["hits"] = 10000
    route.side_effect = [httpx.Response(200, json=capped)]
    target = company(id="amazon", ats="amazon", host="www.amazon.jobs", locale="en", max_pages=1)
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert len(jobs) == 1
    assert target.extra["amazon_truncated"] is True
    assert target.extra["amazon_hits"] == 10000


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get("https://www.amazon.jobs/en/search.json").mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(
            company(id="amazon", ats="amazon", host="www.amazon.jobs", locale="en"),
            httpx.Client(),
        )
    assert exc.value.retryable is True
