# Phase 8C-A technical report

Date: 2026-10-29. Final acceptance review completed for one authorized local commit,
`feat: prepare secure free-tier public deployment`. No push, merge, provisioning,
deployment, live collection, data publication or schedule activation performed.

- Repository: `zinverno/github-radar`.
- Verified starting HEAD and freshly fetched, unchanged `origin/main`:
  `4cf885f21ed67c97df7d7073dcf52d77a3422d23`.
- Working branch: `feat/phase-8c-a-free-tier-preparation`.
- Delivery commit: the commit containing this report; obtain its full SHA with
  `git log -1 --format=%H` on the reviewed branch. It is intentionally not pushed.
- Existing application database and real workspace data were not accessed.
- Runtime dependencies, both lockfiles, migrations 0001–0006, frontend source and
  collection workflows are unchanged. CI gains deployment tests, without cloud secrets.

## Implemented behavior

The [deployment runbook](PUBLIC_DEPLOYMENT.md) describes the exact operator steps.
The native Render build/start configuration serves the existing compiled React SPA
through FastAPI. Automatic deploys/previews are off. The dedicated public entrypoint
requires explicit public mode, exact hosts, the reader credential, verified TLS and
`PORT`; it ignores ambient worker/proxy/debug overrides and starts one bounded worker.
Builds now refuse credential variables before dependency commands. The Blueprint
does not request a database credential during initial creation. The subsequent
credential-free-build/runtime-secret handoff is an explicit provider activation gate.

Connection handling now validates direct PostgreSQL endpoints and safely translates
the accepted TLS URL options into an explicit SSL context. Reader, migration and
backup paths share that validation. The collector keeps its query-free credential
contract, uses the same verified context constructor, and rejects known Neon pooler
endpoints. Its advisory-lock lifecycle is unchanged.

Role checks cover effective table/column/sequence access, PUBLIC ACLs, defaults,
memberships, ownership, administrative attributes and user-defined functions.
This is stricter than the previous minimum-grant check. Existing external
configurations may now be refused; use the new named-object role script, never
relax the checks or fall back to an owner credential.

Initialization uses an explicit database confirmation, requires an empty destination,
and applies 0001–0006 in the checked connection/transaction. Public startup cannot
invoke it. Backup now supports verified external TLS options while preserving the
existing exact local restore-target/emptiness/confirmation checks. A reviewed
historical-data importer is intentionally not supplied: empty first deployment is
supported and the optional publication design is documented separately.

The review also checked SQL NULL behavior in the schema guard, asyncpg's internal
`char` representation in catalog results, and logging behavior when Alembic is
invoked in-process. Catalog `relkind` is explicitly cast to text, schema mismatch
includes an empty version table, and Alembic preserves existing application loggers.

## Final acceptance findings and corrections

All 27 files were reviewed against the pinned `origin/main`, including new files.
An independent read-only security review examined TLS, effective roles, initialization
and recovery, then reviewed the corrections. No remaining code-level acceptance
blocker was found in that scope; this is not cloud release approval.

| Finding | Smallest correction / evidence |
| --- | --- |
| SQL `NOT LIKE 'pg_%'` mistakenly ignored user schemas such as `pgprivate` | Literal `pg_` prefix checks in role audit and shared empty-target guard; PostgreSQL regressions cover table/function/sequence/schema access and refuse initialization/restore without creating an archive |
| Audit omitted PostgreSQL 17+ MAINTAIN and administrative parameter ACLs | Version-gated MAINTAIN check; reject direct/PUBLIC SET and ALTER SYSTEM grants; reject schema/database grant options as well as table/column grant options |
| Initial Render configuration supplied reader credentials to native builds | Remove initial DATABASE_URL prompt; reject credentials before uv/npm; document credential-free builds and Save and deploy without rebuild. Reuse of the first build after refused startup remains **BLOCKED / NOT VERIFIED** |
| Public launcher allowed alternate libpq credential variables | Refuse ambient PG* variables in both public launcher and build; regression cases include PGPASSWORD, PGPASSFILE and PGSERVICE, with fixed redacted errors |

Pre-fix targeted runs reproduced 16 schema/MAINTAIN/build failures and four
parameter-ACL failures. Corrections add 29 regression cases to the supplied 914-test
baseline. No assertion or warning filter was weakened. Migrations 0001–0006,
dependencies, frontend source and collection workflows remain unchanged.

## Verification results

