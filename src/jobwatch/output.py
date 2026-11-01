from __future__ import annotations

import csv
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jobwatch.models import CompanyResult, NormalizedJob


def _location_cell(job: NormalizedJob) -> str:
    text = job.location or ""
    if job.location_ambiguous and text and "unspecified" not in text.lower():
        return f"{text} [?]"
    return text

console = Console()
err_console = Console(stderr=True)


def render_table(rows: list[tuple[NormalizedJob, bool]], *, title: str) -> None:
    table = Table(title=title, show_lines=False, expand=True)
    table.add_column("New", style="bold green", width=4)
    table.add_column("2027", style="bold magenta", width=4)
    table.add_column("Company", style="cyan", no_wrap=True)
    table.add_column("Title", overflow="fold")
    table.add_column("Location", overflow="fold")
    table.add_column("Via", width=11)
    table.add_column("URL", overflow="fold")
    if not rows:
        console.print(table)
        console.print("[dim]No matching postings.[/dim]")
        return
    for job, is_new in rows:
        table.add_row(
            "NEW" if is_new else "",
            "2027" if job.year_match else "",
            job.company_id,
            job.title,
            _location_cell(job),
            job.match_on or "",
            job.url,
        )
    console.print(table)


def company_progress_lines(
    index: int,
    total: int,
    result: CompanyResult,
    *,
    show_all: bool,
) -> list[str]:
    """Plain progress text for one finished company. Index is 1-based."""
    name = result.company.display_name()
    shown = [] if result.error else (result.jobs if show_all else result.new_jobs)
    if result.error:
        status = "failed"
    else:
        label = "open" if show_all else "new"
        status = f"{len(shown)} {label}"
    lines = [f"[{index}/{total}] {name}  {status}"]
    for job in shown:
        location = _location_cell(job)
        parts = [job.title]
        if location:
            parts.append(location)
        parts.append(job.url)
        lines.append("  " + "  ".join(parts))
    if result.warning:
        lines.append(f"warning: {result.warning}")
    return lines


def print_company_progress(
    index: int,
    total: int,
    result: CompanyResult,
    *,
    show_all: bool,
) -> None:
    for line in company_progress_lines(index, total, result, show_all=show_all):
        if line.startswith("warning:"):
            err_console.print(f"[yellow]{result.company.id}:[/yellow] {result.warning}")
        else:
            err_console.print(line, highlight=False, markup=False)


def rows_from_results(results: list[CompanyResult], *, show_all: bool) -> list[tuple[NormalizedJob, bool]]:
    rows: list[tuple[NormalizedJob, bool]] = []
    new_ids = {(job.source, job.job_id, job.company_id) for r in results for job in r.new_jobs}
    for result in results:
        jobs = result.jobs if show_all else result.new_jobs
        for job in jobs:
            is_new = (job.source, job.job_id, job.company_id) in new_ids
            rows.append((job, is_new))
    rows.sort(
        key=lambda pair: (
            not pair[0].year_match,
            not pair[1],
            pair[0].company_id,
            pair[0].title.casefold(),
        )
    )
    return rows


def write_json(path: Path | None, rows: list[tuple[NormalizedJob, bool]]) -> str:
    payload = [
        {
            "new": is_new,
            "year_match": job.year_match,
            "match_on": job.match_on,
            "company_id": job.company_id,
            "source": job.source,
            "job_id": job.job_id,
            "title": job.title,
            "url": job.url,
            "location": job.location,
            "location_ambiguous": job.location_ambiguous,
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
                "year_match",
                "match_on",
                "company_id",
                "source",
                "job_id",
                "title",
                "url",
                "location",
                "location_ambiguous",
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
                    "year_match": "yes" if job.year_match else "no",
                    "match_on": job.match_on or "",
                    "company_id": job.company_id,
                    "source": job.source,
                    "job_id": job.job_id,
                    "title": job.title,
                    "url": job.url,
                    "location": job.location or "",
                    "location_ambiguous": "yes" if job.location_ambiguous else "no",
                    "department": job.department or "",
                    "workplace": job.workplace or "",
                    "posted_at": job.posted_at or "",
                }
            )
