# Phase 8B — Autonomous collection

Status: local implementation for operator review. **Live automation is disabled by
default. No production database, secrets, scheduled collection or deployment was
configured or exercised.** Phase 8A public/private boundaries remain in force.

## Architecture and reuse

The baseline is `53ba7dd6b7ddc55d76e388313c7329db2a24c636` on `origin/main`,
including merged PR #2 and successful [hosted CI](https://github.com/zinverno/github-radar/actions/runs/40703919877).

`github-radar automation run refresh|discover` is a scheduling wrapper around
the existing `CollectionService.run`. It never calls the legacy unbounded update
path and does not implement another collection loop. The existing CLI registers
the automation group and skips application `.env` loading/debug logging for that
group; automation uses only its explicit collector environment configuration.

| Existing mechanism | Reused behavior |
| --- | --- |
| `storage.collection.locked_collection_session` | One dedicated engine/backend per invocation; PostgreSQL session advisory lock 6742565, commit ownership checks, no reconnection after ownership loss |
| `CollectionService` | Serial work, per-operation commits, durable receipts, bounded execution, partial results and cancellation |
| `RequestBudget` and `GitHubClient` | Every HTTP attempt, including retries, reserves budget durably before transport; rate-limit stops and authentication failures propagate |
| `stale_repositories` and `stage_observation` | Never observed first, then oldest observation and stable GitHub ID; each successful poll produces an observation even when counters are unchanged |
| `DiscoveryConfig`, `plan_discovery`, `DiscoveryPlan` | Existing TOML-to-JSON plan contract, derived totals, equal cohort quotas and bounded lightweight discovery |
| `load_quality_report` and persisted receipts | Read-only freshness, observation coverage, costs and partial/unfinished evidence |

No schema or migration changes. Scheduling deduplication reads existing receipts
under the collection lock: any attempt of the same kind in the current UTC period
prevents another automatic run, regardless of its outcome. Refresh periods are UTC
calendar days; discovery periods are Monday–Sunday UTC weeks. Manual collection
receipts also count. Thus a manual dry run does not reserve a period, but a live
attempt with no work does. Receipt creation still precedes any HTTP.

An unfinished receipt blocks automation until explicitly acknowledged by an
operator. Acknowledgment does not change the receipt or mark it completed.
The database lock remains the final guard across Actions, manual CLI invocations,
other hosts and independent schedulers. Each invocation must use its own engine;
sharing that engine across invocations violates the existing lock guard's contract.

## Schedules and quotas

| Bound | Daily refresh | Weekly discovery |
| --- | --- | --- |
| Proposed cron, UTC | `17 3 * * *` (03:17 daily) | `43 4 * * 0` (04:43 Sunday) |
| Selection | Existing stale-first selection, age at least 24h | `config/automation-discovery.toml` |
| Repositories | At most 100 refreshed | At most 10 new |
| Candidate slots | No search | 18, at most 2 per cohort across 9 cohorts |
| Total HTTP attempts | 120 | 36 |
| Detail attempts | 120 | 24 |
| Search attempts | 0 | 12 |
| Contributor/profile attempts | 0 / 0 | 0 / 0 |
| Collection deadline | 300 seconds | 300 seconds |

These are caps, not goals or promised yield. Discovery candidate slots can overlap
across categories/recipes, refer to existing IDs, or return fewer results. Hitting
a candidate/new-repository/request/deadline cap before finishing the plan produces
a **partial** receipt and a failing collection step. A completed refresh means its
bounded selection finished, not that every tracked repository is fresh.

The discovery catalog deliberately retains the existing category queries and
recipe semantics; it is not a new discovery algorithm or a semantic relevance
fix. The operator must review the entire catalog and set its SHA-256 approval.
Changing any byte invalidates that approval. Date-relative recipes are compiled
using the actual invocation's UTC date. The JSON preview and full plan stored in
the existing receipt show the resulting queries, budgets and derived totals.
There is no workflow query, path, branch or repository-list input.

