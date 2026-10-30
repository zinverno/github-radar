# Phase 8A — production readiness review

Current PostgreSQL runtime/configuration: [Phase 8C-B migration](PHASE8C_B_MIGRATION_VERIFICATION.md).
The dated acceptance counts below describe their original phase; TLS/SCRAM is now
mandatory on loopback as well as remote connections. PRE-BETA-UX-001 remains open.

For the current Render/Neon deployment procedure and stricter role/TLS requirements,
use [Phase 8C-A public deployment](PUBLIC_DEPLOYMENT.md). This document retains
the historical Phase 8A results and `PRE-BETA-UX-001` record.

Phase 8A prepares a **read-only public beta**. It does not deploy the service,
provision infrastructure, run collection, migrate the application database, or
make the private workspace suitable for public/proxy access. Baseline:
`a587f2ed665aee0a71dd0f47c85bcc643f322a36`, schema `0006`.

## Status and release decision

- **IMPLEMENTED:** explicit public deployment mode, public read allowlist,
  protected local AI generation, secret-free public process configuration,
  restricted database connection, startup/readiness gates, UI capability gate,
  CI and an allowlisted recovery drill.
- **TESTED:** deterministic HTTP security regressions; guarded PostgreSQL tests;
  a complete fresh backup/restore of the actual local database into an isolated
  cluster; public startup and all 23 public route patterns using a SELECT-only
  role against that restored database. Private table reads fail at PostgreSQL.
- **NOT TESTED:** an actual GitHub Actions run, PostgreSQL 16 CI container on the
  hosted runner, external PostgreSQL/TLS, real HTTPS reverse proxy, load/resource
  ceilings, cold starts, browser acceptance at a real public edge or provider-specific free-tier
  behavior. The earlier operator backup archive was not located/replayed; the
  verified restore uses a fresh consistent backup taken during 8A.
- **BLOCKED for release:** CI must run
  successfully on the hosted runner; a deployment operator must configure and
  verify the restricted DB role, TLS/proxy limits, secret handling, external
  backups and the manual checklist below. The local frontend advisory blocker was
  resolved on 2026-10-27; this review does not certify deployment.

The real developer watchlist still has no developers to accept. Local repository
watchlist acceptance from Phase 7 remains valid. Public/proxy workspace access
remains unsupported and is explicitly refused in public mode.

## Verification results

- **TESTED:** 730 Python tests passed, including 53 guarded PostgreSQL integration
  cases, with no skips, and passed again during dependency remediation on a fresh
  isolated cluster on port 15449 (the earlier review used port 15439); the
  original application/test cluster on port 5433 was not migrated/reset.
- **TESTED:** Ruff and strict mypy (140 files), frontend lint/typecheck, all 53
  Vitest tests and the production build, repeated after the security upgrades.
  Current bundle: 248.82 kB JS (77.21 kB gzip), 10.42 kB CSS; no source maps.
- **TESTED:** migration upgrade/downgrade and metadata agreement (`alembic check`),
  including additive 0006 preservation tests. Migrations 0001–0006 are byte-identical
  to the Phase 7 baseline. Scoring, collection logic and lock ownership are unchanged.
- **TESTED:** 14-table row equality after actual local backup/restore; private table
  access denied to the public role; all 23 read route patterns and built index
  exercised against the restored data. No live GitHub/AI request was made.
- **TESTED:** source public-data fingerprints still match Phase 7, with revision
  0006, 25 repositories, 25 observations and 219 topics. Configured-secret scan of
  source/untracked intended files and built assets passed; private env/archive
  permissions and Git exclusions passed. No secrets or private response bodies
  were printed.
- **TESTED:** workflow YAML parse, immutable action references and CI/runbook shell
  syntax. **NOT TESTED:** workflow execution on GitHub; no commit/push occurred
  during those initial verification runs. Local finalization is recorded below.
- **TESTED:** the earlier Python advisory audit was clean after the Mako patch.
  Fresh frontend full and production-only audits now report **zero vulnerabilities**;
  clean install and registry signatures were verified. No gate was suppressed.

The real-data restore and source fingerprints below belong to the preceding 8A
review. They were not repeated during the dependency-only follow-up, which used
only disposable synthetic databases. See the complete
[frontend advisory and dependency review](FRONTEND_SECURITY_REVIEW.md).

