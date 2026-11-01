from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from jobwatch import __version__
from jobwatch.adapters.base import AdapterError
from jobwatch.config import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    append_company,
    default_home,
    dump_config,
    filter_settings,
    find_config_path,
    find_db_path,
    load_config,
    load_env,
    write_example,
)
from jobwatch.detect import company_from_detection, detect, detect_from_url
from jobwatch.http import build_client
from jobwatch.models import AppConfig, Defaults
from jobwatch.output import console, err_console, render_table, rows_from_results, write_csv, write_json
from jobwatch.pipeline import known_ats, scan_companies
from jobwatch.store import Store

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Watch a whitelist of company careers pages for new-grad / entry-level roles.",
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"jobwatch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    load_env()


@app.command()
def init(
    dest: Optional[Path] = typer.Option(
        None,
        "--dest",
        help="Directory for companies.yaml (default: cwd if it looks local, else $JOBWATCH_HOME).",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing companies.yaml."),
) -> None:
    """Copy the example whitelist and .env template into place."""
    target_dir = dest or (Path.cwd() if (Path.cwd() / "pyproject.toml").exists() else default_home())
    yaml_path = target_dir / "companies.yaml"
    if yaml_path.exists() and not force:
        err_console.print(f"[yellow]{yaml_path} already exists[/yellow] (pass --force to overwrite)")
        raise typer.Exit(2)
    write_example(yaml_path)
    env_src = Path(__file__).resolve().parents[2] / ".env.example"
    env_dest = target_dir / ".env.example"
    if env_src.is_file() and not env_dest.exists():
        env_dest.write_text(env_src.read_text())
    console.print(f"Wrote [bold]{yaml_path}[/bold]")
    console.print("Edit the whitelist, then run [bold]jobwatch scan[/bold].")


@app.command("detect")
def detect_cmd(
    url: str = typer.Argument(..., help="Careers page or hosted ATS board URL."),
    fetch: bool = typer.Option(
        True,
        "--fetch/--no-fetch",
        help="GET the URL when it is not an obvious ATS host (needed for custom domains).",
    ),
) -> None:
    """Show which ATS a URL belongs to and the parsed board/tenant/site."""
    if not fetch:
        found = detect_from_url(url)
        if not found:
            err_console.print("No ATS matched the URL. Re-run without --no-fetch to sniff HTML.")
            raise typer.Exit(1)
        _print_detection(found)
        return
    client = build_client()
    try:
        found = detect(url, client)
    except AdapterError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()
    _print_detection(found)


def _print_detection(found) -> None:
    console.print(f"[bold]ATS[/bold]        {found.ats}  ({found.confidence})")
    console.print(f"[bold]URL[/bold]        {found.careers_url}")
    for key, value in found.identity.items():
        if key == "ats":
            continue
        console.print(f"[bold]{key:<10}[/bold] {value}")
    if found.notes:
        console.print(f"[dim]{found.notes}[/dim]")


@app.command()
def add(
    url: str = typer.Argument(..., help="Careers page or hosted ATS board URL."),
    name: Optional[str] = typer.Option(None, "--name", help="Display name."),
    company_id: Optional[str] = typer.Option(None, "--id", help="Stable id used in SQLite."),
    scope: Optional[str] = typer.Option(
        None,
        "--scope",
        help="new_grad or all. Defaults to all for YC jobs URLs, otherwise new_grad.",
    ),
    config: Optional[Path] = typer.Option(None, "--config", help="companies.yaml to write."),
    no_probe: bool = typer.Option(False, "--no-probe", help="Skip a live fetch that verifies the board."),
) -> None:
    """Detect the ATS for a URL and append it to the whitelist."""
    client = build_client()
    try:
        found = detect(url, client)
        if scope is None:
            scope = "all" if found.ats == "yc" else "new_grad"
        if scope not in {"new_grad", "all"}:
            raise typer.BadParameter("scope must be new_grad or all")
        company = company_from_detection(found, name=name, company_id=company_id)
        company.scope = scope  # type: ignore[assignment]
        if found.ats == "yc":
            from jobwatch.adapters.yc import DEFAULT_MAX_AGE_DAYS

            company.max_age_days = DEFAULT_MAX_AGE_DAYS
        if not no_probe and found.ats != "browser":
            from jobwatch.adapters import get_adapter

            adapter = get_adapter(found.ats)
            jobs = adapter.fetch(company, client)
            console.print(f"Probe: [bold]{len(jobs)}[/bold] open posting(s) on {found.ats}.")
    except AdapterError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()

    path = find_config_path(str(config) if config else None)
    if path is None:
        path = Path.cwd() / "companies.yaml"
        if not path.exists():
            dump_config(
                AppConfig(
                    defaults=Defaults(
                        include_title=list(DEFAULT_INCLUDE),
                        exclude_title=list(DEFAULT_EXCLUDE),
                    ),
                    companies=[],
                ),
                path,
            )
    append_company(path, company)
    console.print(f"Added [bold]{company.id}[/bold] ({company.ats}) to {path}")
    _print_detection(found)


@app.command("sync-yc")
def sync_yc_cmd(
    config: Optional[Path] = typer.Option(None, "--config", help="companies.yaml to update."),
) -> None:
    """Replace YC companies with the current US hiring directory.

    Sierra, Harvey, and any other non-YC companies stay put. Each YC entry
    watches that company's public jobs page and keeps roles from about the
    last 90 days.
    """
    from jobwatch.ycdir import fetch_us_hiring_companies, merge_yc_companies

    path = find_config_path(str(config) if config else None)
    if path is None or not path.is_file():
        err_console.print(
            "No companies.yaml found. Run [bold]jobwatch init[/bold] first, then [bold]jobwatch sync-yc[/bold]."
        )
        raise typer.Exit(2)
    cfg = load_config(path)
    client = build_client()
    try:
        hits = fetch_us_hiring_companies(client)
    except AdapterError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        client.close()
    before = sum(1 for company in cfg.companies if company.ats == "yc")
    cfg.companies, skipped = merge_yc_companies(cfg.companies, hits)
    dump_config(cfg, path)
    after = sum(1 for company in cfg.companies if company.ats == "yc")
    console.print(
        f"Wrote [bold]{after}[/bold] YC companies to {path} (replaced {before}, skipped {skipped} already listed)."
    )


@app.command(name="list")
def list_cmd(
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """List companies in the whitelist."""
    cfg = _require_config(config)
    table = Table(title="jobwatch whitelist")
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("ats")
    table.add_column("scope")
    table.add_column("enabled")
    table.add_column("url", overflow="fold")
    for company in cfg.companies:
        table.add_row(
            company.id,
            company.name,
            company.ats or "?",
            company.scope,
            "yes" if company.enabled else "no",
            company.careers_url,
        )
    console.print(table)
    console.print(f"[dim]{cfg.path} · ATS adapters: {', '.join(known_ats())}[/dim]")


@app.command()
def scan(
    all_open: bool = typer.Option(
        False,
        "--all",
        help="Print every currently open match, not only postings first seen this run.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Write JSON to stdout (in addition to the table)."),
    csv_path: Optional[Path] = typer.Option(None, "--csv", help="Write CSV to this path."),
    company: Optional[list[str]] = typer.Option(None, "--company", help="Limit to one or more company ids."),
    no_filter: bool = typer.Option(False, "--no-filter", help="Skip the new-grad title filter."),
    check_description: bool = typer.Option(
        False,
        "--check-description",
        help="Force description enrichment and body matching on (already the default).",
    ),
    no_enrich: bool = typer.Option(
        False,
        "--no-enrich",
        help="Skip description fetches and body matching (title-only).",
    ),
    any_location: bool = typer.Option(
        False,
        "--any-location",
        help="Disable the location filter (default is US-only).",
    ),
    config: Optional[Path] = typer.Option(None, "--config"),
    db: Optional[Path] = typer.Option(None, "--db"),
    quiet: bool = typer.Option(False, "--quiet", help="Skip the table; still write JSON/CSV."),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Fetch every enabled company, update SQLite, print new (or all) matches."""
    cfg = _require_config(config)
    db_path = find_db_path(str(db) if db else None, Path(cfg.path) if cfg.path else None)
    store = Store(db_path)
    enrich_flag: bool | None = True if check_description else (False if no_enrich else None)
    settings = filter_settings(cfg, check_description=enrich_flag, any_location=any_location)
    try:
        results = scan_companies(
            cfg,
            store,
            settings=settings,
            company_ids=company,
            apply_new_grad_filter=not no_filter,
            verbose=verbose,
        )
    except AdapterError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    finally:
        store.close()

    rows = rows_from_results(results, show_all=all_open)
    title = "Currently open matches" if all_open else "New postings since last scan"
    if not quiet:
        render_table(rows, title=title)
        for result in results:
            if result.warning:
                err_console.print(f"[yellow]{result.company.id}:[/yellow] {result.warning}")
        err_console.print(f"[dim]state: {db_path}[/dim]")

    if as_json:
        console.print(write_json(None, rows))
    if csv_path:
        write_csv(csv_path, rows)
        err_console.print(f"Wrote CSV {csv_path}")

    if any(r.error for r in results):
        raise typer.Exit(1)


def _require_config(config: Path | None) -> AppConfig:
    path = find_config_path(str(config) if config else None)
    if path is None or not path.is_file():
        err_console.print(
            "No companies.yaml found. Run [bold]jobwatch init[/bold] or [bold]jobwatch add <url>[/bold]."
        )
        raise typer.Exit(2)
    return load_config(path)
