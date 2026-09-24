# Architecture

Architecture notes for github-radar. Everything here describes how the current
code is put together, the contracts between layers, and the deliberate design
decisions (and their trade-offs). Phase 1 = data foundation + CLI; Phase 2 =
deterministic analytics (time-series, momentum, trends, topics, confidence)
built purely on the snapshot history; Phase 3 = developer intelligence built on
observation deltas; Phase 4 = a derived ecosystem graph (developers,
repositories, topics) built purely on the stored relational dataset.

## Overview

```
┌────────────────────────────── CLIENT ──────────────────────────────┐
│  GitHub REST API  ──►  github/  ──►  domain/  ──►  storage/  ──►  PostgreSQL │
│  (httpx)                raw JSON    canonical      SQLAlchemy 2.x    (asyncpg)
│                          models      dataclasses    async ORM
└────────────────────────────────────────────────────────────────────┘
   ▲                  │            ▲
   │ client           │            │ analytics/ (metrics, momentum, trends,
   │ (auth, retries,  │            │  topics, confidence, timeseries, recency)
   │  rate limits)    │            │  — pure functions of RepositorySnapshot
   services/  ──►  discovery/service.py   (search → persist → snapshot)
            └──►  services/update.py      (refresh tracked repos)
            └──►  services/developers.py  (throttled profile sync)
            └──►  graph/ (Phase 4, read-only)  PostgreSQL ──► EcosystemGraph
                  loader → model → overlap/relationships/metrics/bridges
                  → reports ──► cli (ecosystem, bridges, related-*, graph sections)
```

Data flows **in one direction**: GitHub JSON → Pydantic models → canonical
domain dataclasses → ORM rows. The domain layer (`github_radar.domain`) is the
sole vocabulary shared between `github/` and `storage/` — neither layer knows
the other's types. The graph layer is a **read-only consumer** of the ORM: it
never writes back, so it can always be re-derived from freshly stored data.

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
| `analytics/metrics.py` | Deterministic per-window deltas/rates + `RepoMetrics` |
| `analytics/timeseries.py` | `SnapshotSeries`: ordered, deduped snapshot lookups |
| `analytics/recency.py` | Deterministic age/staleness relative to a reference instant |
| `analytics/momentum.py` | Experimental, explainable momentum score |
| `analytics/trends.py` | Trend classification + `rank_repositories` (trending core) |
| `analytics/topics.py` | `aggregate_topics` (topic-level momentum aggregation) |
| `analytics/confidence.py` | Deterministic data-coverage `LOW`/`MEDIUM`/`HIGH` model |
| `discovery/service.py` | Discovery orchestration |
| `services/update.py` | Update orchestration |
| `services/developers.py` | Throttled profile sync |
| `graph/model.py` | `EcosystemGraph` + node/edge dataclasses, incremental construction, typed access, `filter()` |
| `graph/loader.py` | Build the graph from PostgreSQL in a fixed number of batched queries |
| `graph/overlap.py` | Jaccard `Overlap` shared by all pairwise relationships |
| `graph/scoring.py` | Shared `clamp` / `mean` / weighted-score helpers + `recent_activity` |
| `graph/confidence.py` | Data-coverage confidence (LOW/MEDIUM/HIGH) for relationships & bridges |
| `graph/relationships.py` | Co-contributors, related repositories, related topics |
| `graph/metrics.py` | Reach, degree/weighted-degree centrality, connected components |
| `graph/bridges.py` | Developer / cross-topic / repository bridge intelligence |
| `graph/reports.py` | Deterministic report projections used by the CLI |
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

### `repository_snapshots`  (observation log)
`id` (PK), `repository_id` (FK, CASCADE), `captured_at`, `stars`, `forks`,
`watchers`, `open_issues`, `size_kb`, `pushed_at`. Unique constraint
`uq_repository_snapshots_repo_captured` on `(repository_id, captured_at)`.
**One observation per instant per repository.** Rows from different instants
are never updated or deleted.

`record_observation` writes every poll; `insert_snapshot_if_changed` writes
only when counters changed (both delegate to the same per-instant upsert). The
unique constraint additionally forbids two rows for the same repository and
instant: a re-observation at an already-captured instant **replaces** that row
(idempotent / raw correction) and a new instant **appends**. Surges within the
same second that happen to be captured at the same instant collapse into one
row — accepted and documented trade-off.

### `developer_snapshots`  (developer profile observation log, Phase 3)
`id` (PK), `developer_id` (FK, CASCADE), `captured_at`, `followers`,
`following`, `public_repos`. Unique constraint
`uq_developer_snapshots_dev_captured` on `(developer_id, captured_at)`. Written
by `sync_profile` whenever a developer profile is fetched; one observation per
instant per developer. Only public, non-inferred counters are stored.

