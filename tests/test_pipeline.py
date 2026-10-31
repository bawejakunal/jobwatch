from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobwatch.config import filter_settings, load_config
from jobwatch.pipeline import scan_companies
from jobwatch.store import Store

YAML = """
defaults:
  include_title: ["new grad", "intern"]
  exclude_title: ["senior"]
  check_description: false
  rate_limit_rps: 100
  timeout_s: 5
companies:
  - id: acme
    name: Acme
    careers_url: https://job-boards.greenhouse.io/acme
    ats: greenhouse
    board: acme
  - id: broken
    name: Broken
    careers_url: https://job-boards.greenhouse.io/missing
    ats: greenhouse
    board: missing
"""


@respx.mock
def test_scan_isolates_errors_and_tracks_new(tmp_path: Path) -> None:
    fixture = (Path(__file__).parent / "fixtures" / "greenhouse" / "jobs.json").read_text()
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, text=fixture)
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/missing/jobs").mock(
        return_value=httpx.Response(404, text="nope")
    )

    yaml_path = tmp_path / "companies.yaml"
    yaml_path.write_text(YAML)
    config = load_config(yaml_path)
    store = Store(tmp_path / "db.sqlite")
    settings = filter_settings(config)
    results = scan_companies(config, store, settings=settings)
    by_id = {r.company.id: r for r in results}
    assert by_id["broken"].error
    assert by_id["acme"].error is None
    assert [j.title for j in by_id["acme"].jobs] == ["Software Engineer, New Grad"]
    assert len(by_id["acme"].new_jobs) == 1

    again = scan_companies(config, store, settings=settings)
    assert again[0].new_jobs == []
    assert len(again[0].jobs) == 1
    store.close()
