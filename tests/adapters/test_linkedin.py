from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.linkedin import GUEST_URL, LinkedInAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "linkedin"
ADAPTER = LinkedInAdapter()


def _html(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_identity() -> None:
    found = ADAPTER.parse_identity("https://www.linkedin.com/jobs/search/?f_C=1337,9202023")
    assert found == {"ats": "linkedin", "host": "www.linkedin.com", "board": "1337"}
    assert ADAPTER.matches_url("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search")
    assert not ADAPTER.matches_url("https://www.linkedin.com/company/linkedin")


@respx.mock
def test_guest_cards_page_until_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.linkedin.PAGE_SIZE", 1)
    route = respx.get(GUEST_URL)
    route.side_effect = [
        httpx.Response(200, text=_html("guest_page.html")),
        httpx.Response(200, text=_html("guest_page2.html")),
        httpx.Response(200, text="<html></html>"),
    ]
    jobs = ADAPTER.fetch(
        company(
            id="linkedin",
            ats="linkedin",
            board="1337",
            careers_url="https://www.linkedin.com/jobs/search/?f_C=1337",
        ),
        httpx.Client(),
    )
    assert [job.title for job in jobs] == ["Principal Engineer", "Staff Data Scientist"]
    assert jobs[0].job_id == "4460301137"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/principal-engineer-at-linkedin-4460301137"
    assert jobs[0].location == "Sunnyvale, CA"
    assert jobs[0].places[0].country == "US"
    assert jobs[0].posted_at == "2026-10-28"
    assert jobs[0].description is None
    assert "location" in str(route.calls[0].request.url)
    assert "Worldwide" in str(route.calls[0].request.url)


@respx.mock
def test_empty_guest_page_is_an_error() -> None:
    respx.get(GUEST_URL).mock(return_value=httpx.Response(200, text="<html>sign in</html>"))
    with pytest.raises(AdapterError, match="no jobs"):
        ADAPTER.fetch(company(id="linkedin", ats="linkedin", board="1337"), httpx.Client())


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get(GUEST_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(company(id="linkedin", ats="linkedin", board="1337"), httpx.Client())
    assert exc.value.retryable is True