The wrapper has an additional 330-second execution timeout to cover preflight and
cleanup; the collector's 300-second deadline remains unchanged. Database connection
timeout is 10s; statement/command timeout 15s; database lock wait 5s. Actions plan
jobs have a 3-minute timeout and collection jobs an 8-minute timeout including
installation, diagnostics and cleanup. No whole-run retries are performed.

## GitHub Actions setup and activation (operator-only, not performed)

The two workflows are `collection-refresh.yml` and `collection-discover.yml`.
Both have `contents: read`, pinned checkout/setup actions and disabled checkout
credential persistence. Only `schedule` and `workflow_dispatch` events exist.
Both jobs require this repository and `refs/heads/main`; no PR or other branch is
eligible for the secret-bearing job. Manual dispatch defaults to dry-run.
Collection steps explicitly use `shell: bash`, enabling `pipefail` so `tee` cannot
turn a collector failure or interruption into a successful Actions step.

1. Review and merge this implementation through normal CI. Do not activate while
   the infrastructure/TLS/permissions checks below remain unverified.
2. Create a protected GitHub Actions **environment named `collection`**, restricted
   to the `main` deployment branch. Protect changes to main/workflows with reviews.
   Environment-level secrets are mandatory; do not put collector secrets in
   repository-wide secrets accessible to other workflow refs. Required environment
   reviewers are an optional extra approval gate, but would also pause scheduled runs.
3. Configure the dedicated database role and direct connection described below,
   migrate separately with an operator account, and verify backup/recovery before
   enabling writes. The scheduler never runs migrations.
4. Add environment secrets:

   | Secret | Purpose |
   | --- | --- |
   | `COLLECTOR_DATABASE_URL` | `postgresql+asyncpg://github_radar_collector:<URL-encoded-password>@<direct-host>:5432/<database>`; no URL query parameters |
   | `COLLECTOR_GITHUB_TOKEN` | Separate GitHub credential restricted to reading public repository metadata; no private-repository access or write scopes needed |
   | `COLLECTOR_CA_PEM` | Optional provider CA PEM when system roots do not verify the database certificate |

   The Actions `GITHUB_TOKEN` is not substituted for the collector token. AI and
   private-workspace credentials are neither configured nor required.
5. Set repository variables only after review:

   | Variable | Default / activation meaning |
   | --- | --- |
   | `RADAR_COLLECTION_LIVE` | Missing/anything except exact `true` disables all live runs |
   | `RADAR_REFRESH_ENABLED` | Exact `true` enables only refresh |
   | `RADAR_DISCOVERY_ENABLED` | Exact `true` enables only discovery |
   | `COLLECTOR_DIRECT_CONNECTION` | Exact `true` is the operator's attestation that the endpoint is direct PostgreSQL, not transaction pooling |
   | `RADAR_DISCOVERY_PLAN_SHA256` | SHA-256 of reviewed `config/automation-discovery.toml`, required only for discovery |
   | `COLLECTOR_ACK_UNFINISHED_RUNS` | Normally empty; comma-separated UUIDs only after the interruption review below |

6. Run manual dispatch on `main` with `live=false` for each workflow. Inspect the
   plan. Local secret-free equivalents are:

   ```sh
   uv sync --frozen
   uv run --frozen github-radar automation run refresh
   uv run --frozen github-radar automation run discover
   sha256sum config/automation-discovery.toml
   ```

7. Enable refresh first, then explicitly dispatch `live=true` with the two refresh
   activation variables set. Review receipt/status output and verify actual
   observations. Enable discovery separately only after approving its plan digest.
   Once enabled, cron triggers request live execution; manual dispatch still defaults
   to dry-run. `GITHUB_RUN_ATTEMPT` must be `1`: re-running a workflow is not a recovery
   mechanism. Use a new dispatch after investigating, subject to the period guard.

The plan job's preview blockers describe its isolated, secret-free process;
the separate gate summary is the workflow's live-execution decision.
With gates closed, schedule events can still produce a secret-free plan/skip
summary; they do not read the application database or make collection requests.
Actions concurrency group `github-radar-collection` is shared by both workflows,
with `cancel-in-progress: false`. GitHub may replace pending jobs; it is not a
durable queue. Environment configuration and branch protection cannot be established
by repository YAML alone and have **not** been provisioned here.