| Check | Result / evidence |
| --- | --- |
| Complete Python suite | **VERIFIED: 943 passed**, including **128 PostgreSQL integration tests**, no skips; 60.68 seconds on the final run |
| Deployment stack | **VERIFIED: 56 tests** inside that suite; newly initialized local TLS PostgreSQL, no external DB/GitHub access |
| Fresh public database | **VERIFIED:** additional disposable TLS/ASGI smoke; readiness 200, empty lists and zero counts, missing snapshot dates null, unknown repository 404, SPA route 200 |
| Migration head / schema drift | **VERIFIED:** 0001–0006 on disposable PostgreSQL; `alembic check`: no new upgrade operations |
| Recovery | **VERIFIED:** TLS source, custom CA, archive 0600, consistent row fingerprints, constraints, revision 0006, nonempty target refusal; deployment test round-trips three repositories and one synthetic private workspace record |
| Ruff | **VERIFIED:** `ruff check .` |
| mypy | **VERIFIED:** `mypy src tests deploy`, 153 source files |
| Frontend tests | **VERIFIED:** 53 tests / 9 files |
| Frontend lint / types | **VERIFIED:** `npm run lint`, `npm run typecheck` |
| Production builds | **VERIFIED:** normal Vite build and clean isolated `bash deploy/build.sh`; output hashes identical; runtime-only Python environment imports the app |
| Python dependency audit | **VERIFIED:** `uv audit --locked`, 46 packages, no known vulnerabilities or adverse statuses |
| Frontend dependency audits | **VERIFIED:** `npm audit` and `npm audit --omit=dev`, zero vulnerabilities |
| Actionlint | **VERIFIED:** v1.7.12, including final CI port change |
| Render YAML | **VERIFIED:** current public Render JSON Schema with jsonschema 4.25.1; no Render account/API mutation |
| Resource smoke | **VERIFIED locally:** cgroup CPU <=0.1, memory <=512 MiB; process address-space cap 512 MiB; peak web RSS 93,196 KiB (~91.0 MiB), one observed DB connection, no child web workers |
| DB pool exhaustion | **VERIFIED:** two held public connections; third checkout times out; reader PostgreSQL connection limit is four |
| Diff hygiene | **VERIFIED:** 27-file allowlist, `git diff --check`, secret-signature and manual review; no secrets, dumps, source maps or generated artifacts included; no dependency/migration/frontend-source/collection-workflow changes |

The suite reports 12 deprecation warnings: Alembic's legacy path-separator fallback
and Starlette's old 422 constant used by existing endpoints. No warning was suppressed
and no test/contract was weakened.
The clean build also emits existing npm deprecation notices for `whatwg-encoding`
and unsupported ESLint 9.39.5; both audits remain clean. uv reports its experimental
audit status and a harmless cross-filesystem hardlink fallback. These notices were
not suppressed, and no dependency upgrade was introduced during acceptance.

Local PostgreSQL was **18.6**. The CI workflow uses PostgreSQL **16**, including the
new TLS fixture's server/client tools. This exact revision's hosted PostgreSQL 16
run is **NOT VERIFIED**: there is no pushed commit/PR, and the local Docker daemon
is unavailable. The previously green Phase 8B CI does not establish this phase's CI.
CI's main container binds loopback port 55432 to avoid the distribution PostgreSQL
service that installing server tools may start on 5432.
PostgreSQL 16 lacks MAINTAIN: its two hostile-MAINTAIN cases deliberately skip there;
the audit does not query that privilege on PostgreSQL 16. All cases ran on local 18.6.

### Security scenarios exercised

- Missing/wildcard hosts, non-public mode, wrong DB identity, missing/invalid port,
  forbidden GitHub/AI/workspace/collector/admin secrets; failure before app creation,
  with fixed error text and no secret echo.
- Actual reader and collector certificate validation; both reject an untrusted CA
  and a valid CA with a hostname mismatch. Verified connections report TLS active.
- Rejection of weak/ambiguous/duplicate/unknown TLS parameters and known pooled
  Neon endpoints; SSL key logging is not enabled by the environment.
- Collector backend PID/session ownership remains stable across commit while the
  existing advisory lock is held. Existing lock-loss/concurrent-collector tests remain.
- PUBLIC and direct column grants to private tables, AI reads, unexpected writes,
  sequence access, NOINHERIT role escalation, schema CREATE, database TEMP,
  permissive default privileges, executable SECURITY DEFINER functions and grant
  options all cause role audit failure.