### `contributor_snapshots`  (contribution-link observation log, Phase 3)
`id` (PK), `repository_id` (FK, CASCADE), `developer_id` (FK, CASCADE),
`captured_at`, `contributions` (cumulative GitHub count). Unique constraint on
`(repository_id, developer_id, captured_at)`. Written by `sync_contributors`
for every observed contributor link. The delta between two snapshots of the
same link is the *observed* activity signal for a window; a single snapshot is
never recent activity.

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
`PROFILE_REFRESH_DAYS` (tracked via `developers.profile_fetched_at`). Each
profile fetch writes a `developer_snapshots` row and each contributor sync
writes `contributor_snapshots` rows, so developer intelligence accumulates
history without any extra API cost.

### Snapshot policy

A **state** is the repository's counters at a point in time. An
**observation** is a fact that we fetched that state at a specific instant.
The distinction matters for analytics: _two observations with identical state_
prove the repository was seen and standing still, while _one observation_ only
proves it existed once.

`insert_snapshot_if_changed` is the Phase 1 **state-change-only** filter: it
compares the new observation with the *newest stored* snapshot using
`RepositorySnapshot.same_counters()` (stars, forks, watchers, open issues,
size_kb, pushed_at). Identical → skipped (`None`). Different → written via
`record_observation`.

`record_observation` is the truer, unconditional form: it persists **every**
observation and never compares counters. A repository genuinely polled twice
with identical counters is stored as **two** rows — so clearly-observed zero
growth is representable. Idempotency is per instant
(`uq_repository_snapshots_repo_captured`): a re-observation at the exact same
`captured_at` replaces that row (a raw correction), a new instant appends, and
history from other instants is never rewritten.

There is **no production scheduler yet**: services and the CLI still use the
state-change-only filter, so the stored history remains a low-noise change log.
Phase 2 only makes *periodic* observation possible — a future scheduler calls
`record_observation` on each poll tick to fill in unchanged periods.

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

All analytics are **pure functions of `RepositorySnapshot` history** plus an
explicit reference instant — the wall clock is never consulted, so results are
reproducible between runs without a new snapshot. The CLI computes the
reference instant from the newest stored `captured_at`.

### Metrics (`analytics/metrics.py`)

For each of `stars`, `forks`, `watchers`, `open_issues` and each of the windows
1d / 7d / 30d, compute a `FieldDelta` with `delta`, `base_value`,
`current_value`, `per_day`, and a `TimeWindow` (span + `complete` flag). Base is
the newest snapshot older than `now - window` when one exists, otherwise the
earliest snapshot (window flagged incomplete). All inputs are
`RepositorySnapshot` values; results are `None` when history is too short.
`RepoMetrics` bundles every window's deltas, the latest counts and a
`SnapshotSeries` of the repository's snapshots.

### Time series (`analytics/timeseries.py`)

`SnapshotSeries` wraps the ordered, de-duplicated snapshot list and answers
`at_or_before` / `at_or_after` / `previous_of` / `nearest_to` lookups. It is the
shared lookup tool for the analytic modules and always picks the
deterministic-equal answer (e.g. the older snapshot on an exact tie).

### Recency (`analytics/recency.py`)

`recency_score(pushed_at, reference_now, half_life_days)` decays linearly from
`1.0` (pushed at the reference instant) to `0.0` over the half-life, clamped.
Used by both momentum and trend classification so the "freshness" definition is
shared.

### Momentum (`analytics/momentum.py`)

One experimental, **explainable** score (weights are module constants, every
component is exposed on the result):

```
stars_growth = clamp(100 · Δstars / base, 0, 100)     (Δ = 7-day window delta)
forks_growth = clamp(100 · Δforks / base, 0, 100)     (same window)
recency      = recency_score(last push, reference_now, 90d)  ∈ [0, 1]
score        = 0.05 · stars_growth
             + 0.03 · forks_growth
             + 1.00 · recency
```

The score is bounded: `MAX_SCORE = 0.05·100 + 0.03·100 + 1.0·1 = 9.0`.
Returns `None` when either growth delta is unavailable or its base is zero
(uncomputable) rather than fabricating a number. Replace `WEIGHTS` /
`WINDOW_DAYS` / `RECENCY_HALF_LIFE_DAYS` — or the whole function — without
touching callers.

### Trends (`analytics/trends.py`)

`classify_trend(metrics, momentum, *, reference_now)` labels a repository
`rising` / `steady` / `declining` / `inactive` / `new` from its 7-day star
growth (thresholds ±5 pp) and recency (< 0.05 ⇒ `inactive`). `rank_repositories`
filters out repositories without a computable momentum, sorts the rest by score
descending and attaches each row's 7-day window confidence — the pure core
behind `trending`.

