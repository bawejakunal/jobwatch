from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.apple import AppleAdapter
from jobwatch.adapters.base import AdapterError
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "apple"
ADAPTER = AppleAdapter()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _csrf() -> httpx.Response:
    return httpx.Response(200, headers={"X-Apple-CSRF-Token": "token"})


def test_parse_identity() -> None:
    found = ADAPTER.parse_identity("https://jobs.apple.com/en-us/search")
    assert found == {"ats": "apple", "host": "jobs.apple.com", "locale": "en-us"}
    assert ADAPTER.matches_url("https://jobs.apple.com/en-us/search")
    assert not ADAPTER.matches_url("https://www.apple.com/careers")


@respx.mock
def test_search_sends_format_block_and_maps_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.apple.PAGE_SIZE", 1)
    respx.get("https://jobs.apple.com/api/v1/CSRFToken").mock(return_value=_csrf())
    first = _load("search_page.json")
    second = json.loads(json.dumps(first))
    second["res"]["searchResults"][0]["id"] = "PIPE-2"
    second["res"]["searchResults"][0]["positionId"] = "2"
    second["res"]["searchResults"][0]["postingTitle"] = "Software Engineer"
    second["res"]["searchResults"][0]["transformedPostingTitle"] = "software-engineer"
    route = respx.post("https://jobs.apple.com/api/v1/search")
    route.side_effect = [
        httpx.Response(200, json=first),
        httpx.Response(200, json=second),
    ]
    target = company(
        id="apple",
        ats="apple",
        host="jobs.apple.com",
        locale="en-us",
        careers_url="https://jobs.apple.com/en-us/search",
    )
    jobs = ADAPTER.fetch(target, httpx.Client())
    body = json.loads(route.calls[0].request.content)
    assert body["format"] == {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
    assert body["query"] == ""
    assert body["locale"] == "en-us"
    assert body["page"] == 1
    assert route.calls[0].request.headers["X-Apple-CSRF-Token"] == "token"
    assert [job.title for job in jobs] == ["Technical Specialist", "Software Engineer"]
    assert jobs[0].url == "https://jobs.apple.com/en-us/details/114438210/technical-specialist"
    assert jobs[0].location == "Cupertino, California, United States"
    assert jobs[0].places[0].country == "US"
    assert jobs[0].department == "Apple Retail"
    assert jobs[0].posted_at == "2026-11-02T00:41:36.350Z"
    assert jobs[0].description is None


@respx.mock
def test_include_content_keeps_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.apple.PAGE_SIZE", 1)
    respx.get("https://jobs.apple.com/api/v1/CSRFToken").mock(return_value=_csrf())
    page = _load("search_page.json")
    page["res"]["totalRecords"] = 1
    respx.post("https://jobs.apple.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=page)
    )
    jobs = ADAPTER.fetch(
        company(id="apple", ats="apple", host="jobs.apple.com", locale="en-us"),
        httpx.Client(),
        include_content=True,
    )
    assert jobs[0].description == "Offer technical support in Apple Retail."


@respx.mock
def test_max_pages_sets_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.apple.PAGE_SIZE", 1)
    respx.get("https://jobs.apple.com/api/v1/CSRFToken").mock(return_value=_csrf())
    page = _load("search_page.json")
    page["res"]["totalRecords"] = 5
    respx.post("https://jobs.apple.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=page)
    )
    target = company(id="apple", ats="apple", host="jobs.apple.com", locale="en-us", max_pages=1)
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert len(jobs) == 1
    assert target.extra["apple_truncated"] is True


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get("https://jobs.apple.com/api/v1/CSRFToken").mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(
            company(id="apple", ats="apple", host="jobs.apple.com", locale="en-us"),
            httpx.Client(),
        )
    assert exc.value.retryable is True
