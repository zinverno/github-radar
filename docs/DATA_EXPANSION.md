# Phase 6.5 — Data Expansion & Refresh

Checkpoint A adds offline discovery planning and enforceable HTTP budgets.
Checkpoint B implements bounded execution and manually triggered refresh;
see [Checkpoint B](CHECKPOINT_B.md) for its acceptance evidence and operations.

## Audit and reuse (2026-10-16)

The local checkout began clean on `main` at `d8c9e77`, equal to the local
`origin/main` tracking ref. No remote fetch was needed for this audit.
The history contains six implementation commits:

| Phase | Commit | Reusable foundation |
| --- | --- | --- |
| 1 | `4ef939f` | GitHub client, search/detail/contributors endpoints, discovery/update services, normalized persistence |
| 2 | `bcedb31` | Per-instant snapshot uniqueness, unconditional `record_observation`, pure time-series analytics |
| 3 | `ddd7098` | Profile TTL reuse, public profile snapshots, contributor observation history |
| 4 | `a649d86` | Derived ecosystem graph over stored evidence |
| 5 | `c5e5690` | Evidence fingerprints and cached AI narration |
| 6 | `d8c9e77` | Read-only dataset projections, REST API and React UI |

Pre-Checkpoint-A findings (the baseline, not the current CLI behavior):

- Discovery enriches new repositories with detail, topics, initial snapshot,
  contributors and stale/missing contributor profiles. Known repositories get
  metadata from the search payload, without new snapshots or contributor sync.
- Refresh uses `list_repositories`, ordered newest `last_seen_at` first. A small
  repeated limit can therefore starve older repositories. It uses
  `insert_snapshot_if_changed`, so unchanged polls leave no observation rows.
- `record_observation` already supports unconditional polls without rewriting
  other instants. No analytics rewrite or schema reset is necessary.
- Contributor counts are cumulative; the existing observation history and
  profile refresh TTL should be retained. One observation is insufficient to
  infer growth or recent contribution activity.
- Repository/contributor data commits before profile sync. A stopped run can
  retain a repository with incomplete profile enrichment; reporting must say so.
- The client already handles pagination, transient retries and per-resource
  rate-limit state. Before A, it had no local attempt budget, checked quota only
  before the retry loop, and applied the core threshold of 50 to search too.

Baseline checks: **296 Python unit tests**, Ruff, mypy; frontend ESLint,
TypeScript, **25 tests**, and Vite build passed. Python emitted two existing
Starlette HTTP-422 deprecation warnings. PostgreSQL integration fixtures drop
and recreate schemas, so they were deliberately excluded from this no-reset
checkpoint. The reported port 5433, migration state and empty application
database were not rechecked through a database connection.

## Plan and checkpoint boundary

1. **A:** validated categories/recipes, deterministic local cohort plans,
   dry-run output, shared attempt budgets, focused offline tests and docs.
2. **B (implemented, first pilot verified):** execute reviewed cohorts with deduplication and accurate
   partial-run reports; refresh due repositories and developer profiles using
   the existing storage/observation primitives. See [Checkpoint B](CHECKPOINT_B.md).

No scheduler, live recipe executor, database migration, analytics change, AI
generation or frontend change is part of A. No new dependencies are needed.

## Local planning

From the checkout:

```bash
uv run github-radar discover-plan --as-of 2026-10-16
uv run github-radar discover-plan --config config/discovery.toml \
  --category ai-agents --recipe recent --as-of 2026-10-16
uv run github-radar discover "mcp" --language python --limit 25 --dry-run
uv run github-radar discover "mcp" --contributors-limit 0 --dry-run
```

Both commands output JSON without creating a GitHub client, querying rate
limits, opening a database connection, or calling an AI provider. Credentials
are not required. `discover-plan` is always a dry run; there is no execution
switch. `discover --dry-run` previews the existing single-query command.
Its options, environment budget and search page size match the live path.

`config/discovery.toml` is an editable sample. Categories contain a unique
slug `name` and a nonblank GitHub `query`. Recipes contain a unique `name`,
`min_stars`, optional `max_stars`, optional positive `created_within_days` /
`pushed_within_days`, `sort`, and `order`. Valid sorts are `stars`, `forks`,
`updated`, `help-wanted-issues`, and `best-match`; order is `asc` or `desc`.
Unknown keys, duplicate names, invalid ranges, and unknown selections fail.
Category queries use GitHub syntax; the local validator does not validate the
entire search language or resolve contradictory custom qualifiers.

Selected categories are crossed with selected recipes at equal per-cohort
quotas. `--category` and `--recipe` can be repeated; configuration order is
retained, with categories interleaved within each recipe. Recipe searches add
`is:public archived:false fork:false`. The example separates established
repositories (1000+ stars) from recent and recently active smaller repositories
(10–999 stars). The latter two cohorts can overlap. These are sampling labels,
not measured momentum or developer classifications.