- User schemas beginning with `pg` but not the reserved literal `pg_` prefix cannot
  escape audits or empty-target validation. MAINTAIN, parameter SET/ALTER SYSTEM and
  database/schema grant options are rejected for both application roles.
- Disabling the reader's transaction read-only setting still cannot enable table
  writes, private/AI/receipt reads, sequence allocation or `SET ROLE` to the owner.
- Actual HTTP serves the built entrypoint/assets and SPA routes, responds on the
  assigned port, and serves public database-backed endpoints. Workspace/AI/docs
  stay closed under spoofed forwarding headers. Wrong/malformed/duplicate Host and
  write methods are refused; HTTP security headers are present.
- After startup, revoked SELECT causes generic 500/503 responses; restoring grants
  and terminating the old DB backend allows pre-ping to reconnect. Logs contain no
  SQL, connection password or synthetic private-note sentinel.
- Complete database row fingerprints are unchanged by the HTTP smoke: startup and
  requests perform no collection, AI generation, migration or application writes.
- Build tests exclude runtime credentials, dotenv, unreviewed `public/` files and
  npm user configuration, and reject unexpected artifact types/source maps.
  The top-level build refuses supplied credential variables before dependency code.

### Reproduction and local evidence

Prerequisites: pinned Python/Node/uv/npm, PostgreSQL server/client tools on PATH,
OpenSSL, and permission to create temporary local processes/sockets. No real cloud
credentials are needed. Never substitute the application database for a test URL.

```sh
uv sync --frozen
python deploy/build_frontend.py
# Set DATABASE_URL to an unreachable placeholder and TEST_DATABASE_URL to a newly
# created disposable github_radar_test database, as .github/workflows/ci.yml does.
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src tests deploy
```

The TLS stack tests create/stop their own independent cluster; `TEST_DATABASE_URL`
is their integration opt-in, not their target. The full existing integration suite
uses its guarded test URL. The resource run used a temporary user scope:

```sh
systemd-run --user --quiet --scope -p MemoryMax=512M -p CPUQuota=10% \
  env RADAR_TEST_RESOURCE_LIMITS=1 \
  TEST_DATABASE_URL=disposable-cluster-opt-in \
  DATABASE_URL=postgresql+asyncpg://unused@127.0.0.1/unreachable_application \
  DEPLOYMENT_MODE=local GITHUB_TOKEN= AI_API_KEY= WORKSPACE_ADMIN_TOKEN= \
  .venv/bin/pytest -q tests/integration/test_deployment_stack.py -k production_http -s
```

Ephemeral workstation evidence (not portable release dependencies):

- Final JUnit: `/tmp/radar-8ca-suite-kfrm9hvu/pytest.xml`.
- Full-suite isolated cluster runner: `/tmp/run-radar-8ca-suite.py`; every run creates
  a new cluster and stops it in `finally`.
- Clean production build tree: `/tmp/radar-8ca-artifact-t4a037qy`.
- Artifact reproduction helper: `/tmp/build-radar-8ca-artifact.py`.
- Render schema fetched from `https://render.com/schema/render.yaml.json`, SHA-256
  `8a3fb8da6b4f55fe686fbefa790e011d3dd7895f013f355953c572a21ab6494a`.

All pytest clusters were stopped. Failed early sandbox runs were not counted as
passes; required socket/network/cache operations were rerun with approved access.

| Input / artifact | SHA-256 |
| --- | --- |
| `uv.lock` | `5d9fc03017f3bae91f82f30ab6f5d7bee4aed171d06fe24bca44b9380d066be5` |
| `web/package-lock.json` | `db1cb039baaa35eb39a4ad746d3205006ef5a4f5b5e436ebd823ee9bd136c342` |
| `web/dist/index.html` | `41771443ee09f329e962cc7c4df9b4e9a97b760356b2dcb03ebb19460e33bf97` |
| `web/dist/assets/index-DrLAsq7Q.css` | `e307f1be95ec5e00f5aef63c557ada1fb225195dfd3accc2a08b633ecd386a01` |
| `web/dist/assets/index-DruLil4j.js` | `a5a6221403db962b115171a74b358bef14e47ea6bb9e55d0f2ee0dda517d7f1f` |

## All changed repository files

