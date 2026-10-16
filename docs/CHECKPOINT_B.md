# Checkpoint B — Bounded Discovery & Safe Refresh

Implemented and verified with fake-HTTP tests and the user's first live pilot.
Acceptance checked application data through a read-only transaction; no further
collection was performed. Checkpoint C, Phase 7 and Phase 8 have not started.

## Architecture and reuse

`CollectionService` coordinates the existing GitHub client, repository discovery,
repository update, developer sync and storage primitives. The new staging
methods leave transaction ownership with collection, so each completed
repository, contributor batch or profile commits with its result checkpoint.
`record_observation` and the existing developer/contributor observation helpers
retain their per-instant behavior. Analytics, API projections and frontend code
are unchanged. No dependencies or background framework were added.

Concurrency is **one operation**. CLI collection commands hold a session-level
PostgreSQL advisory lock on the same pinned connection used for persistence;
another collection process exits before HTTP. Direct legacy Python service
callers do not acquire this CLI lock or create durable collection receipts.

The acquisition transaction commits before an `AsyncSession` is bound to that
connection. Incremental commits remain independent, and HTTP awaits leave no
open database transaction. Every session commit verifies advisory-lock ownership
on its own backend, including the successful terminal receipt. If that backend
dies between the check and commit, its transaction cannot commit. Replacement
connections are refused for the rest of the run; ownership is never silently
reacquired. SQLAlchemy disconnects and explicit invalidation cannot route writes
through a new unlocked backend.

On ownership loss, the CLI exits 1 with the run ID and directs the operator to
`collection-runs`. It does not rewrite receipts through another connection or
attempt to unlock on a replacement backend. Previous legitimate commits remain;
an unfinalized receipt stays `running` and is displayed as partial/unfinished.
Normal completion, authentication failure and cancellation release ownership.
Success output follows cleanup. An already-issued HTTP request may finish after
ownership disappears, but its resulting work cannot be committed.

## Checkpoint A acceptance audit

- Every HTTP attempt passes the same client's request budget before transport,
  including pagination, retries, failures and rate-limit requests. Scoped caps
  and the total cap are checked together. A blocked attempt consumes nothing.
- Search and core rate-limit state are independent. Endpoint classification
  handles a repository owner named `search`; response resource headers and the
  rate-limit endpoint update the appropriate state. Quota is checked before
  each retry. External-origin requests and changed pagination endpoints fail.
- Local planning and all dry-runs create neither HTTP clients nor DB engines.
  Fake-HTTP tests make accidental requests fail rather than reaching GitHub.
- TOML/JSON caps reject unknown keys, negative/noninteger values and invalid
  ranges. Zero disables a request scope. CLI overrides preserve unspecified
  environment caps. Reviewed plan execution uses the plan's standalone caps.
- Loading a reviewed plan validates its schema and recomputes derived totals
  and warnings. This detects inconsistent edits; it is not a signed approval
  mechanism. Review the exact JSON file before explicit execution.

## Commands

Local-only commands (safe without credentials):

```bash
uv run github-radar discover-plan --config config/discovery.toml \
  --category ai-agents --recipe recent --as-of 2026-10-16 > reviewed.json
uv run github-radar discover-execute reviewed.json --dry-run
uv run github-radar discover mcp --limit 6 --max-new-repositories 3 --dry-run
uv run github-radar enrich owner/repo --max-profiles 2 --contributors-limit 5 --dry-run
uv run github-radar refresh --limit 5 --min-refresh-hours 24 --dry-run
```

Live command syntax below is documentation only; running it requires explicit
collection authorization and application migration 0005 first:

```bash
uv run github-radar discover-execute reviewed.json
uv run github-radar discover mcp --limit 6 --max-new-repositories 3 \
  --budget '{"total":12,"search":3,"repository_details":6,"contributors":0,"profiles":0}'
uv run github-radar enrich owner/repo --max-profiles 2 --contributors-limit 5 \
  --budget '{"total":8,"repository_details":2,"contributors":2,"profiles":2}'
uv run github-radar refresh --limit 5 --min-refresh-hours 24 --max-seconds 60
uv run github-radar collection-runs --limit 10
uv run github-radar collection-runs --run-id RUN_ID
```

