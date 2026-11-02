from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.optiver import API_URL, OptiverAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "optiver"
ADAPTER = OptiverAdapter()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_identity() -> None:
    found = ADAPTER.parse_identity("https://www.optiver.com/join-us/jobs/")
    assert found == {"ats": "optiver", "host": "www.optiver.com"}
    assert ADAPTER.matches_url("https://optiver.com/en/api/v1/jobs")
    assert not ADAPTER.matches_url("https://www.optiver.com/")


@respx.mock
def test_pages_with_from_until_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.optiver.PAGE_SIZE", 1)
    route = respx.get(API_URL)
    route.side_effect = [
        httpx.Response(200, json=_load("jobs_from0.json")),
        httpx.Response(200, json=_load("jobs_from1.json")),
    ]
    jobs = ADAPTER.fetch(company(id="optiver", ats="optiver"), httpx.Client(), include_content=True)
    assert [job.title for job in jobs] == ["Software Engineer", "Quantitative Researcher"]
    assert jobs[0].url == "https://www.optiver.com/join-us/jobs/technology/austin/software-engineer-2/"
    assert jobs[0].location == "Austin"
    assert jobs[0].department == "Technology"
    assert jobs[0].description is None
    assert route.calls[0].request.url.params["from"] == "0"
    assert route.calls[1].request.url.params["from"] == "1"


@respx.mock
def test_max_pages_sets_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.optiver.PAGE_SIZE", 1)
    respx.get(API_URL).mock(return_value=httpx.Response(200, json=_load("jobs_from0.json")))
    target = company(id="optiver", ats="optiver", max_pages=1)
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert len(jobs) == 1
    assert target.extra["optiver_truncated"] is True


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get(API_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(company(id="optiver", ats="optiver"), httpx.Client())
    assert exc.value.retryable is True
