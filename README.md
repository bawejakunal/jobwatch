# jobwatch

A Python CLI that watches a **whitelist of company careers pages** for **new-grad / entry-level** roles and prints the ones that are currently open, highlighting postings you have not seen before.

It talks to ATS JSON APIs when they exist (Greenhouse, Lever, Ashby, Workday CXS, SmartRecruiters) and falls back to the classic iCIMS iframe search HTML. Custom JS/Cloudflare careers pages are a Phase 2 Playwright recipe — see [PLAN.md](PLAN.md).

Install it on `PATH` with `uv tool install` or `pipx`. Run it from cron.

## Install

```bash
# from a clone
uv tool install -e .

# or
pipx install .
```

Then `jobwatch` is on your PATH (usually `~/.local/bin`).

Dev checkout without a tool install:

```bash
uv sync --extra dev
uv run jobwatch --help
```

## Quick start

```bash
jobwatch init                          # writes ./companies.yaml from the example
# edit companies.yaml — or:
jobwatch add https://job-boards.greenhouse.io/discord
jobwatch add https://jobs.lever.co/palantir
jobwatch list
jobwatch scan                          # new postings only
jobwatch scan --all                    # every open match
jobwatch scan --json
jobwatch scan --company discord --csv out.csv
```

Detection without writing config:

```bash
jobwatch detect https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
jobwatch detect https://careers-fhcrc.icims.com/jobs/search?ss=1 --no-fetch
```

## What it does

1. Loads `companies.yaml` (cwd, `./.jobwatch/`, or `~/.config/jobwatch/`).
2. For each enabled company, hits the matching adapter.
3. Keeps titles that look new-grad (configurable include/exclude). Campus boards can set `scope: all`.
4. Upserts SQLite (`first_seen`, `last_seen`, `closed_at`).
5. Prints a table. **NEW** means first seen on this run. `--all` prints the full open set.

Failed companies are isolated: one 403 does not abort the scan (exit code 1 if anything failed).

## Config

| Looked up in order | |
| --- | --- |
| `--config` / `JOBWATCH_CONFIG` | explicit yaml |
| `./companies.yaml` | local |
| `./.jobwatch/companies.yaml` | local hidden |
| `$JOBWATCH_HOME/companies.yaml` | default `~/.config/jobwatch` |

`.env` is loaded from the current directory, then `$JOBWATCH_HOME/.env`. Copy `.env.example`. Secrets are for Phase 2 Slack/SMTP and Phase 3 LLM keys.

SQLite: `--db` / `JOBWATCH_DB`, else `./jobwatch.sqlite` next to a local yaml, else `$JOBWATCH_HOME/jobwatch.sqlite`.

Example whitelist (one live board per ATS) is in `companies.example.yaml`.

## Adapters

| ATS | How jobwatch reads it |
| --- | --- |
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs` |
| Lever | `GET api.lever.co/v0/postings/{site}` (EU host supported) |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{board}` |
| Workday | `POST {host}/wday/cxs/{tenant}/{site}/jobs` — page size 20, wrap-safe |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{id}/postings` |
| iCIMS | HTML ` /jobs/search?ss=1&in_iframe=1&pr=` — **no public JSON** |

`jobwatch add <url>` recognizes all of the above from the hosted URL (and from embeds on a custom domain).

### iCIMS limits

Classic portals usually work with a desktop Chrome UA. Many tenants return **403/405** or a Cloudflare interstitial; newer **Attract** career sites are SPAs. Those raise a clear “needs browser” error instead of an empty list. A Playwright recipe is Phase 2. Details: [PLAN.md §5.6](PLAN.md).

### Workday limits

`limit` must be 20. `total` is only trusted on page 0. Offsets past 2,000 wrap. jobwatch stops on repeats and warns when `total == 2000`. Tenant may live in the hostname (`nvidia.wd5.myworkdayjobs.com`) or the path (`wd1.myworkdaysite.com/recruiting/...`).

## Scheduler

```cron
15 7 * * *  /home/you/.local/bin/jobwatch scan --json >> /home/you/jobwatch/latest.json 2>> /home/you/jobwatch/scan.log
```

Keep the whitelist short and the interval daily. This is a personal monitor, not a job-board scraper.

## Tests

```bash
uv sync --extra dev
uv run pytest
```

Adapter tests replay recorded fixtures. They do not hit the network.

## Design

[PLAN.md](PLAN.md) has the architecture diagram, config schema, every adapter’s endpoint and pagination rules, the browser-recipe format, the filter, the DB, the CLI, follow-up ATS (Taleo, SuccessFactors, Workable, …), and the roadmap.