## PostgreSQL connection and minimum grants

Use external PostgreSQL with a **direct, session-stable endpoint**. PgBouncer
transaction/statement pooling is unsupported. The wrapper uses a dedicated
non-pooling SQLAlchemy engine with an explicit SSL context requiring certificate
verification, hostname checking and TLS 1.2 or newer. URL SSL overrides are rejected.
System trust roots are used, optionally extended by `COLLECTOR_CA_PEM`.
An endpoint name or a successful connection cannot prove absence of a proxy;
the operator must confirm topology with the provider. Lock-loss checks still apply.

Do not reuse the public read-only web role or any owner/migration/admin account.
The runtime requires the exact role `github_radar_collector`, no administrative
role attributes, no schema creation, schema version 0006, required collection
grants/sequence usage, and **no privileges** on either workspace table or AI artifacts.

Future owner-run setup template, for the intended external database only; **not
executed by this phase or the workflow** (set the password interactively via
`\password github_radar_collector`, never paste credentials into a SQL file/log):

```sql
CREATE ROLE github_radar_collector LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Grant CONNECT on the intended database separately if PUBLIC CONNECT is revoked.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO github_radar_collector;
GRANT SELECT ON repositories, repository_snapshots, topics, repository_topics,
  discovery_provenance, collection_runs, developers, developer_snapshots,
  repository_contributors, repository_contributor_snapshots, alembic_version
  TO github_radar_collector;
GRANT INSERT, UPDATE ON repositories, repository_snapshots, topics,
  discovery_provenance, collection_runs TO github_radar_collector;
GRANT INSERT, DELETE ON repository_topics TO github_radar_collector;
GRANT USAGE ON SEQUENCE repositories_id_seq, repository_snapshots_id_seq,
  topics_id_seq TO github_radar_collector;
```

Confirm the sequence names against the actual schema. Do not grant membership in
an owner/admin role, schema ownership, broad table/sequence privileges, or automatic
grants on future private tables. Revoke inherited/PUBLIC privileges on private/AI
tables if necessary. Diagnostics need public developer/contributor reads; collection
does not write them. Snapshot updates are retained for the existing same-timestamp
observation contract; topic DELETE is required to remove obsolete associations.
Association rows need INSERT/DELETE, not UPDATE. Phase 8C-A additionally audits
effective table/column/sequence grants, PUBLIC defaults, memberships/SET ROLE paths,
ownership and executable user-defined functions, and refuses known Neon pooler
hosts. Use [the current role setup and TLS runbook](PUBLIC_DEPLOYMENT.md), including
revocation of PUBLIC database CREATE/TEMP. External provider grants/topology still
require operator verification.

## Monitoring, stopping and recovery

`github-radar automation status` reads a REPEATABLE READ, READ ONLY snapshot using
`COLLECTOR_DATABASE_URL` and verified TLS. It does not make GitHub requests. Output:

- Last successfully completed bounded refresh and most recent discovery (including status).
- Current freshness, repositories due at the 24h cutoff and existing quality window coverage.
- HTTP attempt reservations and up to 20 recent/problem receipts, partial and budget totals.
- Unfinished run IDs and missing UTC observation days over the last seven complete days,
  starting no earlier than each repository's first-seen day.
- Configuration/activation blockers for the diagnostic process. Local diagnostics will
  correctly report that they are not a trusted Actions execution context.

Attempts are reserved before transport, so crashes can overcount sent requests.
Missing-day coverage is calendar-day presence, not proof of evenly spaced polling.
No backfill observations are fabricated. Delayed daily cron plus the 24h minimum
can skip a repository on the next day; assess coverage rather than assuming daily
continuity. A maximum of 100 stale repositories per run can leave a backlog.