### Topics (`analytics/topics.py`)

`aggregate_topics(reports_by_topic)` turns `{topic: [RepositoryReport, …]}` into
`TopicAggregate` rows (repository count, total/avg momentum,
share-with-momentum, plus the topic confidence rule), sorted by total momentum
descending.

### Confidence (`analytics/confidence.py`)

A deterministic data-coverage score in `[0, 1]` mapped to `LOW` / `MEDIUM` /
`HIGH`. Repository windows blend three terms — snapshot count, window span and
base-gap — weighted `0.34 / 0.33 / 0.33`. Topic confidence blends topic size,
share-with-momentum and share-of-complete-windows (`0.50 / 0.30 / 0.20`) and
cannot reach `HIGH` below `TOPIC_MIN_REPOS_FOR_HIGH` (10). Confidence is *not*
a p-value; it exists so thin history is loudly labelled.

### Developer intelligence (Phase 3)

Developer analytics are *observation-driven*, mirroring the Phase 2 snapshot
policy: developer profile counters (`analytics/developers.py`) and cumulative
contribution links (`analytics/intelligence.py`) only ever report deltas
**between real observations**. A missing window end is `None`, never a
fabricated zero.

- `analytics/history.py` — `ObservationSeries[T]`, an ordered, de-duplicated
  series over any object with a `captured_at` (the developer-analytics analogue
  of `SnapshotSeries`), plus the generic `window_delta`.
- `analytics/developers.py` — `compute_profile_deltas` (followers, following,
  public_repos over 1d/7d/30d) and `compute_contribution_delta`.
- `analytics/intelligence.py` — topic relevance (owned + contributed topic
  repos, per-repo contribution share capped), observed activity (only positive
  cumulative-count deltas in the window count), ecosystem score (momentum +
  ownership + breadth + relevance), and the
  `EMERGING` / `ACTIVE` / `ESTABLISHED` / `QUIET` / `INSUFFICIENT_HISTORY`
  classification with small-sample and fame protections.
- `services/intelligence.py` — `DeveloperIntelligenceService.load_dataset`
  assembles `RepoContext` / `ContributorLink` / `DeveloperContext` from storage
  and anchors `reference_now` on the newest real `captured_at` observation
  (wall clock only when the dataset is empty); `build_reports` produces one
  digest per developer. `filter_reports` implements the `developers` filters.
- Public contacts are built only from profile fields GitHub exposes on purpose
  (profile URL, public email, blog, twitter) — nothing scraped or inferred.

See `docs/DEVELOPERS.md` for the formulas and CLI commands.

### Ecosystem graph (Phase 4)

The graph is **derived, not stored**: `graph/loader.py` (`load_graph`) rebuilds
the whole `EcosystemGraph` in memory from PostgreSQL on every command using a
fixed number of batched statements (latest repository rows, topics by
repository, snapshots by repository, contributor links, contributor snapshots,
developers) — never a query per repository or per contributor link. The
regression is pinned in the integration suite. `reference_now` is anchored to
the newest stored `captured_at`, falling back to the wall clock only when the
dataset is empty.

It is a **co-contribution / co-occurrence** model, not a social graph: node
types are `DEVELOPER` / `REPOSITORY` / `TOPIC` and edges are the relational
rows themselves (`OWNS`, `CONTRIBUTES_TO` carrying share + observation history,
`TAGGED_WITH`), collapsed under natural identifiers so duplicates never produce
parallel edges. Connectedness is a statement about "transitively reachable
through tracked repositories and topics" and nothing more.

On top of the model:

- `graph/overlap.py` — jaccard `Overlap` shared by every pairwise relationship.
- `graph/relationships.py` — related repositories (developer overlap and topic
  overlap reported separately, combined `relationship_score` only alongside its
  components), related topics (repository/developer overlap separate), and
  developer co-contribution strength (log-scaled breadth + capped per-repo
  shares + momentum + observed co-activity).
- `graph/metrics.py` — per-type reach; degree/weighted-degree centrality over
  homogeneous neighbourhoods; deterministic Union-Find connected components
  sized and ordered canonically (missing nodes → zero reach, never a raise).
- `graph/confidence.py` — data-coverage confidence (≤ 1.0) for relationships
  and bridges; below 3 distinct evidence repositories a `HIGH` is unreachable.
- `graph/bridges.py` — the bridge producers. Developer bridges require ≥ 2
  topic memberships, ≥ 2 distinct supporting repositories and ≥ 10 lifetime
  contributions; below the minimums the score is hard-capped at
  `SMALL_SAMPLE_CAP = 0.35` and flagged. All scores are bounded `[0, 1]`,
  weights are module constants, and every component (including **missing**
  ones) is listed per result. Followers never enter the score.
