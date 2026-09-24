# github-radar

GitHub ecosystem intelligence. **Phase 1** builds the production-minded,
data-owning foundation: it discovers GitHub repositories by topic/query, tracks
them over time through append-only snapshots, and stores normalized
repositories, topics, contributors and developer profiles in PostgreSQL.
**Phase 2** adds the deterministic analytics that make the historical dataset
usable: time-series snapshots, an explainable momentum score, repository trend
classification, topic aggregation and a confidence model — all exposed through
`trending`, `topics` and `topic` CLI commands (see `docs/TRENDING.md`).
**Phase 3** extends the observation policy to developers and turns contributors
into an intelligence surface: per-developer profile and contribution history,
topic relevance, observed activity, ecosystem score and an emerging-developer
classification, exposed through `developers`, `developer` and
`emerging-developers` (see `docs/DEVELOPERS.md`). **Phase 4** turns the whole
tracked dataset into an **ecosystem graph**: a derived, in-memory network of
developers, repositories and topics connected by real contribution, ownership
and topic edges. It adds overlap and reach metrics, degree/weighted-degree
centrality, connected components, bridge intelligence (developers and
repositories connecting separate topic ecosystems) and graph reports — exposed
through `ecosystem`, `bridges`, `related-topics` and `related-repos` and the
graph sections of `developer` and `topic` (see `docs/GRAPH.md`).

> github-radar is **not** a GitHub Trending scraper. It only ever talks to the
> official GitHub REST API. The real long-term asset is its own historical
> dataset — GitHub gives us today's state, our database remembers how things
> changed.

## What Phase 1 does

- Discover repositories via the GitHub search API (`query`, `language`,
  `topic`, `min-stars`, `created-after`).
- Fetch full repository metadata, topics and contributors.
- Store normalized entities in PostgreSQL: repositories, developers, topics,
  repository↔topic and repository↔contributor associations.
- Record **append-only snapshots** of counters (stars, forks, watchers, open
  issues, size, pushed_at) so growth over time can be analyzed.
- Compute deterministic growth metrics (1d / 7d / 30d deltas and per-day
  rates) plus one simple, documented, experimental **momentum score**.
- Respect GitHub API pagination, rate limits and a configurable pause
  threshold.
- Expose everything through a small Typer CLI.

## What Phase 1 does NOT do

- No web UI, FastAPI, GraphQL, LLM summaries, embeddings/pgvector, or AI topic
  taxonomy.
- No GitHub OAuth / GitHub App / multi-user auth.
- No outreach, CRM, notifications, or email sending.
- No crawling of all of GitHub — discovery is query/topic based on purpose.

## What Phase 2 adds

- **Time-series snapshots** — an ordered, de-duplicated per-repository series
  built from the stored `captured_at` history.
- **Momentum score** — a deterministic, explainable weighted score
  (`stars_growth`, `forks_growth`, `recency`) with a documented bound.
- **Trend classification** — every tracked repository is labelled
  `rising` / `steady` / `declining` / `inactive` / `new` from its 7-day star
  growth and recency.
- **Topic analytics** — repository-level momentum aggregated per topic
  (count, total/avg momentum, coverage share) and a topic confidence rule.
- **Confidence model** — deterministic `LOW` / `MEDIUM` / `HIGH` data-coverage
  scores so thin history is never mistaken for ground truth.
- **Snapshot observation policy** — same-instant observations are upserted
  (a changed observation at the newest `captured_at` replaces that row), and a
  unique `(repository_id, captured_at)` constraint protects the series.
- All analytics are **reproducible**: they are pure functions of snapshot
  history plus an explicit reference instant — the wall clock is never read.

## What Phase 3 adds

- **Developer profile history** — an append-only `developer_snapshots` log
  (followers, following, public_repos per `captured_at`), written on every
  profile fetch; deltas computed over 1d / 7d / 30d windows between real
  observations.
- **Contributor observation history** — an append-only `contributor_snapshots`
  log per repository↔developer link, written on every contributor sync. Since
  GitHub's contribution count is *cumulative*, activity is the **observed
  delta** between two observations of the same link; a single observation is
  never treated as recent activity.
- **Topic relevance** — how connected a developer is to a topic's tracked
  repositories through owning and contributing, with per-repo contribution
  share capped so one huge repository can't dominate.
- **Activity** — observed positive contribution movement inside the tracked
  ecosystem over a window, plus ecosystem score (momentum, ownership, breadth).
- **Emerging classification** — every tracked developer is labelled
  `EMERGING` / `ACTIVE` / `ESTABLISHED` / `QUIET` / `INSUFFICIENT_HISTORY` from
  a deterministic weighted score with small-sample and fame protections (a lone
  +1 contribution can never be "emerging").
