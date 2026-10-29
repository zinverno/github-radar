# Public beta deployment: Phase 8C-A

Preparation only. Baseline: `4cf885f21ed67c97df7d7073dcf52d77a3422d23` (merged
Phase 8B). No cloud resource, live collection, schedule activation, application
database access, private-data import or push is part of this phase. Final acceptance
authorizes one reviewed local commit; it does not authorize deployment.
See [verification and changed files](PHASE8C_A_VERIFICATION.md) for observed results.

## Status and architecture

| Status | Scope |
| --- | --- |
| IMPLEMENTED | Manual Render Blueprint, frozen build, bounded public entrypoint, direct verified TLS, effective role checks, empty database initializer and TLS-capable recovery |
| VERIFIED | Local disposable PostgreSQL, local CA positive/negative TLS, real HTTP/SPA, role attacks, recovery, resource smoke and deterministic regression (details in report) |
| NOT VERIFIED | Render native build/edge/custom-domain behavior and reuse of a build after failed initial startup, actual Neon certificate chain/roles/extensions/hibernation, hosted CI for this commit, representative sustained beta load |
| BLOCKED | Public release until credential-free build/runtime handoff is proven, operator approval, hosted checks, reviewed data policy, external acceptance, independent backup custody and quota checks are complete |

```mermaid
flowchart LR
    Browser -->|HTTPS| Render[Render Free edge]
    Render -->|HTTP on PORT| API[One bounded FastAPI process + web/dist]
    API -->|Direct TLS / SELECT only| Neon[(New Neon Free PostgreSQL)]
    Actions[Existing GitHub Actions collector] -->|Direct TLS / restricted collector role| Neon
    Owner[Operator-only owner] -->|Explicit initialization / migration| Neon
    Neon -->|Operator backup over verified TLS| Backup[Encrypted off-provider archive]
```

