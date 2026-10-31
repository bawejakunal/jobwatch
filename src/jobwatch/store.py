from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jobwatch.models import NormalizedJob


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              company_id  TEXT NOT NULL,
              source      TEXT NOT NULL,
              job_id      TEXT NOT NULL,
              title       TEXT NOT NULL,
              url         TEXT NOT NULL,
              location    TEXT,
              department  TEXT,
              first_seen  TEXT NOT NULL,
              last_seen   TEXT NOT NULL,
              closed_at   TEXT,
              PRIMARY KEY (company_id, source, job_id)
            );
            CREATE TABLE IF NOT EXISTS scans (
              id          INTEGER PRIMARY KEY,
              started_at  TEXT NOT NULL,
              finished_at TEXT,
              companies   INTEGER,
              fetched     INTEGER,
              new_count   INTEGER,
              errors      TEXT
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def start_scan(self) -> tuple[int, str]:
        started = utcnow()
        cur = self.conn.execute("INSERT INTO scans (started_at) VALUES (?)", (started,))
        self.conn.commit()
        return int(cur.lastrowid), started

    def finish_scan(
        self,
        scan_id: int,
        *,
        companies: int,
        fetched: int,
        new_count: int,
        errors: list[dict[str, str]],
    ) -> None:
        self.conn.execute(
            """
            UPDATE scans
            SET finished_at = ?, companies = ?, fetched = ?, new_count = ?, errors = ?
            WHERE id = ?
            """,
            (utcnow(), companies, fetched, new_count, json.dumps(errors), scan_id),
        )
        self.conn.commit()

    def upsert_jobs(
        self,
        company_id: str,
        source: str,
        jobs: list[NormalizedJob],
        *,
        seen_at: str,
        mark_missing_closed: bool = True,
    ) -> list[NormalizedJob]:
        """Return jobs whose first_seen was set on this call (brand new)."""
        new_jobs: list[NormalizedJob] = []
        current_ids = {job.job_id for job in jobs}
        for job in jobs:
            row = self.conn.execute(
                "SELECT first_seen, closed_at FROM jobs WHERE company_id=? AND source=? AND job_id=?",
                (company_id, source, job.job_id),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO jobs (
                      company_id, source, job_id, title, url, location, department,
                      first_seen, last_seen, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        company_id,
                        source,
                        job.job_id,
                        job.title,
                        job.url,
                        job.location,
                        job.department,
                        seen_at,
                        seen_at,
                    ),
                )
                new_jobs.append(job)
            else:
                self.conn.execute(
                    """
                    UPDATE jobs
                    SET title=?, url=?, location=?, department=?, last_seen=?, closed_at=NULL
                    WHERE company_id=? AND source=? AND job_id=?
                    """,
                    (
                        job.title,
                        job.url,
                        job.location,
                        job.department,
                        seen_at,
                        company_id,
                        source,
                        job.job_id,
                    ),
                )
        if mark_missing_closed and current_ids:
            placeholders = ",".join("?" for _ in current_ids)
            self.conn.execute(
                f"""
                UPDATE jobs
                SET closed_at = COALESCE(closed_at, ?)
                WHERE company_id = ? AND source = ?
                  AND closed_at IS NULL
                  AND job_id NOT IN ({placeholders})
                """,
                (seen_at, company_id, source, *current_ids),
            )
        elif mark_missing_closed and not current_ids:
            self.conn.execute(
                """
                UPDATE jobs
                SET closed_at = COALESCE(closed_at, ?)
                WHERE company_id = ? AND source = ? AND closed_at IS NULL
                """,
                (seen_at, company_id, source),
            )
        self.conn.commit()
        return new_jobs
