from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.google import RESULTS_URL, GoogleAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "google"
ADAPTER = GoogleAdapter()


def _html(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_identity() -> None:
    found = ADAPTER.parse_identity(
        "https://www.google.com/about/careers/applications/jobs/results/?page=1"
    )
    assert found == {"ats": "google", "host": "www.google.com"}
    assert ADAPTER.matches_url("https://google.com/about/careers/applications/")
    assert not ADAPTER.matches_url("https://www.google.com/search?q=jobs")


@respx.mock
def test_parses_embedded_jobs_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.google.PAGE_SIZE", 1)
    route = respx.get(RESULTS_URL)
    route.side_effect = [
        httpx.Response(200, text=_html("results_page.html")),
        httpx.Response(200, text=_html("results_page2.html")),
        httpx.Response(200, text=_html("empty_page.html")),
    ]
    target = company(
        id="google",
        ats="google",
        host="www.google.com",
        careers_url=RESULTS_URL,
    )
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert [job.title for job in jobs] == [
        "Business Development Manager",
        "Software Engineer",
    ]
    assert jobs[0].job_id == "92363761649623750"
    assert jobs[0].url == f"{RESULTS_URL}92363761649623750"
    assert jobs[0].location == "Shanghai, China"
    assert jobs[0].places[0].country == "CN"
    assert jobs[0].posted_at == "2026-10-30T08:08:29Z"
    assert jobs[0].description is None
    assert jobs[1].location == "Mountain View, CA"
    assert "google_truncated" not in target.extra


@respx.mock
def test_include_content_keeps_role_and_qualifications() -> None:
    respx.get(RESULTS_URL).mock(return_value=httpx.Response(200, text=_html("results_page.html")))
    jobs = ADAPTER.fetch(company(id="google", ats="google"), httpx.Client(), include_content=True)
    assert jobs[0].description == "Grow advertising clients. 2 years of sales"


@respx.mock
def test_missing_payload_is_an_error() -> None:
    respx.get(RESULTS_URL).mock(return_value=httpx.Response(200, text="<html>empty</html>"))
    with pytest.raises(AdapterError, match="no job payload"):
        ADAPTER.fetch(company(id="google", ats="google"), httpx.Client())


@respx.mock
def test_max_pages_sets_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.google.PAGE_SIZE", 1)
    respx.get(RESULTS_URL).mock(return_value=httpx.Response(200, text=_html("results_page.html")))
    target = company(id="google", ats="google", max_pages=1)
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert len(jobs) == 1
    assert target.extra["google_truncated"] is True


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get(RESULTS_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(company(id="google", ats="google"), httpx.Client())
    assert exc.value.retryable is True
