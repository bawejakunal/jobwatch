from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.eightfold import PAGE_SIZE, EightfoldAdapter
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eightfold"
ADAPTER = EightfoldAdapter()
MS_HOST = "apply.careers.microsoft.com"
NF_HOST = "explore.jobs.netflix.net"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_identity_known_hosts() -> None:
    microsoft = ADAPTER.parse_identity(
        "https://apply.careers.microsoft.com/careers?domain=microsoft.com"
    )
    assert microsoft == {"ats": "eightfold", "host": MS_HOST, "board": "microsoft.com"}
    netflix = ADAPTER.parse_identity("https://explore.jobs.netflix.net/careers")
    assert netflix == {"ats": "eightfold", "host": NF_HOST, "board": "netflix.com"}
    generic = ADAPTER.parse_identity("https://acme.eightfold.ai/careers?domain=acme.com")
    assert generic["board"] == "acme.com"
    assert ADAPTER.matches_url("https://acme.eightfold.ai/careers")
    assert not ADAPTER.matches_url("https://jobs.lever.co/spotify")


def test_sniff_html() -> None:
    found = ADAPTER.sniff_html(
        "https://explore.jobs.netflix.net/careers",
        '<script src="https://static.eightfold.ai/pcsx.js"></script>',
    )
    assert found is not None
    assert found["board"] == "netflix.com"
    assert ADAPTER.sniff_html("https://example.com/jobs", "<html>no board</html>") is None


@respx.mock
def test_pcsx_maps_title_location_and_posted_time() -> None:
    respx.get(f"https://{MS_HOST}/api/pcsx/search").mock(
        return_value=httpx.Response(200, json=_load("pcsx_search.json"))
    )
    jobs = ADAPTER.fetch(
        company(
            id="microsoft",
            ats="eightfold",
            host=MS_HOST,
            board="microsoft.com",
            careers_url="https://apply.careers.microsoft.com/careers?domain=microsoft.com",
        ),
        httpx.Client(),
    )
    assert [job.title for job in jobs] == [
        "Software Engineer: Security & Identity Intern Opportunities for University Students, Redmond",
        "Program Manager",
    ]
    assert jobs[0].location == "Redmond, WA, US"
    assert jobs[0].url == f"https://{MS_HOST}/careers/job/1970393556922930"
    assert jobs[0].posted_at == "2026-11-01T07:00:00Z"
    assert jobs[0].department == "Software Engineering"
    assert jobs[0].description is None
    assert jobs[0].places[0].country == "US"


@respx.mock
def test_pcsx_403_falls_back_to_v2_and_keeps_description_when_requested() -> None:
    respx.get(f"https://{NF_HOST}/api/pcsx/search").mock(return_value=httpx.Response(403))
    respx.get(f"https://{NF_HOST}/api/apply/v2/jobs").mock(
        return_value=httpx.Response(200, json=_load("v2_jobs.json"))
    )
    target = company(
        id="netflix",
        ats="eightfold",
        host=NF_HOST,
        board="netflix.com",
        careers_url="https://explore.jobs.netflix.net/careers",
    )
    titles_only = ADAPTER.fetch(target, httpx.Client())
    assert titles_only[0].description is None
    assert titles_only[0].location == "Los Gatos, California"
    assert titles_only[0].url == "https://explore.jobs.netflix.net/careers/job/790298014263"

    with_body = ADAPTER.fetch(target, httpx.Client(), include_content=True)
    assert with_body[0].description == "Build tooling for ads."
    assert target.extra["_eightfold"]["style"] == "v2"


@respx.mock
def test_pagination_stops_at_count_and_warns_when_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.adapters.eightfold.PAGE_SIZE", 2)
    page0 = {
        "data": {
            "count": 3,
            "positions": [
                {"id": 1, "name": "One", "positionUrl": "/careers/job/1", "locations": ["Austin, TX"]},
                {"id": 2, "name": "Two", "positionUrl": "/careers/job/2", "locations": ["Austin, TX"]},
            ],
        }
    }
    page1 = {
        "data": {
            "count": 3,
            "positions": [
                {"id": 3, "name": "Three", "positionUrl": "/careers/job/3", "locations": ["Austin, TX"]},
            ],
        }
    }
    route = respx.get(f"https://{MS_HOST}/api/pcsx/search")
    route.side_effect = [
        httpx.Response(200, json=page0),
        httpx.Response(200, json=page1),
    ]
    target = company(id="microsoft", ats="eightfold", host=MS_HOST, board="microsoft.com")
    jobs = ADAPTER.fetch(target, httpx.Client())
    assert [job.job_id for job in jobs] == ["1", "2", "3"]
    assert "eightfold_truncated" not in target.extra

    capped = company(
        id="microsoft",
        ats="eightfold",
        host=MS_HOST,
        board="microsoft.com",
        max_pages=1,
    )
    route.side_effect = [httpx.Response(200, json=page0)]
    jobs = ADAPTER.fetch(capped, httpx.Client())
    assert [job.job_id for job in jobs] == ["1", "2"]
    assert capped.extra["eightfold_truncated"] is True
    assert PAGE_SIZE == 10


@respx.mock
def test_429_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobwatch.http.time.sleep", lambda *_args, **_kwargs: None)
    respx.get(f"https://{MS_HOST}/api/pcsx/search").mock(return_value=httpx.Response(429))
    with pytest.raises(AdapterError) as exc:
        ADAPTER.fetch(
            company(id="microsoft", ats="eightfold", host=MS_HOST, board="microsoft.com"),
            httpx.Client(),
        )
    assert exc.value.retryable is True


@respx.mock
def test_enrich_one_reads_position_details() -> None:
    respx.get(f"https://{MS_HOST}/api/pcsx/position_details").mock(
        return_value=httpx.Response(200, json=_load("position_details.json"))
    )
    from jobwatch.models import NormalizedJob

    job = NormalizedJob(
        company_id="microsoft",
        source="eightfold",
        job_id="1970393556922930",
        title="Intern",
        url="https://apply.careers.microsoft.com/careers/job/1970393556922930",
        detail_ref="1970393556922930",
    )
    body = ADAPTER.enrich_one(
        company(id="microsoft", ats="eightfold", host=MS_HOST, board="microsoft.com"),
        httpx.Client(),
        job,
    )
    assert body == "University intern on the identity team."