Target: $0/month **within all account quotas**. Exactly one web process per
instance, no worker, scheduler, disk or Render database. Existing Phase 8B
Actions remain disabled by their activation gates. Render can briefly overlap
old/new instances during a manual rolling deploy; the reader role's connection
limit of four reserves two connections for each. The configured steady state is
one free instance. See [Render deploy behavior](https://render.com/docs/deploys).

## Reproducible build and start

The checked-in [Blueprint](../render.yaml) uses `runtime: python`, `plan: free`,
one instance, previews off and `autoDeployTrigger: off`. **Creating or syncing a
Blueprint can provision resources and trigger an initial deploy despite automatic
Git deployment being off.** Do neither before approval. Later configuration saves
can also deploy: use “Save only” until the operator approves the specific change.
[Blueprint reference](https://render.com/docs/blueprint-spec),
[environment configuration](https://render.com/docs/configure-environment-variables).

Build command: `bash deploy/build.sh`.
Start command: `env PYTHONPATH=src .venv/bin/python -m github_radar.public_server`.
Run both from the repository root of the approved commit.

- Python `3.12.13`, Node `24.14.1`, uv `0.11.6`, npm `11.11.0` are pinned.
  Render supports explicit [Python/uv versions](https://render.com/docs/python-version)
  and [Node versions](https://render.com/docs/node-version).
- The first build step refuses nonempty database URLs and common application
  credential variables, before running any dependency commands. Credentials must
  already be absent from the Render build environment; rejection is a guard against
  operator mistakes, not a claim that a supplied secret never reached the provider.
- `uv sync --frozen --no-dev --no-install-project` installs runtime dependencies
  from `uv.lock`. Source runs via `PYTHONPATH=src`, avoiding an unpinned isolated
  hatchling build dependency. No migration is part of the build or start command.
- `deploy/build_frontend.py` copies only the reviewed frontend source/config/lock
  inputs to a temporary directory and runs `npm ci --ignore-scripts` and the real
  Vite production build. Child commands receive a small explicit environment;
  runtime secrets, `NODE_OPTIONS`, Vite variables, dotenv, user npm config and
  `web/public` are excluded. No external application/database content is an input.
- The output allowlist is HTML, JS and CSS; source maps and source-map references
  fail the build. Successful output replaces only generated `web/dist` and prints
  SHA-256 hashes. Review frontend source as code: hardcoded secrets cannot be made
  safe by a build filter. Never put credentials or data exports in source.
- The deployable tree is `src/`, `.venv/` and `web/dist/` from this build. Record
  the commit, lockfile hashes, tool versions and asset hashes with each release.
  Native Render OS images are provider-managed; this is a frozen dependency build,
  not a claim that the entire operating-system image is bit reproducible.

### Credential-free build and runtime handoff — activation BLOCKED

Native Render environment variables are available to builds as well as the service.
There is no runtime-only scope configured here. Therefore `DATABASE_URL` is deliberately
absent from the Blueprint. A service created from it cannot start successfully until
the operator supplies the reader URL **after** a credential-free build. Do not add
production credentials to make a build pass or attach shared secret groups/files.

After provisioning approval, verify this procedure before any public release:

1. Build the approved commit with no database URL or application credentials. Initial
   startup must refuse the missing reader URL; this is expected and serves no traffic.
2. Confirm that Render retains that completed build despite failed startup. Add only
   the reader `DATABASE_URL` using **Save and deploy**, which deploys an existing build
   without rebuilding. Never select **Save, rebuild, and deploy** with credentials set.
3. Before every subsequent rebuild, remove the reader URL with **Save only** and
   confirm all credentials are absent. After the credential-free build, restore the
   reader URL using **Save and deploy**. Verify the selected artifact/commit and
   runtime configuration; do not assume uninterrupted service during this handoff.

Render documents redeploying an existing build with updated environment variables,
but reuse after the first failed startup is **NOT VERIFIED**. It is an explicit
**activation blocker**. If Render cannot support this handoff, stop: this native
configuration does not meet the no-production-credentials-at-build requirement.
A separately reviewed artifact delivery change would then be required; weakening
the build guard is not an acceptable workaround.
[Render environment configuration](https://render.com/docs/configure-environment-variables).

## Environment and public boundary

| Variable | Required value / scope |
| --- | --- |
| `DEPLOYMENT_MODE` | `public` on Render |
| `PUBLIC_ALLOWED_HOSTS` | JSON array of exact lowercase deployed hostnames, e.g. `["github-radar-public-beta.onrender.com"]`; replace with actual assigned names, including every verified custom domain |
| `DATABASE_URL` | Runtime only, absent during builds. Reader: `postgresql+asyncpg://radar_public:URL_ENCODED_PASSWORD@DIRECT_HOST/radar_beta?sslmode=verify-full`; supply through the gated handoff above |
| `PORT` | Assigned by Render; launcher validates and binds `0.0.0.0:$PORT` |
| `PYTHON_VERSION`, `NODE_VERSION`, `UV_VERSION` | Pinned in Blueprint; build inputs |
| `MIGRATION_DATABASE_URL` | Operator terminal only, never Render or collector |
| `COLLECTOR_DATABASE_URL`, `COLLECTOR_GITHUB_TOKEN`, optional `COLLECTOR_CA_PEM` | Protected Actions environment only; see Phase 8B runbook |

URL-encode usernames/passwords. Do not paste the full URL into CLI arguments,
logs, PRs or shell history. Inject secrets through the relevant secret store.
No `.env` is read by the public entrypoint. Do not attach secret files or shared
credential environment groups to the public service.

Public startup rejects missing/wildcard hosts, non-public mode, wrong DB role,
unverified TLS, malformed port, GitHub/AI/workspace/collector/admin credentials,
and common token/API-key/password/secret environment keys (case insensitive).
Ambient libpq `PG*` variables, including password/service files, are refused in
both the public launcher and build; neither needs those alternate credential paths.
Only the reader password belongs in this process. `LOG_LEVEL=DEBUG`, worker-count
and forwarded-header environment settings do not change the bounded launcher.

The Phase 8A GET/HEAD API allowlist is retained. Workspace, AI, OpenAPI and docs
are inaccessible; forwarding headers grant no privileges. Duplicate/malformed
Host headers fail. Uvicorn proxy interpretation and access logging are disabled;
there is no trust in `Forwarded`, `X-Forwarded-Host`, `X-Forwarded-For` or
`X-Forwarded-Proto`. Configure exact hosts; never add `*` to make probes work.

Render terminates HTTPS and forwards HTTP to `$PORT`. Verify the actual edge
redirect, certificate and Host preservation after authorization. The application
does not derive identity or HTTPS from proxy headers and does not perform scheme
redirects that could loop behind the edge. Responses include CSP, no-store,
nosniff, frame denial, no-referrer, restricted Permissions-Policy and HSTS without
`includeSubDomains`. Private-network access is not an authorization mechanism.

## Neon connections and role model

Neon's `-pooler` endpoint is PgBouncer transaction pooling; session advisory locks
are unsupported there. Direct endpoints preserve the session used by Phase 8B's
lock. Direct connections are also recommended for migration and `pg_dump`.
This phase deliberately uses **direct endpoints for both applications**: the reader
also relies on session settings and only needs two connections. Known Neon pooler
hostnames are refused; arbitrary proxy names cannot be identified by a URL parser,
so the operator must confirm the topology. Pooled public-reader support is not
implemented. [Neon pooling semantics](https://neon.com/docs/connect/connection-pooling).

Reader/migration/recovery URLs accept `sslmode=verify-full` (or the legacy
`ssl=verify-full`) and an optional absolute `sslrootcert` CA-bundle path. Conflicting,
duplicate, weak or unknown URL options fail; no option is silently discarded.
The collector retains its Phase 8B query-free URL and explicit verified context,
optionally extended by `COLLECTOR_CA_PEM`. A pasted Neon URL containing
`channel_binding=require` is rejected rather than silently weakening that setting:
this asyncpg configuration verifies certificates/hostnames, but does not claim
SCRAM channel-binding enforcement.

Python uses an explicit `CERT_REQUIRED` context, hostname checks and TLS >=1.2,
without SSL key logging. Default system CA roots apply unless a CA file is
specified. `pg_dump` gets the corresponding `PGSSLMODE=verify-full`, CA file and
minimum TLS version. All ambient `PG*` overrides are cleared. Only explicit
loopback development URLs without TLS options allow plaintext; the public
entrypoint always requires TLS, including in tests. See
[Neon secure connections](https://neon.com/docs/connect/connect-securely).

| Role | Effective privileges |
| --- | --- |
| `radar_public` | CONNECT, schema USAGE, SELECT on exactly the nine tables in `storage/permissions.py`; no sequences or collection receipts/provenance |
| `github_radar_collector` | Public reads plus receipts/provenance; INSERT/UPDATE on five collection tables; INSERT/DELETE on `repository_topics`; USAGE on three named sequences |
| Migration/owner | Operator-only creation, migrations, grant administration and backups; never an application identity |

Create application roles through **SQL**, not Neon Console/API/CLI role creation:
Neon grants the latter membership in `neon_superuser`.
[Neon role semantics](https://neon.com/docs/manage/roles).

`deploy/roles.sql` refuses pre-existing role names, revokes PUBLIC database
privileges/schema CREATE, grants only named objects, and limits reader/collector
connections to 4/2. It adds no broad table grants or application schema ownership.
Set passwords interactively with `\password` after the transaction succeeds.

Runtime checks reject administrative attributes, any role membership (including
NOINHERIT/SET ROLE paths), ownership, database CREATE/TEMP, schema creation,
unexpected effective table/column/sequence privileges (including PostgreSQL 17+
MAINTAIN), table/column/schema/database grant options, parameter SET/ALTER SYSTEM
grants, permissive default ACLs and executable user-defined functions. PUBLIC
privileges count. Schema filters use a literal `pg_` prefix; user schemas such as
`pgprivate` are audited and also prevent empty-database initialization/restore.
Unknown views/functions/extensions require review; do not relax checks to fit a
provider's defaults. Public startup and `/ready` audit the reader; collection
preflight audits the collector before HTTP. The audit covers the connected
database and cluster role memberships; it cannot inspect relation ACLs inside
other databases. Use an isolated new project/database and verify that application
roles cannot connect to unrelated databases or access large objects/extensions.
Provider/system catalog and built-in function ACLs also require operator review;
the application audit does not certify a managed provider's entire privilege model.

## Operator-controlled empty initialization

Perform only after authorization, from a reviewed checkout, with the new database
identified in the change record. No step reads or clones the existing application DB.

1. Create an independent Neon Free project in a region close to Render (Blueprint
   defaults to Frankfurt). Use PostgreSQL 16 for parity with CI. Create an **empty**
   `radar_beta` database owned by the operator role. Do not branch/restore a private
   database. Obtain its direct endpoint and verify the CA chain/hostname.
2. On an operator machine: `uv sync --frozen`. Inject only the new owner's
   `MIGRATION_DATABASE_URL` with verified TLS. Review the exact database name,
   endpoint and role in the provider UI; keep exclusive ownership during setup.
3. Run:

   ```sh
   uv run --frozen python -m github_radar.initialize --confirm-database radar_beta
   ```

   This checks the explicit confirmation and absence of user objects before
   applying migrations `0001` through **`0006`**, in one connection/transaction.
   It refuses a populated schema and application roles. It is never called by
   the web service. A failure is an operator review event, not a startup retry.
4. Connect `psql -X` using discrete `PGHOST`, `PGDATABASE`, `PGUSER`, injected
   `PGPASSWORD`, `PGSSLMODE=verify-full` and a trusted `PGSSLROOTCERT`. Clear inherited
   `PGSERVICE`, `PGHOSTADDR`, `PGOPTIONS` and other overrides first. Then:

   ```sh
   psql -X -v ON_ERROR_STOP=1 -v target_database=radar_beta -f deploy/roles.sql
   ```

   Set the two passwords with `\password radar_public` and
   `\password github_radar_collector`. Revoke application access to any default
   unrelated database in the new Neon project; audit inherited/provider defaults.
5. Verify the exact effective grants using the reader's startup/readiness and a
   read-only collector status/preflight. Confirm private tables and AI cache remain
   empty. Verify table owner, schema revision `0006`, TLS, and denied writes/SET ROLE.
   Never fix a failure by granting ALL or using the owner as the reader.

Migrations and roles are independent of web rollout. Schema revision changes
require compatibility review, backup and a separate operator-run migration.

## Publication of reviewed public data

The smallest supported first launch is an **empty public database**. Its public
API and SPA remain usable and show empty states. No export/import job or cloning
of the local database is installed.

Before publishing records, approve a source catalog and a field/row review policy.
For Phase 8B collection into this new database, approve its public-only catalog,
bounded budgets, retention and public metadata policy separately. Enabling those
Actions is a separate operator decision, not implied by deployment approval.

If a pre-reviewed seed is required, use this offline procedure in a separately
authorized task:

1. Construct an artifact from explicitly selected public records in a disposable
   staging database. Do not point export tools at the private application DB as
   part of deployment. Record source/provenance, reviewed identities, schema
   revision, row counts, canonical field lists and SHA-256 hashes in a manifest.
2. Allow only the eight public data tables (the reader allowlist without
   `alembic_version`). Review free text, profile fields, URLs and repository
   visibility; a table name alone does not prove every row is publishable.
   Keep IDs/FKs/timestamps coherent. Never include `workspace_*`, `ai_artifacts`,
   `collection_runs`, `discovery_provenance`, credentials or arbitrary SQL/DDL.
3. Review the exact data-only artifact and manifest. Use explicit columns and
   parameterized inserts/COPY into an empty, already migrated staging database;
   no archive owner/ACL replay, triggers, arbitrary SQL or overwrite/upsert of
   existing public records. Verify FKs/sequences and all public API responses.
4. After separate approval, load that identical artifact into the new empty
   destination with the operator role in one transaction. Recheck hashes/counts,
   empty private tables and both application role audits. Retain the review receipt.

**NOT IMPLEMENTED:** a generic seed exporter/importer. Publishing historical data
is **BLOCKED** until such an exact artifact and its loader are reviewed. Empty
deployment does not depend on that optional data-transfer path. The synthetic
tests establish route/DB isolation even when private notes exist; they do not
certify any real dataset as public.

## Health, limits, cold starts and logs

- `/api/v1/health` is DB-independent liveness and is the Render probe. Public
  startup first requires a built SPA and a successful schema/role audit (15-second
  deadline), so a new invalid instance never serves. Continuous `/ready` probes
  would wake Neon and consume free compute. `/api/v1/ready` is a separate bounded
  3-second operator/readiness probe, returning generic 200/503.
- Render uses the service hostname or a verified custom domain for health Host
  and requires a response within five seconds. Include all actual probe hostnames
  in the exact allowlist. [Render health checks](https://render.com/docs/health-checks).
- One worker, four concurrent connections/tasks, backlog 32, 2-second keepalive,
  20-second graceful shutdown and bounded HTTP header buffering. Public SQL pool:
  two connections, no overflow, 3-second checkout, 10-second connection timeout,
  5-second statement/command timeout. Overload can return 503; do not add workers
  to mask memory pressure. The Render Free plan is
  [0.1 CPU and 512 MB](https://render.com/docs/compute-plans).
- A Neon wake-up exceeding the 3-second readiness budget yields 503; a later
  request may recover. Startup gets a longer bounded budget. No startup migration,
  collection, AI call, writes or repair occurs. Pre-ping replaces disconnected
  sessions; deterministic smoke terminates a DB session and verifies recovery.
- Render's idle wake-up can show its own loading page before application startup.
  Do not run keep-alive pings to evade free-plan sleep. Treat cold start as beta
  behavior and show deployment status to operators. Provider timing is unverified.
- SQL/driver exceptions are replaced by generic responses and fixed log messages;
  URL query strings, passwords, SQL parameters and private notes are not logged.
  Access logs are disabled. Provider-level logs are outside the application's
  control: review their access/retention before publishing.
- Several analytics paths materialize a dataset before pagination. Concurrency
  and DB limits do not bound total historical rows or CPU cost of graph analytics.
  Keep the initial dataset small, measure realistic growth/load before admission,
  and suspend collection/public traffic when approaching quotas. No load SLA is claimed.

## Independent backup and recovery

Neon history is a short fallback, not the only backup. Choose an operator-owned
encrypted off-provider destination before launch: for example an existing
encrypted local disk plus a separately controlled encrypted copy. Buying storage
would change the $0 target. Never store archives in Render's ephemeral filesystem,
the public repository, public CI artifacts or public frontend assets.

Suggested initial policy: daily plus pre-migration backups, seven daily and four
weekly generations, private manifests/checksums, tested decryption and monthly
restore drills. This is a proposed RPO <=24h; actual scheduling, custody and RTO
must be accepted/measured by the operator. No backup schedule is activated here.

The existing recovery tool previously rejected all URL query options. It now maps
only reviewed verified-TLS options to both asyncpg and libpq. Unsafe options,
pooler sources and downgrade attempts fail before a dump. Source is read-only;
`pg_dump` shares its exported repeatable-read snapshot. Archives are mode 0600,
created exclusively (never overwritten). Backup permissions must cover the whole
source DB, so use the operator role in an isolated operator environment, never the
public reader/collector. Full backups can contain private data if an operator later
adds it; treat every archive as confidential, never as a publication artifact.

For a backup with an immediate recovery drill:

1. Start a **new disposable local PostgreSQL cluster**, with a matching client/server
   major version, and create the empty `github_radar_restore_check` database there.
   The tool must have exclusive use of that destination throughout the drill.
2. Inject the new beta owner's direct verified-TLS URL as `DATABASE_URL` in an
   isolated terminal with `DEPLOYMENT_MODE=local`; set `RESTORE_TEST_DATABASE_URL`
   to the explicit loopback disposable database URL, with no query options.
3. Run with a new output filename on private storage:

   ```sh
   uv run --frozen python -m github_radar.recovery \
     --output /PRIVATE/BACKUP/PATH/beta-UNIQUE-UTC.dump \
     --confirm-target github_radar_restore_check
   ```

4. Retain the JSON verification report, archive checksum, schema revision, source
   commit/lock hashes and operator record privately. The tool restores with
   `--no-owner --no-privileges --no-tablespaces --single-transaction --exit-on-error`,
   never `--create` or `--clean`, compares every public-schema table's count and
   row hash with the source snapshot and validates constraints. Stop the disposable
   cluster, encrypt the archive/report and keep the second copy independently.

For disaster recovery from a retained archive, verify its stored checksum first,
then rehearse `pg_restore` with those same flags against a **fresh empty local**
`github_radar_restore_check`. Inspect schema, counts, constraints, public responses
and data publication policy offline. Keep it isolated: database dumps contain
executable database definitions and are trusted only when their provenance is known.

**BLOCKED:** automatic production restore and in-place database overwrite. The
recovery tool intentionally refuses every remote/non-allowlisted/nonempty restore
target. A production recovery needs separate approval for a new empty replacement
database, reviewed owner-run restore, fresh application grants/passwords and an
explicit service URL switch. Do not weaken the drill's target validator. Large
databases may exceed its current 120-second subprocess limits or in-memory
fingerprinting capacity; test at the planned retained size before relying on its RTO.

## Quotas, monitoring and rollback

References checked 2026-10-29; recheck the actual account before authorization.

| Component | Free constraints / operator action |
| --- | --- |
| Render | 750 instance-hours/workspace/month; idle sleep after 15 minutes, wake around a minute; ephemeral filesystem; bandwidth/build allowances shared by account; unusually high external DB traffic can trigger suspension |
| Neon | Currently 100 CU-hours/project, 1 GB/project storage, 5 GB/project public transfer; scale-to-zero after five minutes; six-hour history subject to a storage allowance; no uptime SLA |
| GitHub Actions | Standard hosted runners are free for public repos; private-repo usage and artifact/cache storage have separate limits. Keep bounded Phase 8B runs and short receipt retention; no paid larger runners |

[Render Free limits](https://render.com/docs/free),
[Neon plan limits](https://neon.com/docs/introduction/plans),
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
Render can bill bandwidth/build overages when a payment method exists; inspect
account limits and prevent paid overages. Do not promise an unconditional $0 bill.
At a continuously active 0.25 CU, 30 days would consume 180 CU-hours, exceeding
Neon's current 100-hour allowance: real idle time and bounded traffic matter.

Review provider usage, storage growth, memory/CPU, 5xx/503s, cold-start failures,
latest successful collector receipt, unresolved runs, stale observations and backup
age. Review at least daily during initial beta and on provider notices; avoid
continuous DB polling. Set operational warning thresholds (e.g. 70% quota, 80%
memory, backup older than 24h) in the operator checklist. No external monitoring
service or telemetry integration is provisioned.

For application rollback, select the recorded previously verified commit/build
and manually redeploy; keep automatic deployment off. Render Free retains a
limited number of rollback deployments, so retain source/lock hashes independently.
Do not automatically downgrade the database. Current readiness requires exactly
0006; incompatible future schemas require explicit recovery/compatibility review.
For a leaked reader password, revoke/rotate it, save the new secret, and approve a
manual redeploy. For integrity trouble, disable collection gates and public service
first, preserve evidence, then restore to a replacement only after review.

## Operator release checklist

- [ ] Review this diff and approve a specific commit; run hosted CI on that exact head.
- [ ] Recheck Free quotas/billing settings and select approved regions/hostnames.
- [ ] Approve cloud provisioning separately; initialize only the new empty database.
- [ ] Create SQL application roles, set passwords, audit effective access and verified TLS.
- [ ] Approve empty launch or an exact public-data artifact/policy; keep private tables empty.
- [ ] Complete independent backup/decryption/restore drill and record RPO/RTO/custody.
- [ ] Prove the credential-free build/runtime handoff above, including first-build
      reuse after refused startup. Until then, activation remains blocked.
- [ ] Configure only the runtime reader credential; remove it before every rebuild;
      no credential environment groups/files.
- [ ] Manually deploy the approved commit; verify real HTTPS redirect/certificate, Host
      handling, SPA routes, public API, 200 health/ready and forbidden workspace/AI.
- [ ] Exercise cold start, DB unavailable/recovery, invalid configuration and expected
      dataset/load with memory/CPU metrics; inspect application and provider logs.
- [ ] Confirm rollback artifact and operational owner; keep collection gates disabled
      until their separate authorization and credential review.

Known UI issue **PRE-BETA-UX-001** (320 px overflow) remains recorded in
[production readiness](PRODUCTION_READINESS.md). It is unchanged and is a separate
UX acceptance item; these checks do not claim it is fixed.