`discover-plan` always plans locally. `discover-execute` executes the saved
cohorts verbatim; it does not regenerate dates or silently change caps.
`discover` retains the single-query options and uses the same executor.
Its `--limit` counts examined candidates, including duplicate IDs. Its retained
`--contributors-limit` affects only the optional enrichment cost preview.
`update` remains a command and now aliases bounded stale-first `refresh`.
Existing read, analytics, graph, AI and serve commands remain registered.

`enrich` requires `--max-profiles`; zero requests contributor relationships
only. Its effective profile-attempt cap is the smaller of that option and the
configured profile budget. Positive caps can be consumed by retries, so they
do not guarantee that many successful profiles. At most 25 explicit repository
references are accepted. Refresh defaults to 25 repositories and a 24-hour
minimum interval. Dry-run refresh reports selection rules, without reading
which repositories are currently due.

## Discovery, identity and provenance

The executor follows catalog order, interleaving categories within each recipe.
It reduces page size to the remaining candidate allowance and deduplicates
GitHub repository IDs across every category and recipe. A duplicate adds its
provenance without repeating detail requests. A known ID reuses rich metadata,
topics and historical observations; its owner/name can follow a rename. A new
ID gets metadata, topics, counters and one initial observation. Detail identity
must match search identity. Name collisions or identity mismatches fail closed.

Provenance stores repository ID, category, recipe, actual query and query hash,
first/last discovery time and latest run ID. Its composite key prevents duplicate
provenance rows on a rerun. Each durable run retains the reviewed plan, including
sort/order and limits. Query provenance is separate from GitHub topics.

`candidates_examined` counts every yielded search result. The unique count is
distinct examined GitHub IDs, including known IDs and failed saves; the separate
new/reused counters count completed persistence work. **40 candidates do not
promise 40 unique IDs or 40 new repositories.** Short or overlapping cohorts
are not automatically backfilled with extra calls. Search `incomplete_results`
stops the run partially before consuming that page's candidates.

## Budgets and enrichment

All calls share `total`, `search`, `repository_details`, `contributors` and
`profiles` caps for the invocation. Discovery also enforces `max_candidates`,
`max_new_repositories`, and `max_seconds`. `max_repositories` applies to
refresh/enrichment selection only. Defaults are in the reviewed plan and
`config/discovery.toml`; CLI operations print their effective limits in dry-run.
Configured target limits define the requested work; a cap that prevents its
completion yields a partial report. There is no automatic budget refill.

Discovery never enriches. Explicit enrichment fetches a bounded contributor
batch, commits relationships/observations and its checkpoint, then processes
profiles individually. Fresh profiles are reused by stable developer ID across
repositories, with no profile HTTP request or synthetic history row. Completed
contributor batches (even an empty 204 response) are reused within the same
`PROFILE_REFRESH_DAYS` TTL, provided they cover the requested row limit.
Increasing that limit requires a new batch. The default TTL is seven days;
zero forces refresh. Incomplete pagination is not marked a completed batch.

The runtime deadline covers collection work, including retries and their waits.
Rollback, final report persistence and resource cleanup occur afterward and can
extend wall-clock duration. GitHub quota pauses, authentication errors and
incomplete searches stop work. Individual repository/profile errors are recorded
and remaining bounded items may continue; the run still finishes partial.

## Refresh and observations

Refresh selects by the latest repository observation timestamp: never-observed
first, oldest next, stable GitHub-ID tie-breaker. `last_seen_at` metadata touches
do not make old observations fresh. Rows must meet the minimum refresh interval.
Each successful detail poll updates mutable metadata/topics and invokes
`record_observation`, even if every counter is unchanged. New timestamps append;
the exact same instant reuses/corrects its row. Other timestamps are preserved.
A refresh clock older than the newest stored observation fails before HTTP.
The report counts actual inserted observations, not merely fetched repositories.

## Interruption, recovery and reporting

`collection_runs` is a receipt, not a job queue. Successful work and its result
checkpoint commit incrementally. HTTP attempt reservations commit **before**
transport separately, so a power loss between reservation and send can
conservatively overcount requests. `request_accounting` states this explicitly.
Counters never claim that a response was received just because an attempt was
reserved. Database/HTTP failure rolls back the current staged operation and
preserves previous commits.