Collection step JSON distinguishes `completed`, `partial`, `blocked`, `skipped`,
`failed` and `interrupted`; duplicate skips include the earlier receipt/status.
Successful Actions steps can mean an explicit skip, not a completed collection.
Failure/cancellation summaries run with `always()`, as do bounded read-only
diagnostics when collection was attempted. Pre-receipt failures and skipped jobs
exist only in Actions evidence, not as invented collection receipts. A hard runner
kill can prevent even the summary step; inspect Actions status and the next diagnostic.
Configure operator Actions failure notifications externally; this phase sends no messages.

| Failure | Behavior / operator action |
| --- | --- |
| Rate exhaustion / auth failure | Existing client stops; receipt is partial, collection exits nonzero; investigate quota/token, no whole-run retry |
| Request cap / partial repositories / deadline | Previously committed observations retained; partial never becomes completed |
| Lock contention | No receipt/HTTP for losing process; inspect the owner, do not bypass lock |
| PostgreSQL disconnect or lock loss | Existing ownership guard refuses reconnect/commit; last durable receipt may remain running/unfinished |
| SIGTERM / task cancellation | Cancel the task; existing collector attempts a partial receipt on its owned backend |
| SIGKILL / host failure | Receipt remains unfinished; next automatic run blocks until review |
| Job never started / scheduler skipped it | Check Actions and freshness; no receipt or observation is fabricated |

Pause future collection by setting `RADAR_COLLECTION_LIVE=false`, or disable only
one schedule with its enable variable. Disabling the workflow in Actions is an
additional stop control. These changes do not revoke an already running job's
environment; cancel that job explicitly. Cancellation is not completion.

After an interruption: pause automation, inspect Actions and `collection-runs` /
`automation status`, confirm the old process is stopped and no lock owner remains,
and inspect committed observations and reserved attempts. Fix the underlying fault.
For an unfinished receipt, explicitly put its exact UUID in
`COLLECTOR_ACK_UNFINISHED_RUNS` after review. The next invocation still acquires the
normal lock and refuses another attempt in the same UTC period. The old receipt
remains unfinished forever as honest evidence; remove the acknowledgment only if
you intend to block again. Acknowledgment is not permission to replay an old run
or manufacture a success. Manual exceptional recovery remains an operator decision.

## Cost assumptions and limitations

At the stated maxima: 120 refresh HTTP attempts per day and 36 discovery attempts
per week, with no AI, contributor or profile calls. Most current 25-repository
refreshes should consume about 25 detail attempts absent retries; this is an estimate,
not a guarantee. GitHub's shared quota and secondary limits can stop runs earlier.
External PostgreSQL storage/egress and Actions allowances depend on the operator's
account/provider; no paid infrastructure was selected or provisioned.

