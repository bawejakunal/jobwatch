from __future__ import annotations

import csv
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jobwatch.models import CompanyResult, NormalizedJob

console = Console()
err_console = Console(stderr=True)


def render_table(rows: list[tuple[NormalizedJob, bool]], *, title: str) -> None:
    table = Table(title=title, show_lines=False, expand=True)
    table.add_column("New", style="bold green", width=4)
    table.add_column("Company", style="cyan", no_wrap=True)
    table.add_column("Title", overflow="fold")
    table.add_column("Location", overflow="fold")
    table.add_column("URL", overflow="fold")
    if not rows:
        console.print(table)
        console.print("[dim]No matching postings.[/dim]")
        return
    for job, is_new in rows:
        table.add_row(
            "NEW" if is_new else "",
            job.company_id,
            job.title,
            job.location or "",
            job.url,
        )
    console.print(table)


def rows_from_results(results: list[CompanyResult], *, show_all: bool) -> list[tuple[NormalizedJob, bool]]:
    rows: list[tuple[NormalizedJob, bool]] = []
    new_ids = {(job.source, job.job_id, job.company_id) for r in results for job in r.new_jobs}
    for result in results:
        jobs = result.jobs if show_all else result.new_jobs
        for job in jobs:
            is_new = (job.source, job.job_id, job.company_id) in new_ids
            rows.append((job, is_new))
    return rows


def write_json(path: Path | None, rows: list[tuple[NormalizedJob, bool]]) -> str:
    payload = [
        {
            "new": is_new,
            "company_id": job.company_id,
            "source": job.source,
            "job_id": job.job_id,
            "title": job.title,
            "url": job.url,
            "location": job.location,
            "department": job.department,
            "workplace": job.workplace,
            "posted_at": job.posted_at,
        }
        for job, is_new in rows
    ]
    text = json.dumps(payload, indent=2)
    if path:
        path.write_text(text + "\n")
    return text


def write_csv(path: Path, rows: list[tuple[NormalizedJob, bool]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "new",
                "company_id",
                "source",
                "job_id",
                "title",
                "url",
                "location",
                "department",
                "workplace",
                "posted_at",
            ],
        )
        writer.writeheader()
        for job, is_new in rows:
            writer.writerow(
                {
                    "new": "yes" if is_new else "no",
                    "company_id": job.company_id,
                    "source": job.source,
                    "job_id": job.job_id,
                    "title": job.title,
                    "url": job.url,
                    "location": job.location or "",
                    "department": job.department or "",
                    "workplace": job.workplace or "",
                    "posted_at": job.posted_at or "",
                }
            )