A graceful interruption produces a partial report. Power loss can leave a
`running` receipt. `collection-runs` labels it unfinished and `partial: true`,
without guessing whether it is still active. Its duration is the last checkpoint
and `duration_is_final: false`; finalization records the full elapsed duration.
Inspect that run before explicitly invoking another. A rerun gets a new run ID
and budget, repeats search as necessary, reuses saved repositories/observations,
and retries missing enrichment while reusing fresh completed batches/profiles.

Every execution reports candidates, distinct IDs, known/new repositories,
refreshed repositories, repository/contributor/developer observation counts,
profile fetches/reuses, scoped HTTP attempt counts, completed/skipped/failed
operations, exhaustion, stop reason, partial status and elapsed duration.
Per-operation outcomes distinguish budget exhaustion from failures and skips.
Partial runs exit **1**; invalid command/plan input exits nonzero (normally 2).
Only fully completed requested work gets `status: completed`. A process killed
before finalization cannot emit a final report; its committed receipt is the
recovery evidence. If the database is unavailable, the CLI reports that failure
and directs the operator to inspect receipts once connectivity returns.

## Schema and verification

Additive migration **0005** adds two nullable contributor checkpoint columns,
`collection_runs`, and `discovery_provenance`. Migrations 0001–0004 are unchanged.
The application database is at revision 0005 after the user's pilot setup.
Only the guarded test database was recreated during automated verification;
the existing safety guard was not weakened. Upgrade from populated
0004 and downgrade/re-upgrade preserve repository observations. Downgrading 0005
does remove its new receipts/provenance/checkpoints, as expected.

Verification commands:

```bash
uv run --env-file .env pytest --tb=short
uv run ruff check src tests migrations
uv run mypy src tests
git diff --check
```

`--env-file .env` exposes the configured `TEST_DATABASE_URL` to the integration
fixtures. They validate the exact allowed database name and separation from
`DATABASE_URL` before destructive operations. All HTTP uses fake responses.
The collection scenarios run both on in-memory SQLite and PostgreSQL; CLI
integration also exercises existing repository/analytics/developer/graph/AI-status
read commands against the fixture data. Migration and API tests cover the
existing paths. Real AI generation and serving a live browser were not invoked.

Pre-lock-fix result (2026-10-16): **417 tests passed in 30.86 seconds**, comprising
391 unit tests and 26 PostgreSQL integration tests, with no skips. This includes
19 new PostgreSQL collection/migration/CLI cases. Ruff passed; mypy passed for
120 source files; `git diff --check` passed. Three deprecation warnings remain:
one Alembic configuration warning and two existing Starlette HTTP-422 warnings.
Frontend files are unchanged; the earlier Checkpoint A baseline passed lint,
typecheck, 25 tests and build, and was not rerun for B.

Lock-ownership fix result (2026-10-16): **427 tests passed in 31.10 seconds**,
comprising 391 unit tests and 36 PostgreSQL integration tests, with no skips.
The 10 permanent ownership cases passed independently first. They cover backend
termination, explicit invalidation/reconnection attempts and explicit unlock,
both during HTTP and before terminal persistence; a competing collector
completes while the interrupted collector cannot commit further work. They also
cover release on completion, authentication failure and cancellation. Barriers
use events, not timing sleeps. The regression failed before the fix because the
original collector committed a second repository after losing ownership.
Ruff passed; mypy passed for 121 source files; `git diff --check` passed.
The same three deprecation warnings remain. This fix changes no migrations.

Final pilot acceptance repeated the full suite: **427 passed in 31.78 seconds**,
including all 36 PostgreSQL integration cases, with no skips. Ruff and mypy
passed again. The 29 commit candidates contained no detected secrets, environment
files, dumps or generated artifacts; migrations 0001–0004 and the PostgreSQL
safety guard remained byte-identical to the Phase 6 baseline.

## Changed files

Checkpoint A and B form one change based on `main` at `d8c9e77`.
Full inventory relative to the repository root:

| Area | Files |
| --- | --- |
| Configuration | `.env.example`, `config/discovery.toml`, `src/github_radar/config/settings.py` |
| Discovery | `src/github_radar/discovery/planning.py`, `src/github_radar/discovery/service.py` |
| GitHub | `src/github_radar/github/budget.py`, `src/github_radar/github/client.py`, `src/github_radar/github/repositories.py` |
| Services | `src/github_radar/services/collection.py`, `src/github_radar/services/developers.py`, `src/github_radar/services/update.py` |
| Storage / migration | `src/github_radar/storage/collection.py`, `src/github_radar/storage/models.py`, `migrations/versions/0005_bounded_collection.py` |
| CLI | `src/github_radar/cli/app.py` |
| Unit tests | `tests/unit/test_cli_help.py`, `tests/unit/test_collection.py`, `tests/unit/test_collection_cli.py`, `tests/unit/test_discovery_planning.py`, `tests/unit/test_github_budget.py`, `tests/unit/test_github_repositories.py` |
| PostgreSQL tests | `tests/integration/test_bounded_collection.py`, `tests/integration/test_collection_lock.py`, `tests/integration/test_postgres_flow.py` |
| Documentation | `README.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPERS.md`, `docs/DATA_EXPANSION.md`, `docs/CHECKPOINT_B.md` |

## First live pilot — completed and verified

The user executed a standalone reviewed plan with these bounds; the broader
sample catalog was not used unchanged:

| Setting | Pilot bound |
| --- | --- |
| Categories | `ai-agents` (`topic:ai-agents`), `developer-tools` (`topic:developer-tools`) |
| Recipe | `recent`: created within 365 days, 10–999 stars, updated descending |
| Filters | public, nonarchived, nonfork |
| Candidates | 3 per category, at most **6 examined total** |
| New repositories | at most **6**; overlap/known IDs may reduce this |
| HTTP attempts | **16 total**, search **4**, repository details **12** |
| Contributors / profiles | **0 / 0** attempts; **0 profiles fetched** |
| Concurrency / deadline | **1** / **120 seconds** of collection work |
| Observed duration | **3.805 seconds** for this run |

Read-only acceptance verification confirmed run
`88c9195e-fa20-4904-903f-b8cadb836945` has a persisted `completed` receipt,
`partial: false`, six examined candidates, five unique new repositories, five
observations, one skipped duplicate ID and zero failures. It consumed seven
HTTP attempts (two search, five details), with zero contributor/profile calls.
All five repositories are public, with one initial observation apiece.

`ai-sdlc-framework/ai-sdlc` has exactly one repository row for GitHub ID
`1152413600` and both `ai-agents/recent` and `developer-tools/recent` provenance
rows, with their distinct queries/hashes and the same run ID. Across the pilot,
six provenance rows preserve all six discoveries. The duplicate skip avoids a
second repository/detail request without discarding the second category. No
provenance repair was needed; existing cross-recipe and restart tests exercise
this behavior on SQLite and PostgreSQL.

For future explicitly authorized runs, stop when requested cohorts finish, a
request/runtime/candidate/new-repository cap prevents more work, quota reaches
its configured threshold, authentication
fails, or search reports incompleteness. Item failures produce a partial report
while remaining bounded items may finish. Review any partial/failure before a
new invocation; do not automatically retry or enlarge caps. No enrichment or
refresh is included in this first pilot. A single observation establishes no
growth signal.

## Known limits

- There is no search-page cursor or automatic resume. Search can repeat on an
  explicitly authorized restart; stable IDs and checkpoints prevent duplicate
  saved entities, initial observations and fresh enrichment.
- Tight global caps can stop before later cohorts; equal requested quotas do
  not guarantee equal realized coverage. No unseen skipped-item count is invented.
- Repository renames found through discovery update identity names. A stale
  refresh name that redirects, disappears or is reused fails safely; automatic
  redirect/rename repair is not implemented.
- Request receipts measure reservations, not exact wire sends after a crash.
  An unfinished run's true final duration and in-flight response are unknown.
- Contributor and profile reuse share the existing TTL. Request budgets cap
  attempts, not successful entities. A new manual invocation has a new budget.
- Report operations/JSON are bounded by configured work limits; no long-term
  receipt retention service or background collection is implemented.
