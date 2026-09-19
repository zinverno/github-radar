# github-radar

GitHub ecosystem intelligence. **Phase 1** builds the production-minded,
data-owning foundation: it discovers GitHub repositories by topic/query, tracks
them over time through append-only snapshots, and stores normalized
repositories, topics, contributors and developer profiles in PostgreSQL.

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
| `github-radar rate-limit` | current GitHub API quota |

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

A snapshot is written only when the latest observed state actually differs
from the newest stored snapshot (stars, forks, watchers, open issues, size or
`pushed_at` changed). Identical observations are skipped — no meaningless
duplicates. Historical snapshots are never modified or deleted.

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

## License

MIT (pending — see `pyproject.toml`).