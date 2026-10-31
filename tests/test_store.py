from __future__ import annotations

from pathlib import Path

from jobwatch.models import NormalizedJob
from jobwatch.store import Store


def _job(job_id: str, title: str = "New Grad Engineer") -> NormalizedJob:
    return NormalizedJob(
        company_id="acme",
        source="greenhouse",
        job_id=job_id,
        title=title,
        url=f"https://example.com/{job_id}",
        location="Remote",
    )


def test_first_seen_and_closed(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    new = store.upsert_jobs(
        "acme",
        "greenhouse",
        [_job("1"), _job("2")],
        seen_at="2026-01-01T00:00:00+00:00",
    )
    assert {j.job_id for j in new} == {"1", "2"}

    new = store.upsert_jobs(
        "acme",
        "greenhouse",
        [_job("1"), _job("3")],
        seen_at="2026-01-02T00:00:00+00:00",
    )
    assert {j.job_id for j in new} == {"3"}

    closed = store.conn.execute(
        "SELECT job_id, closed_at FROM jobs WHERE closed_at IS NOT NULL"
    ).fetchall()
    assert [row["job_id"] for row in closed] == ["2"]

    store.upsert_jobs(
        "acme",
        "greenhouse",
        [_job("2")],
        seen_at="2026-01-03T00:00:00+00:00",
    )
    row = store.conn.execute("SELECT closed_at FROM jobs WHERE job_id='2'").fetchone()
    assert row["closed_at"] is None
    store.close()