**TESTED on 2026-10-27:** isolated Chromium 155.0.8059.79 against the production
bundle and three real FastAPI processes (local admin, public SELECT-only role and
disabled workspace), using a separate synthetic database. Routing, browser history,
deep-link reload, repository analytics (+25.0% growth and delta 3), topic/developer
details, ecosystem and bridges worked. Both watchlists supported empty lists,
saving, notes/status editing, filters and public contacts. Cancel, unsave-with-notes,
confirmed deletion, lock/reload/navigation, invalid authentication and disabled
workspace behavior passed. The public process refused workspace/AI/docs and
excluded private sentinels while those records existed in the same test database.
There were zero unexpected console errors, unexpected HTTP failures or external
browser requests. Tokens/notes were absent from request URLs and browser storage
remained empty.

**PRE-BETA-UX-001 — separate open UX issue:** the populated repository detail layout overflows a 320 px
viewport to 360 px in local and public modes. Workspace passed at 320/768/1440 px;
repository detail passed width checks at 768/1440 px. This is recorded separately
from the successful functional smoke and is not presented as fully passing
responsive acceptance. An isolated rebuild with the original Vite 5.4.21 / Router
6.30.6 lockfile reproduced the same 360 px document width at a 320 px viewport,
against the same synthetic API. It is a pre-existing layout issue; no CSS or
analytics rendering was changed by the upgrades.

A real disconnect/restart of the disposable PostgreSQL cluster was also
**TESTED**: liveness stayed 200, readiness became 503, reads returned generic 500
errors, and connections recovered without restarting the web application. The
disposable cluster was stopped after verification.

## Endpoint and method inventory

The inventory regression checks every documented API path and its methods against
these classifications. Public mode admits **GET/HEAD only**; FastAPI may return
405 for HEAD on GET-only API handlers. OPTIONS and all write/other methods return
405 before dependencies, parsing, database access or provider construction. No CORS
permission is granted. WebSockets are refused. Unknown API paths fail closed.

| Paths under `/api/v1` | Registered methods | Public mode |
| --- | --- | --- |
| `/health`, `/ready`, `/capabilities` | GET | Liveness; bounded DB/schema/role readiness; mode only |
| `/status`, `/explore` | GET | Public dataset/freshness; no AI cache query or provider/model disclosure |
| `/repositories`, `/repositories/summary`, `/repositories/{ref:path}` | GET | Explicit repository serializers and deterministic intelligence |
| `/topics`, `/topics/{name}` | GET | Stored public topics and repository intelligence |
| `/developers`, `/developers/emerging`, `/developers/high-activity`, `/developers/{login}` | GET | Stored public profiles and deterministic intelligence |
| `/ecosystem`, `/ecosystem/nodes`, `/ecosystem/edges` | GET | Deterministic graph reads |
| `/bridges`, `/bridges/repos`, `/bridges/developers/{login}`, `/bridges/repositories/{ref:path}`, `/bridges/cross-topic` | GET | Deterministic graph reads |
| `/search` | GET | Stored public dataset only |
| `/ai/status`, `/ai/artifacts`, `/ai/artifacts/{entity_type}/{entity_key:path}` | GET | Unavailable (404), including cached AI artifacts |
| `/ai/generate` | POST | Refused (405); locally requires the existing admin bearer + direct loopback boundary |
| `/workspace/session`, `/workspace/developers`, `/workspace/repositories` | GET | Unavailable (404), regardless of credentials |
| `/workspace/{kind}/{entity_id}` | PUT, DELETE | Refused (405), regardless of credentials |
| `/docs`, `/openapi.json` under API; root `/redoc` | GET, HEAD framework routes | Disabled and blocked; root `/docs` and `/openapi.json` also blocked |
| `/assets/*`, SPA fallback | GET, HEAD | Built static frontend; `/ai` and `/workspace` shells unavailable |

There are no HTTP collection, refresh, enrichment, migration, scheduler or outreach
endpoints. Only local AI generation and workspace writes mutate through the API.
Collection and migration CLIs remain explicit operator commands.

