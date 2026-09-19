# Architecture

Phase 1 architecture notes for github-radar. Everything here describes how the
current code is put together, the contracts between layers, and the deliberate
design decisions (and their trade-offs).

## Overview

```
┌────────────────────────────── CLIENT ──────────────────────────────┐
│  GitHub REST API  ──►  github/  ──►  domain/  ──►  storage/  ──►  PostgreSQL │
│  (httpx)                raw JSON    canonical      SQLAlchemy 2.x    (asyncpg)
│                          models      dataclasses    async ORM
└────────────────────────────────────────────────────────────────────┘
   ▲                  │  ▲
   │ client           │  analytics/
   │ (auth, retries,  │  (metrics, momentum)
   │  rate limits)    │
   services/  ──►  discovery/service.py   (search → persist → snapshot)
            └──►  services/update.py      (refresh tracked repos)
            └──►  services/developers.py  (throttled profile sync)
```

Data flows **in one direction**: GitHub JSON → Pydantic models → canonical
domain dataclasses → ORM rows. The domain layer (`github_radar.domain`) is the
sole vocabulary shared between `github/` and `storage/` — neither layer knows
the other's types.

## Module layout

| Module | Responsibility |
| --- | --- |
| `config/settings.py` | Env/config with validation (`pydantic-settings`), `require_*` guards |
| `github/client.py` | HTTP client: auth, per-resource rate-limit tracking, retries, pagination |
| `github/repositories.py` | Search / detail / contributors endpoints |
| `github/users.py` | Developer profile endpoint |
| `github/models.py` | Pydantic models for GitHub JSON payloads |
| `github/errors.py` | Typed error hierarchy + `classify_error` |
| `domain/models.py` | Canonical `Repository`, `Developer`, `Contributor`, `Topic`, `RepositorySnapshot` |
| `storage/db.py` | Engine/session factory (`asyncpg`), `ping` |
| `storage/models.py` | SQLAlchemy ORM (the schema — see below) |
| `storage/repositories.py` | All persistence operations + queries + stats |
| `analytics/metrics.py` | Deterministic per-window deltas/rates |
| `analytics/momentum.py` | Experimental, explainable momentum score |
| `discovery/service.py` | Discovery orchestration |
| `services/update.py` | Update orchestration |
| `services/developers.py` | Throttled profile sync |
| `cli/app.py` | Typer CLI |
| `migrations/` | Alembic migrations (hand-written `0001_initial`) |

## Canonical domain model

The domain dataclasses in `github_radar/domain/models.py` intentionally carry
*observations*, not schema concerns:

- `Repository` holds **static metadata** (name, language, topics, …) plus the
  *current* counter values used to seed the first snapshot.
- Counters (stars, forks, watchers, open issues, size, pushed_at) are stored
  **only** in `RepositorySnapshot` rows. `repositories` never holds them — the
  newest snapshot is the source of truth for "current" counters.
- `GitHubClient.paginate` yields raw dicts; endpoint functions convert them to
  domain objects. Nothing outside `github/` sees GitHub JSON.

## Database schema

PostgreSQL 13+, normalized. All timestamps are `TIMESTAMPTZ`.

### `developers`
`id` (PK), `github_id` (unique), `login`, `name`, `avatar_url`, `html_url`,
`bio`, `company`, `location`, `blog`, `public_email`, `twitter_username`,
`followers`, `following`, `public_repos`, `created_at`, `updated_at`,
`first_seen_at`, `last_seen_at`, `profile_fetched_at`.
Unique index: `github_id`. Index: `login`.

### `repositories`
`id` (PK), `github_id` (unique), `node_id`, `owner_id` (FK → developers, SET
NULL), `owner_login`, `name`, `full_name` (unique), `description`, `html_url`,
`homepage`, `default_branch`, `primary_language`, `is_fork`, `is_archived`,
`is_disabled`, `is_template`, `visibility`, `created_at`, `updated_at`,
`pushed_at`, `first_seen_at`, `last_seen_at`.
Unique indexes: `github_id`, `full_name`. Indexes: `primary_language`,
`owner_login`, `last_seen_at`.

### `topics`
`id` (PK), `name` (unique index), `first_seen_at`, `last_seen_at`. Names are
normalized (trimmed, lower-cased) to prevent cosmetic duplicates.

### `repository_topics`  (many-to-many)
`repository_id` (FK, CASCADE) + `topic_id` (FK, CASCADE), composite PK.
Topics are **replaced wholesale** per repository on each fetch (snapshots are
never touched).

### `repository_contributors`  (many-to-many developers)
`repository_id` (FK, CASCADE) + `developer_id` (FK, CASCADE), composite PK,
`contributions` (cumulative count — see *Known limitations*),
`first_seen_at`, `last_seen_at`. Index on `(repository_id, contributions)` for
leaderboard queries.

### `repository_snapshots`  (append-only)
`id` (PK), `repository_id` (FK, CASCADE), `captured_at`, `stars`, `forks`,
`watchers`, `open_issues`, `size_kb`, `pushed_at`. Index on
`(repository_id, captured_at)`. Rows are never updated or deleted.

A cosmetic duplicate-guard unique index on `(repository_id, captured_at)` is not
created because `insert_snapshot_if_changed` already prevents identical
observations (see snapshot policy) — surges within the same second are still
kept because `captured_at` differs.

## Data flow

### Discovery (`discover` command → `RepositoryDiscoveryService.discover`)

```
search query (q, language, topic, min_stars, created_after)
  → GET /search/repositories (paginated, sort/order, per_page ≤ 100)
  → for each result:
       is it already tracked?
         yes → upsert mutable metadata from the SEARCH payload (no extra API call)
         no  → GET /repos/{owner}/{repo}          (topics ride along)
               → upsert repository row
               → set topics
               → insert snapshot (if changed)
               → GET /repos/{owner}/{repo}/contributors
               → sync contributor associations
               → commit (per repository)
               → throttle profile fetch per new developer (GET /users/{login})
```

