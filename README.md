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
jobwatch init                          # writes Sierra + Harvey into ./companies.yaml
# or:
jobwatch add https://sierra.ai/careers --name Sierra
jobwatch add https://www.harvey.ai/careers --name Harvey
jobwatch list
jobwatch scan                          # new postings only (2027 matches first)
jobwatch scan --all                    # every open match
jobwatch scan --json
jobwatch scan --company sierra --csv out.csv
jobwatch scan --no-enrich              # title-only, skip detail fetches
jobwatch scan --any-location           # skip the US-only location filter
```

Detection without writing config:

```bash
jobwatch detect https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
jobwatch detect https://careers-fhcrc.icims.com/jobs/search?ss=1 --no-fetch
```

## What it does

1. Loads `companies.yaml` (cwd, `./.jobwatch/`, or `~/.config/jobwatch/`).
2. For each enabled company, hits the matching adapter.
3. Keeps software-engineering roles. A title does not have to say new grad or 2027; those matches are still sorted first. The title is searched first (in-memory SQLite FTS5). Sales, marketing, recruiting, and similar titles are dropped. GTM, solutions, support, and forward-deployed engineers are kept. When the title names no function, the job text is searched for a software phrase such as "software engineer". Senior, staff, principal, director, manager, and lead titles are left out unless they also say new grad, intern, or 2027. Campus boards can set `scope: all` to keep every posting. YC companies use `scope: all` with the software search still on, plus `max_age_days: 90`. Default location filter is **US-only** (any US office, US remote, or unspecified Remote). `--any-location` turns it off.
4. Upserts SQLite (`first_seen`, `last_seen`, `closed_at`, cached `description`).
5. Prints a table. **NEW** means first seen on this run. **2027** is flagged and sorted first. `--all` prints the full open set.

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

Default whitelist is Sierra and Harvey (both Ashby). Per-ATS samples are in `companies.ats-examples.yaml`.

## Adapters

| ATS | How jobwatch reads it |
| --- | --- |
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs` |
| Lever | `GET api.lever.co/v0/postings/{site}` (EU host supported) |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{board}` |
| Workday | `POST {host}/wday/cxs/{tenant}/{site}/jobs` — page size 20, wrap-safe |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{id}/postings` |
| iCIMS | HTML ` /jobs/search?ss=1&in_iframe=1&pr=` — **no public JSON** |
| YC | HTML `https://www.ycombinator.com/companies/{slug}/jobs` — Inertia `data-page` JSON. Keeps `createdAt` within `max_age_days` (90 on the shipped list). |

`jobwatch add <url>` recognizes all of the above from the hosted URL (and from embeds on a custom domain). A YC jobs URL is stored with `scope: all` and `max_age_days: 90`. YC listings are still limited to software-engineering roles. A YC job page is fetched only when the title does not decide the role.

The default whitelist is Sierra, Harvey, and every company on the [YC US hiring directory](https://www.ycombinator.com/companies?isHiring=true&regions=United%20States%20of%20America). That directory is about 1,200 companies. `jobwatch sync-yc` replaces the YC block with a fresh directory read and leaves every other company in place. A full scan is about 15 minutes at the default 1.5 requests/second.

### iCIMS limits

Classic portals usually work with a desktop Chrome UA. Many tenants return **403/405** or a Cloudflare interstitial; newer **Attract** career sites are SPAs. Those raise a clear “needs browser” error instead of an empty list. A Playwright recipe is Phase 2. Details: [PLAN.md §5.6](PLAN.md).

### Workday limits

`limit` must be 20. `total` is only trusted on page 0. Offsets past 2,000 wrap. jobwatch stops on repeats and warns when `total == 2000`. Tenant may live in the hostname (`nvidia.wd5.myworkdayjobs.com`) or the path (`wd1.myworkdaysite.com/recruiting/...`).

## Scheduler

```cron
15 7 * * *  /home/you/.local/bin/jobwatch scan --json >> /home/you/jobwatch/latest.json 2>> /home/you/jobwatch/scan.log
```

Keep the interval daily. The default list includes the YC US hiring directory, so a scan takes about 15 minutes. This is a personal monitor, not a job-board scraper.

## Tests

```bash
uv sync --extra dev
uv run pytest
```

Adapter tests replay recorded fixtures. They do not hit the network.

## Design

[PLAN.md](PLAN.md) has the architecture diagram, config schema, every adapter’s endpoint and pagination rules, the browser-recipe format, the filter, the DB, the CLI, follow-up ATS (Taleo, SuccessFactors, Workable, …), and the roadmap.