A pre-existing migration/model discrepancy was also corrected without changing
any migration or database: PostgreSQL merges redundant association-table UNIQUE
constraints into the named composite primary keys. The ORM now declares those
same named primary keys, retaining uniqueness, non-null and FK guarantees.
`alembic check` now passes and is asserted by the migration integration test.
See [PostgreSQL CREATE TABLE](https://www.postgresql.org/docs/18/sql-createtable.html).
The private-endpoint inventory test now handles FastAPI included routers through
OpenAPI enumeration and asserts a nonempty inventory; the old direct route walk
could silently miss them on the current FastAPI version.

## Security boundaries

Settings default to `DEPLOYMENT_MODE=local`. The standard launcher refuses a
non-loopback bind unless public mode is explicitly selected. Public mode requires
an exact nonempty `PUBLIC_ALLOWED_HOSTS` list and refuses nonempty `GITHUB_TOKEN`,
`AI_API_KEY` or `WORKSPACE_ADMIN_TOKEN`. A misspelled mode or malformed host list
fails configuration validation. Do not distribute `.env` or `.env.workspace` to a
public build/runtime; settings automatically reads `.env` if one is present.

HTTP middleware order is Starlette's outer server-error handler, then
`DeploymentBoundary`, then `WorkspaceAccess`, then exception handling/routing.
The deployment boundary validates the raw single Host header and rejects writes
and non-allowlisted API reads before the private boundary or database dependencies.
It does not use forwarded headers, Origin, cookies or query arguments as identity.
Uvicorn proxy-header trust is disabled by the launcher. The HTTPS proxy must
preserve the allowed Host; public reads do not require Origin and gain no browser
CORS permission from it. Do not infer trust from a proxy's loopback connection.

The existing private boundary is retained: one constant-time compared bearer,
actual loopback peer, local Host, matching Origin when supplied, no cross-site
request, and rejection of Forwarded, X-Forwarded-* and X-Real-IP. It now also guards
local `POST /ai/generate`; the React form requires the local admin token, sends it
only as Authorization, refuses redirects, and clears it after completion/pagehide.
The AI caller now uses the backend's snake-case entity query parameters and
passes the bridge form's two topics explicitly; the previous form did not match
the endpoint contract. These requests are tested with mocks, not live providers.
Root-path normalization is shared with Starlette routing. Public mode blocks the
private surface even if application code accidentally injects an admin token.

Public responses carry no-store, no-referrer, nosniff, frame denial and a same-origin
CSP. No private notes/watchlist data is serialized by public routes; tests insert
private sentinels and exercise every public read pattern. Public `/status` avoids
querying AI artifacts. Public startup requires database read-only transactions and
no SELECT grant on either private table; an owner connection is rejected.

The public engine uses a pool of 2 plus 1 overflow, a 3-second pool/connect timeout,
5-second SQL statement timeout and `default_transaction_read_only=on`. The DB role
must independently have only the listed SELECT grants. Connection settings are
not a substitute for PostgreSQL permissions. A superuser/owner is unsuitable.

Settings representations and validation text hide credentials. SQL parameters are
hidden; HTTP failures suppress raw SQL/driver exceptions, notes and connection
strings in application logs and responses. The launcher disables access logs,
which otherwise record query strings. Provider host serialization strips URL
userinfo. Frontend tokens are never build variables, URLs or browser storage;
production source maps are disabled. A proxy/platform log collector is outside
this code's boundary: configure it to omit Authorization, cookies, bodies and
query strings. Keep local logs and OS/browser access admin-controlled; use INFO,
not HTTP/SQL trace logging. Do not pass secrets in command-line flags.

The public process contains no GitHub/AI credentials, has no paid generation
surface and cannot collect. Its anonymous reads can still consume CPU and memory:
graph/list calculations load stored datasets, and pagination does not bound all
internal work. Uvicorn limits active connections/tasks to 20 in public mode, but
this is not a per-client rate limiter or a load guarantee. Edge rate limits,
request-size/time limits and resource testing are release prerequisites.

## Production environment and process layout

Use a dedicated environment, a pinned checkout and Python >=3.12. Build the React
bundle with Node 22.12+ (22.x) or supported Node LTS >=24, and npm 11.11.0 (CI uses
Node 22 and explicitly installs npm 11.11.0). Run **one FastAPI process serving
`web/dist`**, backed by external PostgreSQL. Start from the checkout root: the
current frontend location is repository-relative; a wheel-only installation does
not include it. Do not expose Vite dev/preview servers.

| Variable | Public service value/policy |
| --- | --- |
| `DEPLOYMENT_MODE` | `public` (required explicit opt-in) |
| `PUBLIC_ALLOWED_HOSTS` | JSON array of exact lowercase hostnames, e.g. `["radar.example.org"]`; no wildcards, scheme or port |
| `DATABASE_URL` | `postgresql+psycopg://...` for a dedicated SELECT-only login; external TLS with certificate verification required |
| `GITHUB_TOKEN`, `AI_API_KEY`, `WORKSPACE_ADMIN_TOKEN` | Empty/absent; configuration rejects any nonempty value |
| `AI_MODEL`, `AI_BASE_URL` and AI tuning | Unneeded; omit from public runtime |
| `TEST_DATABASE_URL`, `RESTORE_TEST_DATABASE_URL` | Absent from public runtime; reserved for disposable validation |
| `LOG_LEVEL` | `INFO`; configure platform/proxy log redaction separately |

No `VITE_*` secret variables are used or needed. Store the database password only
in the hosting secret environment. Configure host/TLS outside the application;
start it with `github-radar serve --host 0.0.0.0 --port 8000` only after public
settings are in place, with the upstream port reachable only by the proxy.
The CLI does not read an automatic `PORT` variable: supply the platform's assigned
port explicitly if different. Do not use the raw Uvicorn CLI for deployment: it
would bypass launcher limits and logging/proxy defaults.

Example build commands, from a secret-free build environment:

```sh
uv sync --frozen --no-dev
cd web
npm ci
npm run build
cd ..
```

Example public environment (replace placeholders through secret injection;
never copy the local collector/admin `.env`):

```dotenv
DEPLOYMENT_MODE=public
PUBLIC_ALLOWED_HOSTS=["radar.example.org"]
DATABASE_URL=postgresql+psycopg://radar_public:REPLACE_IN_SECRET_STORE@db.example.org:5432/radar_beta?sslmode=verify-full&channel_binding=require
GITHUB_TOKEN=
AI_API_KEY=
WORKSPACE_ADMIN_TOKEN=
LOG_LEVEL=INFO
```

External TLS/CA behavior has not been tested here. Validate certificate verification
with the actual provider; never switch to unverified TLS to get readiness green.
Transaction-mode database proxies/poolers are unsuitable for the collection
session-advisory-lock connection; collection must use a direct/session-persistent
connection. Public read pooling compatibility also needs provider verification.

Provisioning/migrations are separate manual operations, **not startup hooks**.
Migration history `0001`–`0006` remains unchanged. The public account cannot run
migrations. Back up first, review the target and run `alembic upgrade head` with an
owner/migration account only in a separately authorized release. Public startup
requires exactly `0006`; a later schema revision needs an explicit compatibility
review. Never run upgrade/downgrade automatically during crash recovery.

The following grant recipe is for a DBA to review on the chosen beta database;
it was exercised only in the isolated restored database, not on the application
or any production system. The password must be set interactively (`\password
radar_public` in psql) or by a secret manager, not pasted into SQL history.

```sql
CREATE ROLE radar_public LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE radar_beta TO radar_public;
GRANT USAGE ON SCHEMA public TO radar_public;
GRANT SELECT ON alembic_version, developers, repositories, topics,
  repository_topics, repository_contributors, developer_snapshots,
  repository_contributor_snapshots, repository_snapshots TO radar_public;
ALTER ROLE radar_public SET default_transaction_read_only = on;
```

Audit existing PUBLIC and inherited role grants before using this recipe. Do not
use `GRANT SELECT ON ALL TABLES` or permissive default grants: those include private
workspace, AI artifacts and collection receipts. Verify the account cannot read
those tables, write public tables, create objects or assume a more privileged role.
PostgreSQL permissions are not encoded as an application migration because role
ownership and database names are deployment-specific.

## Health, crashes and operations

- `/api/v1/health`: no database access, returns `{"status":"ok"}`. Use for liveness,
  not proof of data readiness. `/api/v1/ready`: bounded three-second DB/schema/role
  check, 200/503 with generic text and no-store; use for routing readiness. Include
  an allowed Host in platform health probes. Public startup fails before serving
  when the frontend, database, schema or restricted role is unsuitable.
- SQLAlchemy pre-ping is retained. Later disconnects/timeouts return generic
  errors and readiness becomes 503; the pool can reconnect after recovery. No
  business write or paid call is automatically retried by the public web service.
- The service has no background worker/timer. Restarting it runs no collection,
  migration, AI generation or repair. Use platform process supervision; do not
  create an infinite retry loop around a failed migration or paid operation.
- Existing request-attempt caps count retries/pagination; GitHub core/search quotas,
  bounded retry and stop receipts remain intact. Existing collection ownership
  checks pin the PostgreSQL session, refuse work after loss, and protect terminal
  writes. Crash/lost-connection releases its session lock; a receipt may remain
  `running` after a hard crash and needs operator review, not blind replay.
- Existing AI budget is per invocation, **not a durable/global currency budget**;
  separate local requests/runs can spend again. Public mode avoids this exposure
  entirely. Provider retries remain bounded and existing caching is retained.
- A future separate scheduled collection job must use its own credentialed local
  CLI environment and direct PostgreSQL connection, explicit request caps, stable
  receipts and the existing advisory lock. Set a process deadline and prevent
  scheduler retries from silently replaying paid AI. No scheduler/job is added in
  8A. Never mount collector credentials into the public web process.
- Monitor readiness failures, process restarts, generic request failures, database
  utilization and dataset freshness. Read receipt/status diagnostics privately.
  No third-party telemetry, log drain, alert service or new logging dependency is
  installed. HTTP access logs are deliberately off; request metrics/log redaction
  at the selected edge are an operator prerequisite.

## CI

`.github/workflows/ci.yml` runs on PRs and main pushes with `contents: read`, pinned
checkout/setup action SHAs, disabled persisted checkout credentials and job timeouts.
Python and frontend gates are separate jobs. CI generates and masks a fresh database
password per job; it uses no repository/production secret. PostgreSQL runs only in
an ephemeral Docker container bound to runner loopback, with no persistent volume.

`DATABASE_URL` initially names an unreachable placeholder; only
`TEST_DATABASE_URL` points to the disposable **github_radar_test**. The exact name
preserves the existing stricter advisory-lock tests. All safety guards still run.
After tests, only that disposable database receives `upgrade head` and `alembic
check`. Recovery restores into the separately created **github_radar_restore_check**,
never github_radar_test. Cleanup removes only the named CI container. No deployment,
push, artifact containing database contents, or production access is configured.

Gates: frozen Python install, Ruff, strict mypy, all pytest cases including
PostgreSQL, migration head/model validation, fresh backup/restore drill, locked
Python advisory audit; npm ci, lint, tsc, Vitest, production build, npm audit and
production-only npm audit. Both npm audits pass locally and fail CI on any reported
severity. Lifecycle install scripts are disabled by `web/.npmrc`; engine versions
are enforced. No advisory gate is suppressed or marked optional.
Repository branch protection/required checks are not changed by a workflow file
and must be configured separately after the first successful hosted run.

This follows GitHub's [least-privilege/pinned-action guidance](https://docs.github.com/en/actions/reference/security/secure-use).
Proxy trust requires explicit configuration; see [FastAPI's proxy documentation](https://fastapi.tiangolo.com/advanced/behind-a-proxy/).

## Backup and recovery

Phase 7's `pg_dump --format=custom` procedure creates a private backup outside Git,
passes credentials through environment variables and checks `pg_restore --list`.
That listing alone did not verify recovery. Dumps contain private notes/watchlists;
keep permissions 0600, the parent directory 0700, and protected off-machine copies.
A fresh 8A snapshot was fully restored; every row in all 14 public-schema tables
matched, including the one real saved repository. Revision `0006` and all restored
constraints were valid. No source row or migration was changed.

The new `python -m github_radar.recovery` drill exports a repeatable-read, read-only
snapshot and uses it for both the row fingerprints and custom-format backup. It
refuses overwriting an existing archive, emits only counts/hashes, and does not
print pg_restore error bodies. It restores only to exact database name
`github_radar_restore_check` on loopback, with explicit confirmation and no URL
query overrides. It refuses the same source/target or an occupied target, even
empty pre-existing tables. It never creates/drops/resets a database. Restore uses
one transaction, exit-on-error, no owner/ACL/tablespace replay, and no `--clean` or
`--create`; see [PostgreSQL pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html).

The original Phase 8A plaintext fixture and external-TLS limitation are superseded
by [the current TLS/SCRAM backup procedure](PUBLIC_DEPLOYMENT.md#independent-backup-and-recovery).
Use a new isolated local PostgreSQL 16 cluster with a trusted localhost certificate,
SCRAM passwords and the exact empty `github_radar_restore_check` database. Both
source and target URLs must use `postgresql+psycopg://`, verified TLS and mandatory
channel binding. Never point tests at an existing application database.

For automated acceptance on synthetic data only, run
`uv run --frozen python -m tests.run_postgres`. This creates fresh clusters, applies
the reviewed migrations locally and verifies snapshot-consistent backup/restore.
For real owner backups, follow the current runbook's private storage and explicit
operator authorization requirements. No cloud storage, encrypted independent
custody or provider recovery has been verified by this local regression.

## Dependency audit and resource limits

Mako was patched from 1.4.1 to 1.4.2 in the locked Python environment; its path
traversal advisory no longer appears. `uv audit --locked` reports no known
vulnerabilities in 44 packages at review time.

The 2026-10-27 follow-up reproduced **8 affected packages: 2 critical, 1 high,
5 moderate** (16 unique advisories), including two moderate production packages.
Targeted upgrades to **Vite 6.4.5, Vitest 4.1.12 and React Router DOM 7.18.4** now
produce **zero vulnerabilities** for both full and production-only npm audits.
React remains 18.3.1. Vite's 6.x security backport avoids an unnecessary newer major;
Vitest 4 and Router 7 are required to leave the affected branches. No forced audit
fixes, overrides or suppressions were used. The complete advisory IDs, affected
ranges, transitive chains, exposure analysis and every changed lockfile package
are in [FRONTEND_SECURITY_REVIEW.md](FRONTEND_SECURITY_REVIEW.md). Refresh audits
before release; a current pass is not a lifetime security guarantee.

No provider was selected or purchased. Free-tier feasibility is **NOT TESTED**.
Budget for the Python process, per-request dataset/graph copies and three database
connections; the frontend build needs a separate Node build environment. Measure
RSS/CPU and slow-query/concurrency behavior before choosing a memory limit; there
is no measured minimum RAM claim here. Expect provider-dependent sleep/cold starts,
ephemeral filesystems, limited connections/storage/egress/build minutes, and no
assumed backup retention or always-on collection. External PostgreSQL and off-host
backups must survive web restarts. Health checks and job schedules may consume
free quotas; verify the selected platform's current terms separately.

## Manual release verification checklist — future authorized release only

1. Confirm reviewed commit/clean build, unchanged migration history, green hosted
   CI including advisory audits, and no secret/env/dump/cache in the artifact.
2. Verify backup age and a restore in a new allowlisted disposable database;
   secure off-host storage. Record revision/counts and recovery time.
3. Verify the public runtime has only its restricted DB secret and explicit public
   mode/allowed hosts. Confirm wrong/empty configuration refuses startup.
4. Verify DB role grants, TLS certificate verification, no workspace/AI/receipt
   table access, and no public-table write privilege. Apply reviewed migrations
   separately only with explicit release authorization.
5. Verify HTTPS, certificate renewal, preserved Host, upstream network restriction,
   request-size/time/rate limits and logging redaction. Probe an invalid Host;
   it must fail even with X-Forwarded-Host claiming the allowed host.
6. At the real edge verify health/readiness, built SPA navigation, empty/stale data
   states, all public read families, filtering and responsive behavior. API docs,
   AI reads, workspace shells and private GETs must be unavailable. Probe POST AI,
   workspace PUT/DELETE, unknown writes and OPTIONS: none may reach dependencies.
7. Verify browser cross-origin requests gain no CORS permission, forwarding headers
   never open private paths, and every denied request/cache/security header is as
   expected. Scan delivered JS/HTML/maps and proxy/app logs for synthetic sentinels.
8. Revoke/disconnect the test DB connection and check generic failures/readiness
   503; restore it and verify recovery. Restart the web process and confirm no
   migration, collection, AI request or private state change occurs.
9. Measure normal and abusive read traffic with limits enabled; verify CPU/RSS,
   connection cap, cold starts and recovery. Define alerts and an owner for failures.
10. Prepare rollback to the previous built application/config after compatibility
    review; retain backups. Never automate schema downgrade or paid-call replay.
    Keep the private admin workspace on a separate direct-loopback installation.

Stop at Phase 8A review. Nothing here authorizes deployment or Phase 8B/8C.


## Local finalization — 2026-10-28

The reviewed commit scope is the 38 files listed below, on
`feat/phase-8a-production-readiness`, based on local `main` at
`a587f2ed665aee0a71dd0f47c85bcc643f322a36`. No runtime implementation changes were
needed during finalization. This is approval for a local commit only; hosted CI,
push, merge and deployment remain separate steps.

- **TESTED again:** `npm ci` installed 283 packages with install scripts disabled.
  Manifest/lock agreement and the lockfile SHA-256 were unchanged:
  `db1cb039baaa35eb39a4ad746d3205006ef5a4f5b5e436ebd823ee9bd136c342`.
  Both `npm audit` and `npm audit --omit=dev` returned zero vulnerabilities.
- **TESTED again:** frontend lint, typecheck, 53 tests in 9 files and production
  build; Ruff and mypy (140 files); 241 public-access/workspace/recovery unit
  regressions passed. Eight existing Starlette HTTP-422 deprecation warnings
  were reported; no test was skipped or weakened. The interrupted sandbox runs
  were not counted as passes; the security rerun completed outside the sandbox
  using mocks and in-memory SQLite.
- **REVIEWED / TESTED:** full Phase 8A diff, all endpoint/method classifications,
  direct-loopback and public access boundaries, complete CI gates, pinned action
  references, `contents: read`, no repository-secret inputs, disposable loopback
  PostgreSQL, independent restore target, YAML and embedded shell syntax.
- **TESTED:** configured-secret/token/private-key scan of intended files and built
  assets; ignored private environment/backup/build files; no source maps or
  temporary/generated files in the commit scope; final whitespace checks.
- **CONFIRMED unchanged:** migrations 0001–0006 match the Phase 7 commit byte for
  byte. Discovery, scoring, collection ownership/budgets and the repository-detail
  CSS/rendering are unchanged. No application database connection, migration or
  mutation was performed during finalization. PRE-BETA-UX-001 remains separate.
- **PRIOR VERIFIED RESULTS retained, not rerun at finalization:** 730 Python tests
  including 53 guarded PostgreSQL cases, Chromium functional/security smoke and
  the earlier isolated recovery exercise. The reviewed implementation is unchanged
  since those checks. Hosted CI and real deployment prerequisites remain
  **NOT TESTED / BLOCKED** as described above.

Complete intended file manifest:

```text
.env.example
.github/workflows/ci.yml
README.md
docs/FRONTEND_SECURITY_REVIEW.md
docs/NETWORKING_WORKSPACE.md
docs/PRODUCTION_READINESS.md
src/github_radar/api/app.py
src/github_radar/api/deployment.py
src/github_radar/api/readiness.py
src/github_radar/api/routes/ai.py
src/github_radar/api/routes/status.py
src/github_radar/api/workspace_access.py
src/github_radar/cli/app.py
src/github_radar/config/settings.py
src/github_radar/recovery.py
src/github_radar/storage/db.py
src/github_radar/storage/models.py
tests/integration/test_postgres_flow.py
tests/integration/test_public_deployment.py
tests/unit/test_api_routes.py
tests/unit/test_public_deployment.py
tests/unit/test_recovery.py
tests/unit/test_schema.py
tests/unit/test_workspace.py
uv.lock
web/.npmrc
web/package-lock.json
web/package.json
web/src/App.tsx
web/src/components/Layout.tsx
web/src/components/LocalOnly.tsx
web/src/lib/deployment.ts
web/src/lib/endpoints.ts
web/src/pages/AiPage.tsx
web/src/pages/__tests__/PublicDeployment.test.tsx
web/src/pages/__tests__/RepositoryDetailPage.test.tsx
web/src/pages/__tests__/Workspace.test.tsx
web/vite.config.ts
```