Cost is intentional: expensive per-repo calls happen **only for newly discovered
repositories**. Already-known repos are refreshed from the search payload alone;
the `update` command exists for full re-observations.

### Update (`update` command → `RepositoryUpdateService.update`)

For every tracked repository: fetch detail, upsert metadata + topics, insert a
snapshot *if counter state changed*, refresh contributors, refresh stale
profiles. Developer profiles are re-fetched at most once per
`PROFILE_REFRESH_DAYS` (tracked via `developers.profile_fetched_at`).

### Snapshot policy

`insert_snapshot_if_changed` compares the new observation with the **newest
stored** snapshot using `RepositorySnapshot.same_counters()` (stars, forks,
watchers, open issues, size_kb, pushed_at). Identical → skipped. Different →
appended. History is never rewritten. This keeps the historical table
low-noise (state-change log, not a sampling log).

## GitHub API surface

| Endpoint | Resource bucket | Purpose |
| --- | --- | --- |
| `GET /search/repositories` | `search` | discovery search, paginated |
| `GET /repos/{owner}/{repo}` | `core` | full metadata + topics |
| `GET /repos/{owner}/{repo}/contributors` | `core` | cumulative contributor counts |
| `GET /users/{login}` | `core` | public developer profile |
| `GET /rate_limit` | `core` | quota snapshot for the `rate-limit` command |

Only the official REST API is used (no GraphQL yet).

### Rate limiting and retries (`github/client.py`)

- Quota tracked **per resource** (`core` / `search`) from
  `X-RateLimit-Resource/Limit/Remaining/Reset` headers.
- **Pause threshold** (default 50, `RATE_LIMIT_PAUSE_THRESHOLD`): when a
  resource's remaining quota drops to the threshold, the client refuses further
  requests (`RateLimitExceeded`) instead of guessing.
- **429** → retried after `Retry-After` (capped at 120 s); a 429 with no
  sleep time is treated as quota exhaustion.
- **5xx / transport errors** → retried with exponential backoff, bounded by
  `HTTP_MAX_RETRIES`.
- **401 / 403 (permission) / 404 / 422** → fail fast with a typed error.
- **403 with `X-RateLimit-Remaining: 0`** → `RateLimitExceeded`.
- Pagination follows the `Link` header; search endpoints are unwrapped via
  their `items` key; short pages / absent links terminate iteration.

## Analytics

### Metrics (`analytics/metrics.py`)

For each of `stars`, `forks`, `watchers`, `open_issues` and each of the windows
1d / 7d / 30d, compute a `FieldDelta` with `delta`, `base_value`,
`current_value`, `per_day`, and a `TimeWindow` (span + `complete` flag). Base is
the newest snapshot older than `now - window` when one exists, otherwise the
earliest snapshot (window flagged incomplete). All inputs are
`RepositorySnapshot` values; results are `None` when history is too short.

### Momentum (`analytics/momentum.py`)

One experimental, **explainable** score (weights are module constants, every
component is exposed on the result):

```
growth_pct = 100 * current / base − 100        (7-day star delta over base)
recency    = clamp(1 − days_since_last_push / 90, 0, 1)
score      = 1.0 · stars_growth_pct
           + 0.5 · forks_growth_pct
           + 2.0 · recency
```

Returns `None` when the base snapshot is missing or zero (uncomputable) rather
than fabricating a number. Replace `WEIGHTS` / `WINDOW_DAYS` /
`RECENCY_HALF_LIFE_DAYS` — or the whole function — without touching callers.

## Config knobs

See `README.md` for the full table. Key switches: `GITHUB_TOKEN`,
`DATABASE_URL`, `GITHUB_API_BASE_URL`, `HTTP_TIMEOUT_SECONDS`,
`HTTP_MAX_RETRIES`, `RATE_LIMIT_PAUSE_THRESHOLD`, `CONTRIBUTORS_LIMIT_PER_REPO`,
`PROFILE_REFRESH_DAYS`, `DISCOVER_DEFAULT_LIMIT`, `DISCOVER_MAX_LIMIT`.

## Known limitations (deliberate)

- **Contributors are cumulative.** GitHub's `/contributors` endpoint reports
  contribution counts over the repository's whole history, so
  `repository_contributors.contributions` cannot be read as "activity in the
  last N days". A per-window signal requires GraphQL (commits with dates), which
  is intentionally out of Phase 1.
- **Discovery refreshes known repos cheaply.** Existing repositories are not
  re-fetched in detail during `discover`; use `update` for full refresh +
  snapshots.
- **Snapshots record state changes, not a strict schedule.** If nothing
  observable changed between two runs, no snapshot is written.
- **Search is capped by GitHub** (100 items/page, ~1000 results) — discovery is
  query/topic scoped, not a full GitHub crawl.
- No time-series sampling job exists yet (Phase 2 territory).

## Extension points for Phase 2

- A scheduler/crawler job can call the same `RepositoryDiscoveryService` /
  `RepositoryUpdateService` and rely on the snapshot policy for history.
- Derived snapshots (e.g. "N days ago, per repo") can be reconstructed
  *without* writing new data because `captured_at` history is retained.
- Topic-based growth analytics need only join `repository_topics` →
  `repository_snapshots`.
- The momentum function is designed to be replaced by richer models
  (e.g. time-series or LLM-assisted summaries) — analytics stay purely
  functions of snapshots, so downstream models don't touch storage.
- A future web/API frontend consumes `storage.repositories` queries; the CLI
  already shares them (`list_repos_with_latest`, `compute_stats`).