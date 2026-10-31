from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobwatch.adapters.lever import LeverAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lever"


@respx.mock
def test_lever_maps_fields_and_pages() -> None:
    page1 = (FIXTURES / "postings.json").read_text()
    empty = (FIXTURES / "empty.json").read_text()

    def dispatch(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("skip") or 0)
        return httpx.Response(200, text=empty if skip else page1)

    respx.get("https://api.lever.co/v0/postings/acme").mock(side_effect=dispatch)
    jobs = LeverAdapter().fetch(
        company(
            ats="lever",
            board="acme",
            region="global",
            careers_url="https://jobs.lever.co/acme",
        ),
        httpx.Client(),
    )
    assert len(jobs) == 2
    assert jobs[0].title == "University Graduate, Software Engineer"
    assert jobs[0].department == "Engineering"
    assert jobs[0].workplace == "hybrid"
    assert jobs[0].posted_at is not None
    assert respx.calls.call_count == 1  # short page, no second request


@respx.mock
def test_lever_empty_global_falls_back_to_eu() -> None:
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, text="[]")
    )
    respx.get("https://api.eu.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "postings.json").read_text())
    )
    c = company(ats="lever", board="acme", careers_url="https://jobs.lever.co/acme")
    jobs = LeverAdapter().fetch(c, httpx.Client())
    assert len(jobs) == 2
    assert c.region == "eu"


def test_lever_eu_url() -> None:
    ident = LeverAdapter().parse_identity("https://jobs.eu.lever.co/lever/abc")
    assert ident == {"board": "lever", "region": "eu", "ats": "lever"}
