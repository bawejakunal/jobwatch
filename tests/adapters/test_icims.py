from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import NeedsBrowserError
from jobwatch.adapters.icims import IcimsAdapter, parse_search_html
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "icims"


def test_parse_search_html_cards_and_page_count() -> None:
    html = (FIXTURES / "page0.html").read_text()
    jobs, total = parse_search_html(
        html, portal="https://careers-acme.icims.com", company_id="acme"
    )
    assert total == 2
    assert [j.job_id for j in jobs] == ["32123", "32082"]
    assert jobs[0].title == "New Grad Software Engineer"
    assert jobs[0].location == "US-WA-Seattle"
    assert "32123" in jobs[0].url


@respx.mock
def test_icims_paginates_until_page_of() -> None:
    def dispatch(request: httpx.Request) -> httpx.Response:
        pr = request.url.params.get("pr") or "0"
        path = "page1.html" if pr == "1" else "page0.html"
        return httpx.Response(200, text=(FIXTURES / path).read_text())

    respx.get("https://careers-acme.icims.com/jobs/search").mock(side_effect=dispatch)
    jobs = IcimsAdapter().fetch(
        company(
            ats="icims",
            portal="https://careers-acme.icims.com",
            careers_url="https://careers-acme.icims.com/jobs/search?ss=1",
        ),
        httpx.Client(),
    )
    assert [j.job_id for j in jobs] == ["32123", "32082", "33001"]
    assert jobs[-1].title == "Internship, Data Analyst"
    assert respx.calls.call_count == 2
    first = respx.calls[0].request
    assert first.url.params["ss"] == "1"
    assert first.url.params["in_iframe"] == "1"


@respx.mock
def test_icims_405_needs_browser() -> None:
    respx.get("https://careers-acme.icims.com/jobs/search").mock(return_value=httpx.Response(405))
    with pytest.raises(NeedsBrowserError, match="405"):
        IcimsAdapter().fetch(
            company(
                ats="icims",
                portal="https://careers-acme.icims.com",
                careers_url="https://careers-acme.icims.com/jobs/search",
            ),
            httpx.Client(),
        )


@respx.mock
def test_icims_challenge_page_needs_browser() -> None:
    respx.get("https://careers-acme.icims.com/jobs/search").mock(
        return_value=httpx.Response(200, text="<html>Just a moment... cloudflare cf-challenge</html>")
    )
    with pytest.raises(NeedsBrowserError, match="challenge"):
        IcimsAdapter().fetch(
            company(
                ats="icims",
                portal="https://careers-acme.icims.com",
                careers_url="https://careers-acme.icims.com/jobs/search",
            ),
            httpx.Client(),
        )


def test_icims_parse_portal() -> None:
    ident = IcimsAdapter().parse_identity("https://careers-fhcrc.icims.com/jobs/search?ss=1")
    assert ident["portal"] == "https://careers-fhcrc.icims.com"
