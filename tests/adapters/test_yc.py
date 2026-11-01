from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from jobwatch.adapters.base import AdapterError
from jobwatch.adapters.yc import YcAdapter, json_ld_description, within_max_age
from jobwatch.models import NormalizedJob
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "yc"
JOBS_URL = "https://www.ycombinator.com/companies/infer/jobs"


@respx.mock
def test_yc_keeps_recent_roles_and_maps_fields() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, text=(FIXTURES / "jobs.html").read_text()))
    jobs = YcAdapter().fetch(
        company(
            id="infer",
            name="Infer",
            ats="yc",
            board="infer",
            careers_url=JOBS_URL,
            scope="all",
            max_age_days=90,
        ),
        httpx.Client(),
    )
    by_id = {job.job_id: job for job in jobs}
    assert set(by_id) == {"111092", "111093", "111095"}
    founding = by_id["111092"]
    assert founding.title == "Founding Engineer"
    assert founding.url == "https://www.ycombinator.com/companies/infer/jobs/aaa-founding-engineer"
    assert founding.location == "San Francisco, CA, US"
    assert founding.posted_at == "18 days"
    assert founding.fingerprint == "111092"
    assert founding.department == "Engineering"
    assert founding.source == "yc"
    assert by_id["111093"].posted_at == "3 months"
    assert by_id["111095"].title == "Account Executive"


@respx.mock
def test_yc_shorter_window_drops_month_buckets() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, text=(FIXTURES / "jobs.html").read_text()))
    jobs = YcAdapter().fetch(
        company(id="infer", ats="yc", board="infer", careers_url=JOBS_URL, max_age_days=30),
        httpx.Client(),
    )
    assert [job.job_id for job in jobs] == ["111092"]


@respx.mock
def test_yc_empty_board_is_success() -> None:
    html = '<div id="app" data-page="{&quot;props&quot;:{&quot;jobPostings&quot;:[]}}"></div>'
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, text=html))
    jobs = YcAdapter().fetch(
        company(ats="yc", board="infer", careers_url=JOBS_URL),
        httpx.Client(),
    )
    assert jobs == []


@respx.mock
def test_yc_http_error() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(AdapterError, match="404"):
        YcAdapter().fetch(
            company(id="infer", ats="yc", board="infer", careers_url=JOBS_URL),
            httpx.Client(),
        )


@respx.mock
def test_yc_enrich_one_reads_json_ld() -> None:
    url = "https://www.ycombinator.com/companies/infer/jobs/aaa-founding-engineer"
    html = (
        "<html><script type=\"application/ld+json\">"
        '{"@context":"https://schema.org/","@type":"JobPosting",'
        '"description":"<p>We are hiring a <strong>software engineer</strong>.</p>"}'
        "</script></html>"
    )
    respx.get(url).mock(return_value=httpx.Response(200, text=html))
    job = NormalizedJob(
        company_id="infer",
        source="yc",
        job_id="1",
        title="APX",
        url=url,
    )
    text = YcAdapter().enrich_one(
        company(ats="yc", board="infer", careers_url=JOBS_URL),
        httpx.Client(),
        job,
    )
    assert text is not None
    assert "software engineer" in text.casefold()


@respx.mock
def test_yc_enrich_one_missing_json_ld_returns_none() -> None:
    url = "https://www.ycombinator.com/companies/infer/jobs/missing"
    respx.get(url).mock(return_value=httpx.Response(200, text="<html><p>no posting</p></html>"))
    job = NormalizedJob(company_id="infer", source="yc", job_id="1", title="APX", url=url)
    assert (
        YcAdapter().enrich_one(
            company(ats="yc", board="infer", careers_url=JOBS_URL),
            httpx.Client(),
            job,
        )
        is None
    )
    assert json_ld_description("<html></html>") is None


def test_yc_parse_identity() -> None:
    ident = YcAdapter().parse_identity("https://www.ycombinator.com/companies/infer/jobs?foo=1")
    assert ident["board"] == "infer"
    assert YcAdapter().matches_url("https://www.ycombinator.com/companies?isHiring=true") is False


@pytest.mark.parametrize(
    "phrase,kept",
    [
        ("18 days", True),
        ("90 days", True),
        ("91 days", False),
        ("about 1 month", True),
        ("about 2 months", True),
        ("2 months", True),
        ("3 months", True),
        ("4 months", False),
        ("12 months", False),
        ("over 1 year", False),
        ("almost 6 years", False),
        ("about 1 year", False),
        ("less than a minute", True),
        ("about 3 hours", True),
        ("", False),
        (None, False),
        ("posted recently", False),
    ],
)
def test_within_max_age(phrase: str | None, kept: bool) -> None:
    assert within_max_age(phrase, 90) is kept


def test_within_max_age_iso() -> None:
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert within_max_age("2026-08-15T00:00:00Z", 90, now=now) is True
    assert within_max_age("2026-05-09T00:06:16Z", 90, now=now) is False