| File | Change |
| --- | --- |
| `.github/workflows/ci.yml` | Build production SPA for Python smoke; PostgreSQL TLS tools and isolated container port |
| `README.md` | Link the deployment runbook and approval boundary |
| `render.yaml` | Manual single Free Python web service configuration; no initial database credential |
| `deploy/build.sh` | Reject build credentials, pin runtime/tools and build frozen production dependencies |
| `deploy/build_frontend.py` | Isolated frontend inputs/environment and output allowlist/hashes |
| `deploy/roles.sql` | Explicit named-object grants for new application roles, schema guard and connection caps |
| `docs/PUBLIC_DEPLOYMENT.md` | Architecture, operator steps, data publication, quotas, backup, rollback and release gates |
| `docs/PHASE8C_A_VERIFICATION.md` | This technical report and complete file inventory |
| `docs/PRODUCTION_READINESS.md` | Point historical Phase 8A instructions to the current runbook; retain UX issue |
| `docs/AUTONOMOUS_COLLECTION.md` | Describe stricter collector audit and current role/TLS procedure |
| `migrations/env.py` | Verified connection options, supplied initialization connection, preserve application logging |
| `src/github_radar/initialize.py` | Explicit empty-database initialization to 0006 |
| `src/github_radar/public_server.py` | Restricted, bounded public entrypoint honoring PORT |
| `src/github_radar/storage/connection.py` | Direct URL validation and verified TLS context/options |
| `src/github_radar/storage/permissions.py` | Effective role/privilege audit, literal schema prefix, MAINTAIN and administrative grant checks |
| `src/github_radar/storage/db.py` | Shared TLS handling and two-connection public pool/timeouts |
| `src/github_radar/services/automation.py` | Direct endpoint/TLS reuse and effective collector privilege audit |
| `src/github_radar/recovery.py` | Strict TLS URL-to-libpq mapping and corrected empty-schema detection, preserving restore restrictions |
| `src/github_radar/api/app.py` | Bounded 15-second initial readiness for cold DB connection |
| `src/github_radar/api/readiness.py` | Full reader-role audit and explicit probe timeout |
| `src/github_radar/api/deployment.py` | Strict raw Host syntax, HSTS and Permissions-Policy |
| `tests/integration/test_automation.py` | Disposable fixture satisfies the stricter PUBLIC CREATE/TEMP policy |
| `tests/integration/test_deployment_stack.py` | TLS/roles/migrations/backup/real HTTP/resource/connection-cap tests |
| `tests/unit/test_connection_security.py` | TLS downgrade, option, pooler and backup-environment guards |
| `tests/unit/test_deployment_artifact.py` | Build isolation and manual Blueprint invariants |
| `tests/unit/test_public_server.py` | Host/secret/mode/role/port startup failures and bounded launch |
| `tests/unit/test_public_deployment.py` | Readiness audit contract and malformed Host regression cases |

Generated `web/dist`, installed dependencies and local `/tmp` evidence are ignored
build/test products and are excluded from the 27-file delivery commit.

## Remaining release gates and operator next steps

1. Review the local acceptance commit; pushing/PR delivery remains a separate action.
   Obtain hosted green checks on that exact commit; local PostgreSQL 18
   checks do not replace the configured PostgreSQL 16 hosted gate.
2. Separately approve a new Neon project and Render service. Recheck account quotas,
   billing controls, region and exact hostnames; create only the new empty database,
   run the initializer and named-role SQL, and set credentials through secret stores.
3. Approve an empty launch or a reviewed public-data policy/artifact. Historical seed
   import remains **BLOCKED** until reviewed; no existing database is auto-cloned.
4. Complete real Neon TLS/role/provider-extension checks and real Render HTTPS,
   proxy, cold-start, error/log and rollback acceptance. These are **NOT VERIFIED**.
   First prove Render can reuse the initial credential-free build after refused
   startup and add runtime credentials without rebuilding. This exact provider
   behavior is **NOT VERIFIED / activation BLOCKED**; do not weaken the build guard.
5. Establish and test independent encrypted backup custody, retention and recovery
   objectives. Automatic production restore/overwrite remains **BLOCKED** by design.
6. Validate representative dataset growth and sustained load. The local 3-repository
   smoke under resource limits establishes operation, not a capacity/SLA guarantee.
7. Keep collector schedules/gates disabled until their separate approval. Keep
   `PRE-BETA-UX-001` (320 px overflow) for the separate UX fix/acceptance decision.

The local implementation and acceptance review are complete. Public release remains
**BLOCKED pending these operator and external acceptance gates**.