The default creates **9 cohorts / 27 candidate slots / 9 search pages** before
retries. The same config and `--as-of` produce the same plan; omitting the date
uses today's UTC date. Relative dates set lower search bounds, not historical
reconstruction. Search results themselves change over time.

Plans explicitly report unknown overlap, availability and existing database
coverage. Candidate slots are neither unique repositories nor guaranteed
results. No local query can prove a representative or complete ecosystem sample.

Search pages are counted per cohort using ceiling division. An impossible
search budget is rejected, without silently dropping categories. Enrichment
demand assumes every slot is a new repository with the maximum contributors
and uncached, distinct profiles. It is an upper bound before retries, not a
promise of work completed. Demand above caps produces explicit partial-coverage
warnings; it never raises those caps. The sample's 810 potential profile calls
exceed its 30-profile cap.

The API supports at most 100 results per page and 1,000 results per search;
plans validate these bounds. Search uses a separate rate-limit bucket.
See [GitHub search API documentation](https://docs.github.com/en/rest/search/search)
and [repository query syntax](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories).

## Strict request and enrichment budgets

Every `GitHubClient` starts with these hard HTTP-attempt caps:

| Scope | Default | Counts |
| --- | ---: | --- |
| `total` | 100 | Every HTTP attempt, including rate-limit and textual-evidence endpoints |
| `search` | 10 | Search attempts, including every page |
| `repository_details` | 30 | Full repository metadata attempts |
| `contributors` | 30 | Contributor page attempts, not returned contributor rows |
| `profiles` | 30 | Public user profile attempts |

Each endpoint attempt consumes its scope **and** total. Failures, 404s,
transport failures and retries count; blocked attempts do not. Zero disables
that scope, including total=0. Negative, non-integer and unknown budget values
are rejected. Counters are reserved synchronously before the HTTP await, and
can be shared across clients with one `RequestBudget` object. They never refill
when GitHub's rate-limit window resets. Existing commands use one client per run;
API textual-evidence clients have a budget per client/request, not per server.

For existing commands, configure the validated JSON environment setting:

```dotenv
GITHUB_REQUEST_BUDGET='{"total":100,"search":10,"repository_details":30,"contributors":30,"profiles":30}'
RATE_LIMIT_PAUSE_THRESHOLD=50
SEARCH_RATE_LIMIT_PAUSE_THRESHOLD=2
```

Partial JSON objects retain defaults for unspecified scopes. The TOML `budget`
table uses the same schema, but describes the **recipe plan only**; it does not
silently change existing `discover`/`update` commands. `discover-execute` uses
the reviewed JSON plan's budget across all cohorts. Enrichment is a separate
explicit invocation with its own caps. CLI JSON overrides retain unspecified
environment caps; a reviewed plan is standalone and uses its own explicit caps.

The TOML `[execution]` table adds `max_candidates` (default 100),
`max_new_repositories` (30), and `max_seconds` (120). Discovery stops partially
when a cap prevents remaining work; it does not silently raise limits.
`max_repositories` (25) bounds refresh/enrichment selection only. Planning
remains local; limits do not trigger execution. The plan's optional enrichment
cost estimate does not authorize fetching contributors or profiles.

Quota is rechecked before every attempt. Core and search pause thresholds are
independent. Exhaustion raises `RequestBudgetExceeded` before another request;
CLI discovery/update exit nonzero with counters and a partial-enrichment notice.
Already committed repositories remain, and pending transactions close without
commit. Budget exhaustion does not imply successful or complete collection.
Contributors/profile budgets bound requests, not a guaranteed number of fully
enriched repositories; retries can consume them sooner.

## Checkpoint B implementation

The [operations and acceptance guide](CHECKPOINT_B.md) describes stable-ID
deduplication, query provenance, explicit enrichment, stale-first refresh,
incremental commits, migration 0005 and honest partial-run reporting.
Automated validation uses fake GitHub responses and the guarded
`github_radar_test` database. The user's first live pilot completed successfully;
its receipt, five observations and overlapping-category provenance were then
verified through read-only application-database queries. No further collection
was run during acceptance. Checkpoint C and later phases have not started.

## Validation commands

Checkpoint A result: **343 Python unit tests passed** (47 added cases), Ruff
passed, mypy passed for 120 source files, and `git diff --check` passed. The two
baseline Starlette deprecation warnings remain. Frontend checks above were run
before implementation; no frontend files were changed.

```bash
uv run pytest tests/unit -q
uv run ruff check .
uv run mypy .
cd web
npm run lint
npm run typecheck
npm test
npm run build
```

New tests cover deterministic plans, invalid configuration, bounds, selection,
enrichment shortfalls, zero-IO CLI paths, HTTP retries/pagination, all budget
scopes, shared clients, custom base paths and independent quota thresholds.
