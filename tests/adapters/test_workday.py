from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.workday import WorkdayAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "workday"
ADAPTER = WorkdayAdapter()


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
            {
                "host": "nvidia.wd5.myworkdayjobs.com",
                "tenant": "nvidia",
                "site": "NVIDIAExternalCareerSite",
                "locale": "en-US",
            },
        ),
        (
            "https://adobe.wd5.myworkdayjobs.com/external_experienced",
            {
                "host": "adobe.wd5.myworkdayjobs.com",
                "tenant": "adobe",
                "site": "external_experienced",
            },
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
            {
                "host": "nvidia.wd5.myworkdayjobs.com",
                "tenant": "nvidia",
                "site": "NVIDIAExternalCareerSite",
            },
        ),
        (
            "https://wd1.myworkdaysite.com/recruiting/abinbev/USA",
            {
                "host": "wd1.myworkdaysite.com",
                "tenant": "abinbev",
                "site": "USA",
            },
        ),
        (
            "https://wd1.myworkdaysite.com/wday/cxs/abinbev/USA/jobs",
            {
                "host": "wd1.myworkdaysite.com",
                "tenant": "abinbev",
                "site": "USA",
            },
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Foo_JR1",
            {
                "host": "nvidia.wd5.myworkdayjobs.com",
                "tenant": "nvidia",
                "site": "NVIDIAExternalCareerSite",
                "locale": "en-US",
            },
        ),
        (
            "https://acme.wd103.myworkdayjobs.com/en-US/Careers",
            {
                "host": "acme.wd103.myworkdayjobs.com",
                "tenant": "acme",
                "site": "Careers",
                "locale": "en-US",
            },
        ),
    ],
)
def test_workday_url_variants(url: str, expected: dict[str, str]) -> None:
    ident = ADAPTER.parse_identity(url)
    for key, value in expected.items():
        assert ident[key] == value


def test_workday_shared_host_without_path_fails() -> None:
    with pytest.raises(AdapterError):
        ADAPTER.parse_identity("https://wd1.myworkdaysite.com/")


@respx.mock
def test_workday_pages_and_uses_total_from_first_page() -> None:
    def dispatch(request: httpx.Request) -> httpx.Response:
        body = request.content
        offset = 0
        if b'"offset": 20' in body or b'"offset":20' in body:
            offset = 20
        path = "page1.json" if offset else "page0.json"
        return httpx.Response(200, text=(FIXTURES / path).read_text())

    respx.post("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs").mock(
        side_effect=dispatch
    )
    c = company(
        ats="workday",
        careers_url="https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia",
        site="NVIDIAExternalCareerSite",
        locale="en-US",
    )
    jobs = ADAPTER.fetch(c, httpx.Client())
    assert len(jobs) == 22
    assert jobs[-1].title == "Intern, Systems"
    assert jobs[0].url.startswith(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/"
    )
    assert respx.calls.call_count == 2
    first_body = respx.calls[0].request.content
    assert b'"limit": 20' in first_body or b'"limit":20' in first_body


@respx.mock
def test_workday_stops_on_wrap() -> None:
    def dispatch(request: httpx.Request) -> httpx.Response:
        body = request.content
        wrap = b'"offset": 20' in body or b'"offset":20' in body
        path = "wrap.json" if wrap else "page0.json"
        return httpx.Response(200, text=(FIXTURES / path).read_text())

    respx.post("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs").mock(
        side_effect=dispatch
    )
    c = company(
        ats="workday",
        careers_url="https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia",
        site="NVIDIAExternalCareerSite",
        locale="en-US",
    )
    jobs = ADAPTER.fetch(c, httpx.Client())
    assert len(jobs) == 20
    assert respx.calls.call_count == 2


@respx.mock
def test_workday_warns_at_2000_cap() -> None:
    respx.post("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "cap.json").read_text())
    )
    c = company(
        ats="workday",
        careers_url="https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia",
        site="NVIDIAExternalCareerSite",
        locale="en-US",
        max_pages=1,
    )
    jobs = ADAPTER.fetch(c, httpx.Client())
    assert len(jobs) == 20
    assert c.extra.get("workday_truncated") is True