- **Public contacts** — surfaced only from profile fields GitHub exposes on
  purpose (profile URL, public email, blog, twitter); nothing is scraped or
  inferred.
- All of it is exposed through `developers`, `developer LOGIN` and
  `emerging-developers`, and each `topic` result now lists the developers
  relevant to it.

## What Phase 4 adds

- **Ecosystem graph** — a derived in-memory graph with `DEVELOPER`,
  `REPOSITORY` and `TOPIC` nodes and three evidence edge types: `OWNS`,
  `CONTRIBUTES_TO` (with cumulative counts, per-repo share and observation
  history) and `TAGGED_WITH`. It is re-derived fresh from PostgreSQL on every
  command — there is no separate graph store to stay in sync with.
- **Overlap & relationships** — jaccard overlap between pairs of repositories
  (developers, topics) and between topic ecosystems, with separate overlap
  components and data-coverage confidence.
- **Reach & centrality** — for every node, how many repositories, developers
  and topics it touches directly, plus deterministic degree/weighted-degree
  centrality per node type.
- **Connected components** — the graph partitioned into deterministic
  components under its direct edges, sized and ordered for reporting.
- **Bridge intelligence** — developers (and repositories) whose evidence
  genuinely spans separate topic ecosystems, scored with explainable bounded
  components, small-sample caps and confidence. Bridges require real evidence
  in *both* ecosystems; many arbitrary tags on one repository never produce a
  bridge.
- **Graph reports** — `ecosystem`, `bridges develop|cross-topic|repos`,
  `related-topics TOPIC`, `related-repos OWNER/REPO`, plus graph sections in
  each `developer LOGIN` and `topic TOPIC` output.
- All graph analytics are **pure functions of the stored dataset**: results
  are reproducible between runs and never consult the wall clock (the
  reference instant is the newest real observation).

## Requirements

- Python 3.12+ (managed via `uv`)
- PostgreSQL 13+ (any recent version works)
- A GitHub token: create a classic token with `public_repo` scope, or a
  fine-grained token with *Public repositories → Metadata: Read*.

## Setup

```bash
cp .env.example .env        # then edit .env
uv sync                     # installs dependencies (Python 3.12 via .python-version)
```

### PostgreSQL setup

Create a database and role for the application, plus a **separate database**
for the destructive integration tests (example):

```sql
CREATE ROLE github_radar WITH LOGIN PASSWORD 'a-strong-password';
CREATE DATABASE github_radar OWNER github_radar;
CREATE DATABASE github_radar_test OWNER github_radar;
```

Then set in `.env`:

```
GITHUB_TOKEN=ghp_...
DATABASE_URL=postgresql+asyncpg://github_radar:a-strong-password@localhost:5432/github_radar
TEST_DATABASE_URL=postgresql+asyncpg://github_radar:a-strong-password@localhost:5432/github_radar_test
```

`TEST_DATABASE_URL` is used **only** by the PostgreSQL integration tests, which
create and drop whole schemas (tables + `alembic_version`). It must never point
at the application database: the test infrastructure refuses to run if
`TEST_DATABASE_URL` is missing, equals `DATABASE_URL`, names `github_radar`, or
does not look like a test database (e.g. `github_radar_test` or any
`*_test`/`test_*` name). An unsafe value fails loudly instead of being
silently skipped. The application itself places no restriction on
`DATABASE_URL`.

### Environment configuration

All configuration comes from environment variables (or `.env` in the project
root — see `.env.example` for every option):

| Variable | Purpose | Default |
| --- | --- | --- |
| `GITHUB_TOKEN` | GitHub API token (required for discover/update) | — |
| `DATABASE_URL` | asyncpg PostgreSQL URL for the app (required) | — |
| `TEST_DATABASE_URL` | separate test DB for destructive integration tests (see PostgreSQL setup) | — |
| `GITHUB_API_BASE_URL` | API base URL | `https://api.github.com` |
| `GITHUB_API_VERSION` | API version header | `2022-11-28` |
| `HTTP_TIMEOUT_SECONDS` | per-request timeout | `15` |
| `HTTP_MAX_RETRIES` | attempts after transient failures | `3` |
| `RATE_LIMIT_PAUSE_THRESHOLD` | stop when remaining quota ≤ this | `50` |
| `DISCOVER_DEFAULT_LIMIT` | default `--limit` for discover | `25` |
| `DISCOVER_MAX_LIMIT` | hard cap for `--limit` | `500` |
| `CONTRIBUTORS_LIMIT_PER_REPO` | contributor rows kept per repo | `30` |
| `PROFILE_REFRESH_DAYS` | refresh developer profiles older than this | `7` |
| `LOG_LEVEL` | root log level | `INFO` |

Never commit `.env`.

### Migrations

