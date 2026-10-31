from __future__ import annotations

from pathlib import Path

import pytest

from jobwatch.detect import detect_from_html, detect_from_url

CASES = [
    ("https://job-boards.greenhouse.io/discord", "greenhouse", "discord"),
    ("https://boards.greenhouse.io/discord", "greenhouse", "discord"),
    ("https://boards-api.greenhouse.io/v1/boards/discord/jobs", "greenhouse", "discord"),
    ("https://boards.greenhouse.io/embed/job_board?for=stripe", "greenhouse", "stripe"),
    ("https://jobs.lever.co/palantir", "lever", "palantir"),
    ("https://jobs.eu.lever.co/lever", "lever", "lever"),
    ("https://api.lever.co/v0/postings/zoox?mode=json", "lever", "zoox"),
    ("https://jobs.ashbyhq.com/openai", "ashby", "openai"),
    ("https://jobs.ashbyhq.com/openai/embed", "ashby", "openai"),
    ("https://api.ashbyhq.com/posting-api/job-board/ashby", "ashby", "ashby"),
    (
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        "workday",
        None,
    ),
    ("https://jobs.smartrecruiters.com/Visa", "smartrecruiters", "Visa"),
    ("https://careers.smartrecruiters.com/smartrecruiters", "smartrecruiters", "smartrecruiters"),
    ("https://api.smartrecruiters.com/v1/companies/acme/postings", "smartrecruiters", "acme"),
    ("https://careers-fhcrc.icims.com/jobs/search?ss=1", "icims", None),
    ("https://jobs-preview2test.icims.com/jobs/1709/customer-service/job", "icims", None),
]


@pytest.mark.parametrize("url,ats,board", CASES)
def test_detect_from_url(url: str, ats: str, board: str | None) -> None:
    found = detect_from_url(url)
    assert found is not None
    assert found.ats == ats
    if board:
        assert found.identity.get("board") == board
    if ats == "workday":
        assert found.identity["tenant"] == "nvidia"
        assert found.identity["site"] == "NVIDIAExternalCareerSite"
    if ats == "icims":
        assert "icims.com" in found.identity["portal"]


def test_detect_unknown_url() -> None:
    assert detect_from_url("https://careers.example.com/jobs") is None


def test_detect_greenhouse_embed_html() -> None:
    html = (Path(__file__).parent / "fixtures" / "detect" / "custom_greenhouse.html").read_text()
    found = detect_from_html("https://discord.com/careers", html)
    assert found is not None
    assert found.ats == "greenhouse"
    assert found.identity.get("board") == "discord"


def test_detect_html_each_ats() -> None:
    snippets = {
        "greenhouse": '<iframe src="https://job-boards.greenhouse.io/acme"></iframe>',
        "lever": '<a href="https://jobs.lever.co/acme/abc">job</a>',
        "ashby": '<script src="https://jobs.ashbyhq.com/acme/embed"></script>',
        "workday": '<a href="https://acme.wd5.myworkdayjobs.com/en-US/Careers">careers</a>',
        "smartrecruiters": '<a href="https://jobs.smartrecruiters.com/acme/1">job</a>',
        "icims": '<iframe src="https://careers-acme.icims.com/jobs/search?in_iframe=1"></iframe>',
    }
    for ats, html in snippets.items():
        found = detect_from_html("https://careers.example.com", html)
        assert found is not None, ats
        assert found.ats == ats
