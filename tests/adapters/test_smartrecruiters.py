from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobwatch.adapters.smartrecruiters import SmartRecruitersAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "smartrecruiters"


@respx.mock
def test_smartrecruiters_pages_by_offset() -> None:
    def dispatch(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset") or 0)
        path = "page1.json" if offset else "page0.json"
        return httpx.Response(200, text=(FIXTURES / path).read_text())

    respx.get("https://api.smartrecruiters.com/v1/companies/acme/postings").mock(side_effect=dispatch)
    jobs = SmartRecruitersAdapter().fetch(
        company(
            ats="smartrecruiters",
            board="acme",
            careers_url="https://jobs.smartrecruiters.com/acme",
        ),
        httpx.Client(),
    )
    assert [j.job_id for j in jobs] == ["744000158320111", "744000158320222"]
    assert jobs[0].title == "University Graduate Engineer"
    assert jobs[0].url == "https://jobs.smartrecruiters.com/acme/744000158320111"
    assert jobs[0].location == "San Francisco, CA, US"
    assert jobs[1].location == "New York, NY, US (Remote)"
    assert respx.calls.call_count == 2
    assert respx.calls[0].request.url.params["destination"] == "PUBLIC"
    assert respx.calls[0].request.url.params["limit"] == "100"


def test_smartrecruiters_parse() -> None:
    ident = SmartRecruitersAdapter().parse_identity(
        "https://careers.smartrecruiters.com/Visa/jobs"
    )
    assert ident["board"] == "Visa"