- `graph/reports.py` — frozen report dataclasses (`EcosystemGraphReport`,
  `DeveloperGraphReport`, `RepositoryGraphReport`, `TopicGraphReport`,
  `CrossTopicBridgeReport`) are pure projections; a node absent from the graph
  yields an empty report, never an exception.

The CLI mounts the `bridges` group **twice** (top-level `bridges` and inside
the `graph` group) so the legacy `graph bridges develop` spelling keeps
working; `Typer.add_typer` only appends a `TyperInfo` reference, so mounting
one app instance twice is safe. Unit tests pin the graph math and the CLI
shape; the loader's bounded-query behavior is covered only by the PostgreSQL
integration suite.

See `docs/GRAPH.md` for the full model, formulas and command reference.

## Config knobs

See `README.md` for the full table. Key switches: `GITHUB_TOKEN`,
`DATABASE_URL`, `GITHUB_API_BASE_URL`, `HTTP_TIMEOUT_SECONDS`,
`HTTP_MAX_RETRIES`, `RATE_LIMIT_PAUSE_THRESHOLD`, `CONTRIBUTORS_LIMIT_PER_REPO`,
`PROFILE_REFRESH_DAYS`, `DISCOVER_DEFAULT_LIMIT`, `DISCOVER_MAX_LIMIT`.

## Known limitations (deliberate)

- **Contributors are cumulative.** GitHub's `/contributors` endpoint reports
  contribution counts over the repository's whole history, so
  `repository_contributors.contributions` cannot be read as "activity in the
  last N days". Phase 3 leans into this instead of fighting it: the *delta*
  between two `contributor_snapshots` of the same link is the observed activity
  signal, and a single snapshot is never treated as activity. A per-window
  commit-date signal would still require GraphQL, which is intentionally out of
  scope. The graph inherits the same rule: `CONTRIBUTES_TO` edges from a single
  observation never contribute "recent activity".
- **The graph reflects tracked data.** Edge completeness is bounded by what
  discovery has tracked; connectivity is a statement about the dataset, not all
  of GitHub.
- **Graph centrality is structural** (degree-based) by design — deterministic
  and explainable, at the price of no spectral ranking.
- **Discovery refreshes known repos cheaply.** Existing repositories are not
  re-fetched in detail during `discover`; use `update` for full refresh +
  snapshots.
- **Snapshots record state changes, not a strict schedule.** If nothing
  observable changed between two runs, no snapshot is written.
- **Search is capped by GitHub** (100 items/page, ~1000 results) — discovery is
  query/topic scoped, not a full GitHub crawl.
- **Analytics are snapshot-anchored.** No wall-clock scheduler produces time
  series yet: current services/CLI use the state-change-only
  `insert_snapshot_if_changed`, so repository history fills in only when state
  changed. Developer history (Phase 3) grows on every `discover`/`update` run:
  each profile fetch writes a developer snapshot and each contributor sync
  writes contributor snapshots, whether or not counters changed. Periodic
  observation on a strict schedule is *possible* (the storage boundary exposes
  `record_observation`), but a scheduler that polls on a schedule is a future
  pipeline, not yet built.

## Extension points

- A scheduler/crawler job can call the same `RepositoryDiscoveryService` /
  `RepositoryUpdateService` and rely on the snapshot policy for history.
- Derived snapshots (e.g. "N days ago, per repo") can be reconstructed with
  `SnapshotSeries` *without* writing new data because `captured_at` history is
  retained.
- Topic-based growth analytics already join `repository_topics` →
  `repository_snapshots` via `_collect_tracked` in the CLI; a richer topic
  model can reuse `aggregate_topics` / `TopicConfidenceRule`.
- The momentum function is designed to be replaced by richer models
  (e.g. time-series or LLM-assisted summaries) — analytics stay purely
  functions of snapshots, so downstream models don't touch storage.
- `DeveloperIntelligenceService.load_dataset` produces the entire
  analytics-ready dataset in one shot; a future web/API frontend can hand
  richer models the same `DeveloperDataset` without repeating the storage
  queries, and costlier models slot in behind it without touching storage.
- A future web/API frontend consumes `storage.repositories` queries; the CLI
  already shares them (`list_repos_with_latest`, `compute_stats`).
- The ecosystem graph is fully **re-derivable**: `graph/loader.py` can rebuild
  it from the relational rows at any time, so a future
  `graph export --json` (node/edge document) or a persistent graph DB just
  replays the same load + report pipeline rather than maintaining a second
  source of truth.