```bash
uv run github-radar init-db          # applies all Alembic migrations
uv run alembic revision --autogenerate -m "..."   # (advanced) new migration
uv run alembic upgrade head          # alternative, uses DATABASE_URL
```

## CLI usage

```bash
uv run github-radar --help
uv run github-radar discover --help
```

| Command | Purpose |
| --- | --- |
| `github-radar init-db` | create/upgrade the schema (Alembic) |
| `github-radar discover "mcp" --language python --limit 50` | search & store |
| `github-radar update --limit 100` | refresh tracked repos + new snapshots |
| `github-radar repos` | list tracked repositories & latest counters |
| `github-radar repo owner/repository` | full detail for one repository |
| `github-radar stats` | dataset statistics |
| `github-radar trending --limit 10` | rank repositories by momentum score |
| `github-radar topics --limit 10` | aggregate topics by coverage & momentum |
| `github-radar topic mcp --limit 10` | list repositories for one topic |
| `github-radar developers --sort activity` | list tracked developers with intelligence scores |
| `github-radar developer adalovelace` | full profile, activity and emerging digest for one developer |
| `github-radar emerging-developers` | developers classified EMERGING, most-rising first |
| `github-radar ecosystem` | whole tracked ecosystem graph: node/edge counts, components, hubs, top bridges |
| `github-radar bridges develop --topic mcp` | developers bridging topic ecosystems |
| `github-radar bridges cross-topic mcp browser-agents` | the bridge ecosystem between two topics |
| `github-radar bridges repos` | repositories ranked as cross-ecosystem bridges |
| `github-radar related-topics mcp` | a topic's graph footprint and related topics |
| `github-radar related-repos owner/repository` | a repository's graph footprint and related repositories |
| `github-radar rate-limit` | current GitHub API quota |

See `docs/TRENDING.md` for the exact formulas behind the Phase 2 commands,
`docs/DEVELOPERS.md` for the developer-intelligence formulas behind the Phase 3
commands, and `docs/GRAPH.md` for the Phase 4 graph model, metrics, bridges and
commands.

### Example discovery

```bash
uv run github-radar discover "mcp" --language python --min-stars 10 --limit 50
uv run github-radar repos
uv run github-radar repo modelcontextprotocol/servers
uv run github-radar stats
```

Running `discover`/`update` again later does **not** overwrite history: it
appends new snapshots whenever counters changed, so growth can be measured over
time.

## Snapshot policy

A **state** is a repository's counters at a point in time; an **observation**
is the fact that we fetched that state at an instant. Analytics can only tell
"observed and standing still" apart from "not observed" if identical re-polls
are recorded.

- `insert_snapshot_if_changed` (used by `discover`/`update`) is
  **state-change-only**: it writes only when counters differ from the newest
  stored snapshot, so the historical table stays a low-noise change log.
  Identical observations are skipped — no meaningless duplicates.
- `record_observation` is the unconditional form for periodic pollers: it
  persists **every** observation, even unchanged counters, so a repository
  observed twice with identical state is stored as two rows. (Phase 2 exposes
  this boundary; no scheduler is built yet.)

Since **Phase 2**, one observation is stored per instant: the table enforces a
unique `(repository_id, captured_at)` constraint. A re-observation at the same
`captured_at` replaces that row (idempotent — a raw correction of a bad
capture); an observation at a new instant appends. History recorded at
different instants is never mutated.

## Development

### Tests

```bash
uv run pytest                     # unit tests (no external services)
uv run pytest -m integration      # optional: requires TEST_DATABASE_URL
```

Unit tests mock the GitHub API (no live calls). Integration tests run against a
real PostgreSQL instance and are **skipped automatically** unless
`TEST_DATABASE_URL` is set. An explicitly set but unsafe target (the app
database, `DATABASE_URL` itself, or a non-test-looking name) is **rejected
loudly**, not skipped:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://github_radar:...@localhost:5432/github_radar_test \
  uv run pytest -m integration
```

### Lint & types

```bash
uv run ruff check .
uv run mypy .
```

## Documentation

- `docs/ARCHITECTURE.md` — modules, data flow, schema, API/rate-limit
  strategy, extension points.
- `docs/TRENDING.md` — the Phase 2 analytics: momentum, trends, confidence,
  topic aggregation, and the `trending` / `topics` / `topic` CLI.
- `docs/DEVELOPERS.md` — the Phase 3 developer intelligence: observation
  policy, topic relevance, activity, ecosystem score, emerging classification,
  contacts, and the `developers` / `developer` / `emerging-developers` CLI.
- `docs/GRAPH.md` — the Phase 4 ecosystem graph: model, overlap/reach/centrality
  metrics, connected components, bridges, reports, and the `ecosystem` /
  `bridges` / `related-topics` / `related-repos` CLI.

## License

MIT (pending — see `pyproject.toml`).