[GitHub documents](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
cron delays/dropped jobs and automatic schedule disabling after 60 inactive days
for public repositories. This is best-effort scheduling, not exactly-once delivery
or an availability SLA. A free database can sleep, exhaust connections/storage,
require IP allowlists or lack an appropriate direct TLS endpoint. Those are release
blockers to verify before activation, not reasons to relax TLS or lock requirements.

## Verification and release state

- **IMPLEMENTED:** disabled-by-default workflows, independent activation gates,
  approved-catalog digest, existing collector/lock/receipt reuse, duplicate-period
  refusal, TLS/role preflight, interruption handling and read-only diagnostics.
- **VERIFIED:** see the final verification record below for exact local counts.
  Tests use fake GitHub responses and a guarded disposable `github_radar_test`,
  never the application database or production credentials.
- **NOT VERIFIED:** hosted execution of these new workflows, external PostgreSQL
  TLS handshake/topology, actual GitHub quota behavior, real SIGKILL/runner loss,
  provider outage recovery, notifications, real scheduled delivery and billing.
- **BLOCKED:** live activation until operator review/merge, green hosted CI,
  protected environment/branch configuration, dedicated role/direct verified TLS,
  credentials, backup/recovery evidence and explicit schedule activation.
  Phase 8A deployment blockers and `PRE-BETA-UX-001` remain separate and unchanged.

### Local verification record

Verified locally on 2026-10-28:

| Gate | Result |
| --- | --- |
| Full Python suite with guarded PostgreSQL | **838 passed**, zero skipped/failed; **72 integration cases**, including 19 new scheduling cases |
| New scheduling unit/security tests | **65 passed**, included in the full suite; actual workflow gate scripts, pipeline exit codes, alternate-ref/PR/rerun refusal, missing inputs, digest changes, invalid plans, credential redaction, TLS context and real SIGTERM with a fake worker |
| Ruff / mypy | Passed; mypy checked **144 files** |
| Frozen Python sync | Passed with uv 0.11.6 |
| Locked Python audit | Zero known vulnerabilities/adverse statuses in **46 packages** |
| Frontend lint / typecheck / tests / build | Passed; **53 tests**, 9 files; frontend source and lockfile unchanged |
| Full / production npm audits | **0 / 0 vulnerabilities** |
| Workflow YAML and expressions | actionlint **1.7.12** passed; YAML security-contract regressions passed |
| Workflow shell syntax | `bash -n` passed for all 37 workflow shell scripts; standalone ShellCheck was not installed locally |
| Actual console dry-run commands | Refresh and discovery both passed with an invalid application URL and empty provider credentials; no database/HTTP connection |
| Diff / migration preservation | Passed; migrations 0001–0006 and all existing collection, lock, scoring and API implementations remain byte-identical |

The 84 new tests include restricted PostgreSQL grants (including column grants on
private notes), simultaneous invocations on separate engines, duplicate periods,
unchanged counters, GitHub authentication/rate/server errors, exhausted budgets,
timeouts, cancellation, hard-crash unfinished receipts and explicit acknowledgment,
backend termination/unlock, and read-only diagnostics with real observation gaps.
The original 754 tests still pass, including the Phase 8A public/private regressions.
Eleven pre-existing Alembic/Starlette deprecation warnings remain.

Local runtime: Python 3.12.13, PostgreSQL 18.6, Node 24.14.1 and npm 11.11.0;
hosted CI will exercise its configured Python 3.12, PostgreSQL 16 and Node 22.
PostgreSQL tests used a newly initialized loopback-only cluster with a fresh
test credential, an explicit `github_radar_test` guard and an unreachable application
URL. Role tests use PostgreSQL `SET ROLE` after test-admin authentication; external
collector-password authentication and TLS negotiation are not claimed tested.
The disposable cluster was stopped after testing. No application database was read
or modified, and no GitHub collection was performed.

Only development dependencies were added: PyYAML **6.0.3** and types-PyYAML
**6.0.12.20260906**, to validate actual workflow YAML without production secrets.
No existing runtime dependency version changed; certifi remains **2026.10.23**.
No live activation is authorized by these tests.

### Final acceptance review

Two confirmed defects were corrected before finalization:

1. The collection pipelines used GitHub's unspecified shell, allowing `tee` to
   hide nonzero collector exits. Both steps now explicitly select Bash, which
   [enables pipefail](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepsshell).
   Eight regressions execute the real step scripts with a fake collector and
   verify that exit codes 0, 1, 2 and 130 survive output capture. The six failing
   cases reproduced the original defect before the fix.
2. The collector grants unnecessarily included UPDATE on `repository_topics`.
   The template, preflight and test role now require only SELECT/INSERT/DELETE
   for those associations. The PostgreSQL regression checks that UPDATE is absent
   and private-table reads fail with insufficient privilege. Full collection tests
   pass with the reduced grants; no existing collection/storage code was changed.

The full 838-test Python suite, Ruff, mypy, actionlint, all workflow shell syntax,
frozen installation and Python/npm audits were rerun for acceptance. The frontend's
previously verified 53 tests and lint/typecheck/build remain applicable: no frontend
source or dependency changes occurred during acceptance, so these were not rerun.
The index/file audit found only the 12 intended Phase 8B files; `.env` files,
database dumps, caches and generated frontend artifacts remain excluded.

No code-level acceptance blockers remain. Hosted CI and every external setup,
credential, TLS/topology and activation prerequisite above remain unverified or
blocked until the operator performs them. Finalization does not enable either schedule.
