from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobwatch.adapters.icims import IcimsAdapter
from jobwatch.adapters.smartrecruiters import SmartRecruitersAdapter
from jobwatch.adapters.workday import WorkdayAdapter
from jobwatch.config import filter_settings, load_config
from jobwatch.models import NormalizedJob
from jobwatch.pipeline import enrich_jobs, scan_companies
from jobwatch.store import Store
from tests.conftest import company

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@respx.mock
def test_workday_enrich_one() -> None:
    respx.get(
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA/Software-Engineer_JR0001"
    ).mock(return_value=httpx.Response(200, text=(FIXTURES / "workday" / "detail.json").read_text()))
    c = company(
        ats="workday",
        host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia",
        site="NVIDIAExternalCareerSite",
        careers_url="https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
    )
    job = NormalizedJob(
        company_id="acme",
        source="workday",
        job_id="JR0001",
        title="Software Engineer",
        url="https://example.com",
        detail_ref="/job/US-CA/Software-Engineer_JR0001",
    )
    text = WorkdayAdapter().enrich_one(c, httpx.Client(), job)
    assert text is not None
    assert "new graduates" in text.lower()
    assert "2027" in text


@respx.mock
def test_smartrecruiters_enrich_one() -> None:
    respx.get("https://api.smartrecruiters.com/v1/companies/acme/postings/744000158320111").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "smartrecruiters" / "detail.json").read_text())
    )
    c = company(ats="smartrecruiters", board="acme", careers_url="https://jobs.smartrecruiters.com/acme")
    job = NormalizedJob(
        company_id="acme",
        source="smartrecruiters",
        job_id="744000158320111",
        title="University Graduate Engineer",
        url="https://jobs.smartrecruiters.com/acme/744000158320111",
    )
    text = SmartRecruitersAdapter().enrich_one(c, httpx.Client(), job)
    assert text is not None
    assert "new graduate" in text.lower()


@respx.mock
def test_icims_enrich_one() -> None:
    respx.get("https://careers-acme.icims.com/jobs/32123/job").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "icims" / "job.html").read_text())
    )
    c = company(
        ats="icims",
        portal="https://careers-acme.icims.com",
        careers_url="https://careers-acme.icims.com/jobs/search",
    )
    job = NormalizedJob(
        company_id="acme",
        source="icims",
        job_id="32123",
        title="New Grad Software Engineer",
        url="https://careers-acme.icims.com/jobs/32123/job",
    )
    text = IcimsAdapter().enrich_one(c, httpx.Client(), job)
    assert text is not None
    assert "2027" in text


@respx.mock
def test_enrich_uses_cache_and_skips_refetch(tmp_path: Path) -> None:
    from jobwatch.adapters.workday import WorkdayAdapter

    detail = respx.get(
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA/Software-Engineer_JR0001"
    ).mock(return_value=httpx.Response(200, text=(FIXTURES / "workday" / "detail.json").read_text()))
    c = company(
        ats="workday",
        host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia",
        site="NVIDIAExternalCareerSite",
        careers_url="https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
    )
    store = Store(tmp_path / "db.sqlite")
    job = NormalizedJob(
        company_id="acme",
        source="workday",
        job_id="JR0001",
        title="University Graduate",
        url="https://example.com",
        fingerprint="v1",
        detail_ref="/job/US-CA/Software-Engineer_JR0001",
    )
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text("defaults: {check_description: true}\ncompanies: []\n")
    settings = filter_settings(load_config(yaml_path))
    settings.check_description = True
    adapter = WorkdayAdapter()
    cached, fetched, truncated = enrich_jobs(adapter, c, httpx.Client(), [job], store, settings)
    assert fetched == 1 and cached == 0 and not truncated
    store.upsert_jobs("acme", "workday", [job], seen_at="2026-01-01T00:00:00+00:00", mark_missing_closed=False)

    job2 = NormalizedJob(
        company_id="acme",
        source="workday",
        job_id="JR0001",
        title="University Graduate",
        url="https://example.com",
        fingerprint="v1",
        detail_ref="/job/US-CA/Software-Engineer_JR0001",
    )
    cached, fetched, truncated = enrich_jobs(adapter, c, httpx.Client(), [job2], store, settings)
    assert cached == 1 and fetched == 0
    assert job2.description and "2027" in job2.description
    assert detail.call_count == 1
    store.close()
