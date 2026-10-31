from __future__ import annotations

from jobwatch.adapters.ashby import _map as map_ashby
from jobwatch.adapters.smartrecruiters import _map as map_sr
from jobwatch.config import filter_settings, load_config
from jobwatch.locations import (
    LocationSettings,
    apply_location_filter,
    classify_text,
    format_locations,
    location_keeps,
    normalize_country,
)
from jobwatch.models import NormalizedJob
from tests.conftest import company


def _job(*, title: str = "Software Engineer, New Grad", location: str | None = None, places=None) -> NormalizedJob:
    return NormalizedJob(
        company_id="acme",
        source="ashby",
        job_id="1",
        title=title,
        url="https://example.com/1",
        location=location,
        places=places or [],
    )


def test_normalize_country_aliases() -> None:
    assert normalize_country("US") == "US"
    assert normalize_country("USA") == "US"
    assert normalize_country("United States") == "US"
    assert normalize_country("United States of America") == "US"
    assert normalize_country("GB") == "GB"
    assert normalize_country("United Kingdom") == "GB"
    assert normalize_country("Singapore") == "SG"


def test_classify_us_text() -> None:
    assert classify_text("Remote - US").country == "US"
    assert classify_text("Remote, United States").country == "US"
    assert classify_text("United States").country == "US"
    assert classify_text("San Francisco, CA").country == "US"
    assert classify_text("New York, NY").country == "US"
    assert classify_text("Seattle").country == "US"
    assert classify_text("Austin, Texas").country == "US"
    assert classify_text("US, CA, Santa Clara").country == "US"
    assert classify_text("US-WA-Seattle").country == "US"
    assert classify_text("Bay Area").country == "US"
    assert not classify_text("Remote - US").ambiguous


def test_classify_foreign_and_remote() -> None:
    assert classify_text("Singapore").country == "SG"
    assert classify_text("London").country == "GB"
    assert classify_text("London, UK").country == "GB"
    assert classify_text("Toronto, Canada").country == "CA"
    assert classify_text("Remote - EMEA").country == "EU"
    remote = classify_text("Remote")
    assert remote.remote is True
    assert remote.ambiguous is True
    assert remote.country is None
    worldwide = classify_text("Remote - Worldwide")
    assert worldwide.ambiguous is True


def test_structured_country_wins() -> None:
    place = classify_text("Headquarters", structured_country="United States")
    assert place.country == "US"
    sg = classify_text("Office", structured_country="SG")
    assert sg.country == "SG"


def test_keep_if_any_location_is_us() -> None:
    settings = LocationSettings(mode="include", countries=["US"])
    us_only = [classify_text("San Francisco, CA")]
    mixed = [classify_text("San Francisco, CA"), classify_text("Singapore")]
    foreign = [classify_text("Singapore"), classify_text("London")]
    remote = [classify_text("Remote")]
    assert location_keeps(us_only, settings) == (True, False)
    assert location_keeps(mixed, settings) == (True, False)
    assert location_keeps(foreign, settings) == (False, False)
    assert location_keeps(remote, settings) == (True, True)
    assert location_keeps([], settings) == (True, True)


def test_display_us_locations_and_marks_others() -> None:
    settings = LocationSettings()
    places = [classify_text("San Francisco, CA"), classify_text("New York, NY"), classify_text("Singapore")]
    display, ambiguous = format_locations(places, settings)
    assert "San Francisco, CA" in display
    assert "New York, NY" in display
    assert "+ Singapore" in display
    assert ambiguous is False
    remote_display, remote_amb = format_locations([classify_text("Remote")], settings)
    assert "unspecified" in remote_display.lower()
    assert remote_amb is True


def test_apply_filter_rewrites_location() -> None:
    settings = LocationSettings()
    jobs = apply_location_filter(
        [
            _job(location="Singapore", places=[classify_text("Singapore")]),
            _job(
                location="San Francisco, CA, Singapore",
                places=[classify_text("San Francisco, CA"), classify_text("Singapore")],
            ),
            _job(location="Remote", places=[classify_text("Remote")]),
            _job(location="London", places=[classify_text("London")]),
        ],
        settings,
    )
    titles_locs = [(j.location, j.location_ambiguous) for j in jobs]
    assert ("Singapore", False) not in titles_locs
    assert any(j.location and "San Francisco, CA" in j.location and "+ Singapore" in j.location for j in jobs)
    assert any(j.location_ambiguous for j in jobs)
    assert not any(j.location == "London" for j in jobs)


def test_exclude_mode_and_any_location() -> None:
    exclude = LocationSettings(mode="exclude", countries=["US"])
    jobs = apply_location_filter(
        [
            _job(location="San Francisco, CA", places=[classify_text("San Francisco, CA")]),
            _job(location="Singapore", places=[classify_text("Singapore")]),
        ],
        exclude,
    )
    assert [j.location for j in jobs] == ["Singapore"]

    disabled = LocationSettings(enabled=False, countries=["US"])
    kept = apply_location_filter(
        [_job(location="London", places=[classify_text("London")])],
        disabled,
    )
    assert len(kept) == 1


def test_config_locations_default_and_override(tmp_path) -> None:
    path = tmp_path / "companies.yaml"
    path.write_text(
        """
defaults:
  include_title: [intern]
  exclude_title: []
  locations:
    mode: include
    countries: [US]
companies:
  - id: acme
    name: Acme
    careers_url: https://example.com
"""
    )
    config = load_config(path)
    settings = filter_settings(config)
    assert settings.locations.countries == ["US"]
    assert settings.locations.mode == "include"
    assert settings.locations.enabled is True

    any_loc = filter_settings(config, any_location=True)
    assert any_loc.locations.enabled is False


def test_ashby_structured_country_and_secondary() -> None:
    c = company(id="sierra", ats="ashby", board="Sierra")
    us = map_ashby(
        c,
        {
            "id": "sf",
            "title": "Software Engineer, Agent (New Grad 2027)",
            "jobUrl": "https://jobs.ashbyhq.com/Sierra/sf",
            "location": "San Francisco, CA",
            "address": {"postalAddress": {"addressCountry": "United States"}},
            "secondaryLocations": [
                {
                    "location": "New York, NY",
                    "address": {"postalAddress": {"addressCountry": "USA"}},
                }
            ],
        },
    )
    assert us is not None
    assert {p.country for p in us.places} == {"US"}

    sg = map_ashby(
        c,
        {
            "id": "sg",
            "title": "Software Engineer, Agent (New Grad 2027)",
            "jobUrl": "https://jobs.ashbyhq.com/Sierra/sg",
            "location": "Singapore",
            "address": {"postalAddress": {"addressCountry": "Singapore"}},
        },
    )
    assert sg is not None
    assert sg.places[0].country == "SG"
    kept = apply_location_filter([us, sg], LocationSettings())
    assert [j.job_id for j in kept] == ["sf"]
    assert "Singapore" not in (kept[0].location or "")
    assert "San Francisco, CA" in (kept[0].location or "")
    assert "New York, NY" in (kept[0].location or "")


def test_smartrecruiters_country_field() -> None:
    c = company(id="acme", ats="smartrecruiters", board="acme")
    job = map_sr(
        c,
        {
            "id": "99",
            "name": "New Grad Engineer",
            "releasedDate": "2026-01-01",
            "location": {"city": "London", "country": "GB", "remote": False},
        },
        "acme",
    )
    assert job is not None
    assert job.places[0].country == "GB"
    assert apply_location_filter([job], LocationSettings()) == []
