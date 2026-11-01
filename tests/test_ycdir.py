from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from jobwatch.config import load_config
from jobwatch.models import Company
from jobwatch.ycdir import DIRECTORY_URL, fetch_us_hiring_companies, merge_yc_companies
from tests.conftest import company

ROOT = Path(__file__).resolve().parents[1]


def test_merge_replaces_yc_and_skips_other_ats() -> None:
    existing = [
        company(
            id="sierra",
            name="Sierra",
            ats="ashby",
            board="Sierra",
            careers_url="https://sierra.ai/careers",
        ),
        company(
            id="stale",
            name="Stale",
            ats="yc",
            board="stale",
            careers_url="https://www.ycombinator.com/companies/stale/jobs",
            scope="all",
            max_age_days=90,
        ),
        company(
            id="harvey",
            name="Harvey",
            ats="ashby",
            board="harvey",
            careers_url="https://www.harvey.ai/careers",
        ),
    ]
    hits = [("sierra", "Sierra"), ("infer", "Infer"), ("harvey", "Harvey"), ("zeta", "Zeta")]
    merged, skipped = merge_yc_companies(existing, hits)
    assert skipped == 2
    assert [item.id for item in merged] == ["sierra", "harvey", "infer", "zeta"]
    assert merged[0].ats == "ashby"
    assert merged[1].ats == "ashby"
    infer = merged[2]
    assert infer.ats == "yc"
    assert infer.scope == "all"
    assert infer.max_age_days == 90
    assert infer.board == "infer"
    assert infer.careers_url == "https://www.ycombinator.com/companies/infer/jobs"


@respx.mock
def test_fetch_directory_pages_and_reads_key_from_html() -> None:
    html = (
        '<html><script>window.AlgoliaOpts = '
        '{"app":"TESTAPP","key":"secret-key","valid_until":1};</script></html>'
    )
    respx.get(DIRECTORY_URL).mock(return_value=httpx.Response(200, text=html))

    def _page(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Algolia-API-Key"] == "secret-key"
        assert request.headers["X-Algolia-Application-Id"] == "TESTAPP"
        body = json.loads(request.content)
        assert body["filters"] == 'isHiring:true AND regions:"United States of America"'
        page = body["page"]
        if page == 0:
            hits = [{"slug": "infer", "name": "Infer"}]
            return httpx.Response(200, json={"hits": hits, "nbHits": 2})
        if page == 1:
            return httpx.Response(200, json={"hits": [{"slug": "zeta", "name": "Zeta"}], "nbHits": 2})
        raise AssertionError(page)

    respx.post("https://testapp-dsn.algolia.net/1/indexes/YCCompany_production/query").mock(
        side_effect=_page
    )
    found = fetch_us_hiring_companies(httpx.Client(), hits_per_page=1)
    assert found == [("infer", "Infer"), ("zeta", "Zeta")]


@respx.mock
def test_fetch_directory_splits_when_the_first_thousand_are_capped() -> None:
    html = (
        '<html><script>window.AlgoliaOpts = '
        '{"app":"TESTAPP","key":"secret-key","valid_until":1};</script></html>'
    )
    respx.get(DIRECTORY_URL).mock(return_value=httpx.Response(200, text=html))

    def _page(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        filters = body["filters"]
        if filters == 'isHiring:true AND regions:"United States of America"':
            if body["page"] == 0:
                return httpx.Response(200, json={"hits": [{"slug": "only", "name": "Only"}], "nbHits": 2})
            return httpx.Response(200, json={"hits": [], "nbHits": 2})
        if filters.endswith('AND regions:"Remote"'):
            return httpx.Response(200, json={"hits": [{"slug": "remote-co", "name": "Remote Co"}], "nbHits": 1})
        if 'NOT regions:"Remote"' in filters:
            return httpx.Response(200, json={"hits": [{"slug": "onsite-co", "name": "Onsite Co"}], "nbHits": 1})
        raise AssertionError(filters)

    respx.post("https://testapp-dsn.algolia.net/1/indexes/YCCompany_production/query").mock(
        side_effect=_page
    )
    found = fetch_us_hiring_companies(httpx.Client(), hits_per_page=1)
    assert found == [("remote-co", "Remote Co"), ("onsite-co", "Onsite Co")]


def test_shipped_example_is_sierra_harvey_and_yc() -> None:
    root = ROOT / "companies.example.yaml"
    bundled = ROOT / "src" / "jobwatch" / "data" / "companies.example.yaml"
    assert root.read_text() == bundled.read_text()
    cfg = load_config(root)
    assert [(c.id, c.ats) for c in cfg.companies[:2]] == [("sierra", "ashby"), ("harvey", "ashby")]
    yc = [c for c in cfg.companies if c.ats == "yc"]
    assert len(yc) > 1000
    assert len({c.id for c in cfg.companies}) == len(cfg.companies)
    for company in yc:
        assert company.scope == "all"
        assert company.max_age_days == 90
        assert company.board
        assert company.careers_url == f"https://www.ycombinator.com/companies/{company.board}/jobs"


def test_merge_types() -> None:
    kept, skipped = merge_yc_companies([], [("acme", "Acme")])
    assert skipped == 0
    assert isinstance(kept[0], Company)
