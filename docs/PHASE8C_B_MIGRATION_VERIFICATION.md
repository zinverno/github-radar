# Phase 8C-B production PostgreSQL driver migration

**Local native blocker: RESOLVED in the verified Debian 12 and Ubuntu 24.04 test environments.**
**DEPLOYMENT BLOCKED:** Render's actual libpq, OpenSSL packages and Python TLS
linkage have not been verified or supplied reproducibly. Hosted CI and the other
cloud acceptance gates remain open at local acceptance; the focused CI repair is recorded below.

Baseline: `ae9e93d19670f4029f36e12ce0b15cb9222f34de`, verified against `origin/main`
and the remote main reference before work. Branch: `feat/psycopg3-libpq-migration`.
The original migration acceptance authorized a local commit only. The separately
authorized PR #5 CI repair below permits a focused commit and push, but no merge,
deployment or collection activation.
Verification date: 2026-10-30 (Europe/Moscow).

## Original migration commit acceptance

Final review confirmed the branch, HEAD, `origin/main` and remote main at the exact
baseline above, and matched all 51 files to the recorded review manifest. No
material implementation changes were required. Only this report's finalization
record changed; the full regression, fault-injection and resource evidence remains
applicable and is preserved.

A supplemental offline frozen installation, actual loaded-library audit and **58
native/connection-security tests** passed in the disposable Debian 12 environment,
without database access. The binary implementation/distribution, affected vendor
packages, invalid TLS options and unsupported native environments still fail closed.
The final scope review found no credentials, database archives, private configuration
or temporary artifacts among the intended migration files. Local commit acceptance
does not approve hosted execution or Render deployment. Supplemental evidence is
`evidence/final-acceptance-native.log`; `evidence/commit-review-manifest.json` records
the final file hashes while retaining the earlier pre-finalization manifest.

## PR #5 hosted CI loader correction (2026-10-30)

The failed [run 40998182588](https://github.com/zinverno/github-radar/actions/runs/40998182588)
used toolcache CPython 3.12.15 on Ubuntu 24.04. The pinned `setup-python` action
exports `LD_LIBRARY_PATH` on Linux when its default `update-environment` is true;
this is unconditional setup behavior, not evidence that this binary needs it.
The unchanged native guard correctly rejected that override before linkage checks.
[Pinned action implementation](https://github.com/actions/setup-python/blob/e797f83bcb11b83ae66e0230d6156d7c80228e7c/src/find-python.ts).

**IMPLEMENTED:** All five Python jobs in CI/refresh/discovery now select exact
3.12.15 with `update-environment: false`. Only the returned executable directory
is added to PATH; `UV_PYTHON` selects that executable and `UV_PYTHON_DOWNLOADS=never`
prevents a managed-interpreter fallback. No loader variable is cleared or allowed.
Inherited overrides still reach the fail-closed gate. Production code, dependency
locks, native baselines, action pins, activation gates and schedules are unchanged.

**VERIFIED locally:** The exact official
[Ubuntu artifact](https://github.com/actions/python-versions/releases/tag/3.12.15-36805895057)
was downloaded and matched GitHub's asset SHA-256
`fcb672b0437c2d8df5565b7cc250b2b0d7455e2b8d6a4a107cfa6f2efe4f7ed8`.
Testing used a disposable Ubuntu 24.04 amd64 filesystem from the official image
manifest `sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d`,
with signed Ubuntu and PGDG package installation. No host packages or operator
credentials/databases were used. This is a local runner reproduction, not Render.

ELF inspection found `DT_RUNPATH=/opt/hostedtoolcache/Python/3.12.15/x64/lib`.
With no loader override, `/proc/self/maps` showed the following actual loaded
objects; symbol resolution and dpkg file checks confirmed shared Python/libpq TLS:

| Loaded object | Provenance | SHA-256 |
| --- | --- | --- |
| `/opt/hostedtoolcache/Python/3.12.15/x64/lib/libpython3.12.so.1.0` | Official artifact above; identical bytes | `c7c1a2206db0fa089f8a252d1652d5ccb82669774dbb599427082ffc9eaaae85` |
| `/usr/lib/x86_64-linux-gnu/libpq.so.5.18` | `libpq5:amd64 18.6-1.pgdg24.04+2` | `e6f752c5ad7005afab0d9ea2932b93d6ad2d863b4333a55aafa1cdd734e74bde` |
| `/usr/lib/x86_64-linux-gnu/libssl.so.3` | `libssl3t64:amd64 3.0.13-0ubuntu3.16` | `a40e7b67489bbd257e432c700ff8f6a1ad796425b5eccecdc2930127fdd86624` |
| `/usr/lib/x86_64-linux-gnu/libcrypto.so.3` | Same Ubuntu package | `6866ed711dab8927e1bf82bf1cd30e828bdfc34f3cc1e83b1c9eb01ce6c37d9a` |

The process reports OpenSSL 3.0.13; the Ubuntu package revision supplies the
[CVE-2026-35189 backport](https://ubuntu.com/security/CVE-2026-35189).
Psycopg remains 3.3.6/Python, SQLAlchemy 2.0.54 and runtime libpq 180006.
No extra OpenSSL or psycopg-binary is accepted. Setting the exact failing runner
loader path reproduced `Native library overrides are forbidden`; removing it in
this controlled comparison passed the unchanged gate. The workflow solution does
not unset it: it prevents `setup-python` from adding it in the first place.
Fresh-process negative controls with copied shared libraries still reject both
`LD_PRELOAD` and `LD_LIBRARY_PATH`, without exposing override values. Workflow
regressions verify explicit interpreter selection and retention of hostile values.

The first full Ubuntu run exposed a timing-origin defect in the existing
0.3-second blackhole test: it measured only the remaining wait after the relay
barrier and an authenticated observer query (0.087s, below its 0.1s lower bound).
The failure collector now starts with the SQL operation. Its original lower and
upper bounds, server-state, uncertainty and connection-invalidation assertions
are unchanged. This corrects the measurement without loosening the deadline gate.
The initial failure log is retained; it was not discarded as a passing retry.
All 28 permanent deadline scenarios then passed in 56.28s; the separate asyncpg
negative control also passed (29/29 total). Blackholed query failures measured
0.3011s / 5.0014s / 15.0017s against 0.3s / 5s / 15s client budgets, with confirmed
aborted server transactions and locally invalidated connections.

**VERIFIED after the correction:** 1,024 Python tests passed, including 155
PostgreSQL integration tests. The only two skips remain PostgreSQL 17-only MAINTAIN
privilege cases on PostgreSQL 16.15; no new skip or warning filter was added.
Frozen installation, Ruff, mypy (159 files), 94 focused native/workflow checks,
all 29 deadline scenarios, Alembic revision 0006/drift checks and independent
fixture backup/restore passed. Restored table counts match and constraints validate.
The locked audit found no known vulnerabilities/adverse statuses in 47 packages;
the unchanged native audit separately passed the CVE/backport and linkage checks.
Actionlint 1.7.12, bash syntax and ShellCheck 0.9.0 passed for all five changed
workflow shell steps. Frontend sources and dependencies are unchanged; their new
hosted run remains a separate gate. The original frontend/resource evidence
is preserved, not presented as a new run.
At local acceptance, the new hosted run is pending; its result must be checked on
PR #5 after the authorized push. **Render remains BLOCKED** pending its own actual
native provenance and reproducible supply evidence. No collection workflow was
invoked and no Neon, existing database or cloud permission was accessed.
Private reproduction evidence: `build/radar-ci-loader-pgbz5gq6/evidence/`
(`python-artifact.json`, `ubuntu-image.json`, `setup.log`, `loader-probe.log`,
`focused.log`, `full-python-initial-timing-failure.log`, `full-python-final.log`,
`deadlines.log`, `reference-control.log`, `audit.log`,
`actionlint.log`, `shell.log`). These temporary artifacts are excluded from Git.

## Original migration acceptance status

| Status | Evidence / limitation |
| --- | --- |
| IMPLEMENTED | System-linked psycopg, fail-closed native provenance gate, mandatory verified TLS/channel binding, UTC, bounded I/O and owned cleanup, explicit uncertainty, migrated entrypoints/CI/runbooks |
| VERIFIED | 1,019 Python passes; 155 PostgreSQL integration passes; 2 PostgreSQL 17-only skips; all 53 frontend tests; Ruff, mypy, actionlint, frozen installation, production build, schema validation, TLS/HTTP/permissions/recovery and constrained local runtime |
| VERIFIED | Bundled psycopg/OpenSSL removed; actual Debian 12 libssl3 3.0.22-1~deb12u2 backport, libpq 18.6 and Python shared TLS linkage verified; all 29 deadline scenarios passed |
| BLOCKED | Render native library provenance and reproducible supply, including Python TLS linkage; no provider image acceptance is inferred from local Debian tests |
| NOT VERIFIED | This revision on hosted CI, Render's OS/edge/build handoff/cold starts, Neon restricted-role runtime or provider recovery, encrypted independent backup custody, production-size load |
| BLOCKED | Neon Stage 2 requires separate operator approval; provider-owned postgres PUBLIC privileges remain unresolved; Render activation and independent backup custody gates remain open |

No existing application database, private credential file or Neon endpoint was
accessed. All database changes were in newly created loopback fixtures containing
synthetic data. No historical data was imported. Migrations 0001–0006, schema models,
`deploy/roles.sql`, the collection workflow activation gates and frontend source
are unchanged. `PRE-BETA-UX-001` (320 px overflow) remains open.

## Root cause and packaging decision

The previous psycopg-binary 3.3.6 wheel loaded OpenSSL **3.5.8**, affected by
**CVE-2026-35189**. The crafted-certificate allocation issue matters to a 512 MiB
client even though Python dependency audits pass. The upstream 3.5 line fix is
3.5.9. No exception, TLS downgrade or exploit-unreachability claim was used.
[OpenSSL advisory](https://openssl-library.org/news/secadv/20260929.txt).

**Selected A:** plain `psycopg==3.3.6`, Python implementation, dynamically linked
to reviewed system libpq **18.6**. SQLAlchemy stays **2.0.54**. Relative to the
previous uncommitted migration, `pyproject.toml` drops only the binary extra and
`uv.lock` drops only the psycopg-binary distribution/extra edges. No other locked
version changes. Compared with main, asyncpg 0.31.0 is removed, psycopg 3.3.6 and
Windows-only tzdata 2026.6 are added, and SQLAlchemy is explicitly pinned. There
is no asyncpg or psycopg-binary production dependency. Frozen installation passes.

**B evaluated, not selected:** `psycopg[c]` would use the same system libpq/OpenSSL;
compilation does not repair an affected shared library or establish Render image
provenance. A passed functionality/resource gate for A avoids a C build toolchain
and another implementation to review. No C variant was built.

**C conditional, not implemented:** an immutable Docker artifact could supply a
reviewed OS/Python/libpq combination if Render native cannot. That is a separate
deployment decision requiring review. No Dockerfile, Blueprint runtime switch or
cloud action was made. The Debian namespace below is a disposable test environment.
[Official psycopg installation choices](https://www.psycopg.org/psycopg3/docs/basic/install.html).

The host itself was unsuitable: its libpq loaded OpenSSL **3.6.4**, while its
uv-managed CPython embedded OpenSSL **3.5.5**. Merely removing the wheel would leave
these affected builds. The new gate rejects that environment. No host OS library
was modified; all database acceptance ran in the patched disposable Debian system.

## Native provenance and vulnerability gate

Test platform: Linux x86_64, Debian **12**, CPython **3.12.13** built from the
checksum-verified official source with shared system OpenSSL, psycopg **3.3.6**
`pq.__impl__ == "python"`, libpq `PQlibVersion() == 180006`, PostgreSQL server
**16.15-1.pgdg12+2**. Dependency packages came through signed Debian and official
PGDG HTTPS repositories. PGDG key fingerprint:
`B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8`. Signed InRelease metadata, package
versions, apt policy, source hashes and loader evidence are retained privately.

| Actual loaded object | Installed package | SHA-256 |
| --- | --- | --- |
| `/usr/lib/x86_64-linux-gnu/libpq.so.5.18` | `libpq5:amd64 18.6-1.pgdg12+2` | `9d4cc38691c249a7a32017a03bfafeee93b3a67759df8a7e55c09470085302b1` |
| `/usr/lib/x86_64-linux-gnu/libssl.so.3` | `libssl3:amd64 3.0.22-1~deb12u2` | `cab3f5b0a1febd4a80db5319f3bca4a9378c797dc6e5dc8bce36a575eaa77e78` |
| `/usr/lib/x86_64-linux-gnu/libcrypto.so.3` | same `libssl3` package | `aa01e614af634bc7c8b678c9250b5c4f613c61989bacf0a5d6aed6570e7dcabd` |

Actual `OpenSSL_version(0)`: **OpenSSL 3.0.22 25 Aug 2026**. Debian's security
tracker identifies **3.0.22-1~deb12u2** as fixed; u1 remains affected. The lower
upstream major/minor does not invalidate this vendor backport. Ubuntu 24.04's
reviewed CI baseline is **libssl3t64 3.0.13-0ubuntu3.16**. That package policy is
tested; the actual hosted Ubuntu runner has not been run in this turn.
[Debian CVE record](https://security-tracker.debian.org/tracker/CVE-2026-35189),
[Ubuntu CVE record](https://ubuntu.com/security/CVE-2026-35189).

`python -m github_radar.native` follows symbols from psycopg's actual ctypes
handle with dladdr, checks `/proc/self/maps`, package versions and package-file
integrity, and emits only non-secret identities/paths/hashes. Python `_ssl` and
`_hashlib` must resolve to those same system libraries. Static/bundled Python TLS,
additional mapped OpenSSL objects, an installed psycopg-binary distribution,
unsupported implementations/versions/OS releases and library/config overrides
fail closed. The audit runs during credential-free builds, before database engine
creation, and before credentials enter collection workflow steps. Its successful
filesystem/package scan is cached per process; the cheap implementation/override
guard is repeated. Package upgrades require process restart and renewed evidence.

This verifies package bytes against dpkg's installed manifest, not a compromised
root account. Signed package acquisition is a separate provenance requirement.
The ELF inventory shows no remaining bundled psycopg native libraries and no
additional loaded OpenSSL copy in the tested runtime. Other extensions remain
(e.g. pydantic-core, greenlet and SQLAlchemy). This is a scoped native OpenSSL
finding/provenance check, **not** a complete OS or static-library vulnerability
scanner and not a claim that every native component has no CVEs. `uv audit` and
npm audits remain separate gates; Python 3.12.13 is retained rather than silently
upgraded as part of packaging remediation.

`deploy/install_ci_native.sh` prepares ephemeral Ubuntu 24.04 Actions runners
using signed vendor/PGDG repositories, then the actual loaded-library audit must
pass. CI separately installs PostgreSQL 16 tools. Collection plans and gated live
jobs run the same secret-free native setup/audit; activation conditions, cron,
protected environment, step-scoped secrets and no-rerun rule are unchanged. This
script is **not** a Render installer. Future libpq version drift fails the exact
180006 guard until explicitly reviewed; allowed vendor security revisions must
still meet the fixed package baseline and loaded-file checks.

**Render supply remains unverified.** Render documents Debian 12 native images,
but that does not prove its libpq version, installed Debian backport, dpkg metadata
availability or Python's shared OpenSSL linkage. The build/runtime guard refuses
an incompatible image. The local Debian build proves the artifact commands work
when these dependencies are supplied; it does not prove Render lets us supply
them reproducibly. Require exact image evidence and an approved repeatable supply
path before activation; otherwise review option C separately.
[Render native runtimes](https://render.com/docs/native-runtimes).

## Implementation and audited entrypoints

- `storage/connection.py`: explicit psycopg URLs; raw duplicate/blank/malformed
  query detection before SQLAlchemy can normalize input; direct hostname and
  identity validation. Only verify-full, required binding and an absolute CA
  path are accepted. No legacy SSL alias, unknown option, pooler or weak fallback.
- Public and owner URLs explicitly select verify-full for remote connections;
  omitted binding is fixed to require. The dedicated collector keeps its existing
  query-free URL and sets both security parameters internally. Loopback also
  requires TLS/SCRAM. Usernames/passwords are never guessed or rewritten.
- Libpq receives explicit CA trust, minimum TLS 1.2, required binding, UTC,
  disabled GSS/client-certificate/key-log/password-file alternatives and finite
  connect limits. Nonempty ambient PG variables are refused. Collector CA PEM is
  validated and written to a private temporary bundle only during authentication.
- `storage/deadlines.py`: aggregate connection deadline including async DNS;
  explicit closure of partial connection generators; per-protocol I/O deadlines.
  Interrupted connections are closed locally and invalidated, never returned as
  healthy pooled sessions. Cancellation keeps its type and uncertainty marker.
- `storage/db.py`: one engine factory, public pool 2/no overflow, collector
  NullPool, UTC/read-only/session limits. Managed engine/session contexts own
  shielded cleanup tasks until completion/failure, including repeated cancellation.
  The two read-only CLI diagnostic sessions also use managed cleanup.
- Collector ownership checks still run on the pinned backend before each protected
  commit. The normal SQLAlchemy do_connect event path is preserved. A replacement
  connection cannot authorize more writes after loss; no whole-run replay exists.
- SQLAlchemy sessions, ORM binding/JSON/upserts, public API/readiness, local CLI,
  refresh/discovery, Alembic, initializer, permission audit and recovery all use
  the shared stack. Generic HTTP/CLI errors explicitly indicate uncertainty when
  a database outcome is unknown, without rendering driver text or SQL parameters.
- Backup/restore retains snapshot consistency, exclusive 0600 archive creation,
  empty target checks, distinct source identity and exact local target name.
  Both source and target now require reviewed TLS/channel-binding options. No
  remote restore, overwrite, destructive cleanup or automatic retry is added.

The adapter deliberately uses version-coupled interfaces: psycopg's `_connect_gen`,
`_closed`, `waiting.wait_async`/`PGconn.finish`, and SQLAlchemy's `async_creator_fn`
and engine connection class. Installed source was re-read and hashed, including
native cancellation/draining, disconnect detection and shielded context cleanup.
A runtime guard refuses other driver/libpq/SQLAlchemy versions or implementations.
The top-level SQLAlchemy async_creator shortcut is not used because it would bypass
collection's do_connect refusal hook. These pins require explicit upgrade review.
[SQLAlchemy psycopg async dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg),
[pinned psycopg source](https://github.com/psycopg/psycopg/blob/3.3.6/psycopg/psycopg/connection_async.py).

## Deadline and failure contract

| Boundary | Client limit | Server defense / distinction |
| --- | --- | --- |
| All runtime engine authentication | 10s aggregate DNS/address/TLS/auth deadline | libpq connect_timeout=10; no insecure alternate connection |
| Public pool acquisition | 3s queue, 2 connections / 0 overflow | Authentication/pre-ping are additional bounded operations |
| Public I/O | 5s per protocol operation | Statement 5s, lock 2s, idle transaction 10s, read-only |
| Collector I/O | 15s per protocol operation, NullPool | Statement 15s, lock 5s, idle transaction 30s |
| Local CLI, owner, migration and recovery SQL | 10s connect / 30s per I/O | Statement 30s, lock 5s; explicit new owner-tool bounds |
| Readiness / public startup | 3s / 15s cooperative outer limits | Role/schema audit only; no repair/migrations |
| Autonomous collection / wrapper | 300s / 330s cooperative execution limits, unchanged | Durable initial receipt and cleanup may lie outside inner scope |
| Automation diagnostics | 60s cooperative outer limit | Read-only snapshot |
| pg_dump / pg_restore | 5s connection; 120s subprocess limit each | Statement 30s, lock 5s; verified TLS and binding |
| Cleanup | Each rollback/unlock/close I/O has its profile's driver bound | Parent retains/consumes child result; repeated cancellation cannot reset or detach it |

Deadlines operate below execute/ORM flush/ownership hooks/commit/rollback/pre-ping
and server-cursor fetch. Sessions remain single-consumer objects. They do not bound
CPU work, an OS resolver thread, arbitrary third-party lock holders or every full
HTTP request. A high-level operation can perform several bounded protocol calls;
outer cancellation may be followed by a bounded cleanup tail. **330 seconds is
not claimed as an exact whole-process wall-clock bound.**

Local connection closure does not prove remote cancellation, rollback or immediate
advisory unlock. A COMMIT with a missing acknowledgement is uncertain whether its
request was dropped or its reply was dropped. Prior commits remain durable; an
unfinished running receipt can truthfully remain without finished_at. The guard
latches ownership loss and refuses further execute/commit/reconnect. A later public
checkout may obtain a new backend, but cannot replay the failed transaction.
Ordinary server SQLSTATE 57014 permits reuse only after confirmed rollback. Healthy
cancellation during fake HTTP can still record a confirmed partial receipt.

## Deterministic regression evidence

Reproduction (reviewed Debian 12 / Ubuntu 24.04 native packages, dynamically
linked Python TLS and PostgreSQL 16 tools required; no operator DSN needed):

```sh
uv sync --frozen
uv run --frozen python -m github_radar.native
uv run --frozen python -m tests.run_postgres
```

`RADAR_TEST_POSTGRES_BIN` optionally selects a tools directory. The runner creates
new TLS/SCRAM clusters with private random fixture passwords and non-UTC server
defaults; tests must still observe UTC. It supplies a deliberately unreachable
application DSN and never reads an existing database. With no arguments it also
checks Alembic head/drift and a fresh snapshot backup/restore on a second cluster.
CI uses this same runner; existing workflow permissions, trust/activation checks,
action pins and security audits remain intact.

Full run: **1,019 passed, 2 skipped, 192.77 seconds**. There are **157 PostgreSQL
integration cases: 155 passed and 2 skipped** for PostgreSQL 17-only MAINTAIN grants.
The prior 996 passing cases remain, with 22 native-provenance cases and a real
public-pool queue/recovery case added. After completing workflow wiring, the 89
native/workflow checks passed again. No assertions were weakened and no skips were
hidden. The full run also passed migration 0001–0006, Alembic drift detection and
independent fixture backup/restore with equal table counts and valid constraints.

**29/29 deadline scenarios passed:** 28 permanent production-adapter scenarios
(including the native psycopg negative control), plus the original asyncpg 0.31.0
negative control rerun in a separate reference-only environment. Asyncpg is absent
from the application lock/environment. TLS/SCRAM trust-auth refusal, hostname/CA
failure, UTC, actual restricted roles, private-route isolation and connection
failure/recovery are also covered by the full suite. Both negative controls retain
their original purpose: demonstrate why a caller timeout alone is insufficient.

The TLS-transparent relay holds/drops bytes at explicit SQLAlchemy/transport
barriers. Independent observer queries establish backend/lock/row state; polling
waits for these predicates instead of treating a sleep as concurrency evidence.
It simulates blackholes and lost commit replies, not a production packet-loss SLA.
Representative measured results from the focused run of this production adapter:

| Fault / control | Observation |
| --- | --- |
| Stalled handshake, configured 10s | Failure at 10.0179s; socket EOF even with exception retained |
| Stalled DNS, scaled 250ms | Failure at 250.4ms; no transaction started |
| Long SQL, 300ms client / 30s server | Failure at 339.2ms; observer still sees server executing |
| Server statement timeout, delivered | SQLSTATE 57014 at 71.0ms; usable after rollback |
| Server timeout reply blackholed, real public/collector budgets | 5.0017s / 15.2270s; backend already aborted, client invalidated |
| COMMIT request held / committed reply held | Both uncertain, observer proves opposite durable outcomes; zero reconnect/replay |
| Cancellation in ownership check | 0.6ms to failure; prior commit preserved; no further authorized writes |
| Cancellation after real CollectionService write/receipt commit | 0.9ms; one durable repository, running unfinished partial receipt, two reserved attempts, one run invocation |
| Stalled rollback / close | 301.5–301.7ms; later close/dispose below 1ms |
| Repeated caller cancellation during cleanup | Caller retains cleanup child through its deadline; failure consumed, connection abandoned |
| Rival collector while remote session remains stalled | Refused before/after original caller failure; only a separately invoked run succeeds after actual remote unlock |
| Readiness with a held reply | False at 251.9ms for scaled 250ms limit; missing schema false; later independent checkout recovers |
| Wrapper with scaled 250ms / 15s I/O | Failure including guard cleanup at 252.9ms |
| Public pool saturation / independent recovery | 3.0022s queue failure; exactly two connections, no overflow; poisoned connection replaced on a later checkout |
| Native psycopg negative control, 300ms outer | Returns at 5.3137s because native cancellation/draining extends the caller timer |

The real CollectionService's inner timer is also tested at a scaled 1s budget and
confirmed a terminal partial receipt with one reserved attempt at 1.117s. Actual
300/330s constants remain covered by policy tests; no live 330s collection was run.
The reference-only asyncpg rerun observed query failure at 301.2ms and its
native 100ms close limit exceeded a 500ms outer watchdog (501.0ms observed), which
finally aborted the connection. This expected negative control passed; it is no
production fallback. The production-adapter negative control similarly demonstrates
native psycopg cancellation/draining extending the caller's outer timeout.

## Other checks and resources

- Ruff and mypy pass (159 source/test files); shell syntax and actionlint 1.7.12
  pass. An initial actionlint compile exhausted the shared /tmp filesystem; the
  same validator passed using this task's disk-backed temporary directory.
- Frontend: 53 tests, ESLint, TypeScript, Vite build, full and production npm
  audits pass. No frontend source/lockfile change. Existing npm deprecation and
  uv experimental-audit warnings remain visible.
- `uv audit --locked`: no known vulnerabilities/adverse project statuses in 47
  audited packages (48 resolved including the application). This does not cover
  native libraries; the independently verified vendor backport is required.
- Credential-free `bash deploy/build.sh` passes with Python 3.12.13 / Node 24.14.1 /
  npm 11.11.0 / uv 0.11.6. The pure-Python wheel requires no psycopg compiler.
  A fake credential marker causes refusal before dependency commands and is not
  echoed. The first disposable attempt lacked a `python` PATH alias; correcting
  the harness PATH made the unchanged build pass. Actual Render remains blocked.
- The unchanged Blueprint passes local validation against the previously retrieved
  official Render schema: Python runtime, autoDeployTrigger off, one instance and
  assigned PORT. No Render API/provisioning was used.
- Real HTTP smoke checks the compiled SPA, public API, hostile Host/forwarding
  headers, read-only grants, private/AI denial, generic errors and DB recovery.
  The first namespace run had a /proc PID-view mismatch in its resource assertion;
  adding a matching disposable PID namespace fixed the harness. No assertion was
  changed. The full final suite passed with the corrected environment.

Actual cgroup v2 limits: `cpu.max = 10000 100000` (0.1 CPU),
`memory.max = 536870912` (512 MiB). One Python web process, two-connection pool,
three synthetic repositories, 30 serial repository reads. The Debian namespace
adds three setup/reaper processes within the cgroup; PostgreSQL is outside it.

| Measurement | Previous binary implementation | System-linked Python implementation |
| --- | --- | --- |
| Peak web RSS | 102352 KiB / 99.95 MiB | **91444 KiB / 89.30 MiB** |
| Cgroup memory peak | 78057472 bytes | **86589440 bytes** |
| Startup to healthy | 9.754s | **16.637s** |
| Median read latency | 192.442ms | **294.111ms** |
| p95 read latency | 197.487ms | **390.398ms** |
| Maximum read latency | 198.224ms | **402.054ms** |

Health/readiness, SPA/public API, private denial and hard cgroup limits passed.
This modest workload fits the resource limit, but throughput/startup are slower.
OS/Python builds, namespace overhead and host scheduling differ, so this is not a
controlled driver-only benchmark. Memory accounting differs between RSS and cgroup
peak. No real Render performance, production-size load, sustained concurrency or
cold-start SLA is claimed. Client connection/query limits remain 10s/5s/15s and
were separately tested against deterministic stalls.

Private evidence root: `build/radar-native-psydqijz/` (0700, ignored by Git).
Important files under `evidence/`: `full-python-1.log`, `deadlines-final.log`,
`asyncpg-negative-control.log`, `final-affected.log`, `native-inventory.log`,
`host-native-libraries.json`, `host-fail-closed.log`, `debian-native-policy.txt`,
`debian-packages.txt`, signed `*InRelease` files, `debian-image.json`,
`resource-results.json`, `resource-controller.log`, `render-build-final.log`,
`frontend.log`, `python-audit.log`, `actionlint.log`, `render-schema-validation.json`
and `final-file-manifest.json`. `harness/`, `bootstrap.sh`, `run-debian.sh` and
`resource-launch.sh` retain the disposable reproduction procedures. Official Debian
filesystem amd64 digest:
`sha256:a3bba7dc7dbcc1f37c2856c51fc9a7fac96608f50cb87b1b4e4d8b8c85ae988e`.
No existing DB/credential files were mounted. Synthetic passwords, TLS keys and
archives remain private fixtures; these are not encrypted independent backup custody.
Historical binary comparison evidence remains under
`/tmp/radar-production-migration-ooc0s_yk/`. All disposable PostgreSQL instances
started for this remediation were stopped. No host DB service was accessed.

## Local configuration, rollout and rollback

1. Keep deployment/collection stopped. Review this system-linked correction and
   obtain green hosted CI. Prove Render's exact native/Python linkage and repeatable
   supply path; a Debian label or Python dependency audit alone is insufficient.
2. On a reviewed patched OS with dynamically linked Python TLS, install the lock
   with `uv sync --frozen` and run `python -m github_radar.native`. Unknown OSes,
   static Python SSL and affected backports fail closed; do not bypass the guard.
   Update private URLs manually to `postgresql+psycopg://` while preserving exact identity and
   URL-encoded credentials. Add verify-full, require binding and trusted CA as
   appropriate. Do not modify another application's .env or weaken local TLS.
3. For the collector, keep its query-free direct URL and protected CA PEM if needed;
   the factory supplies mandatory options. Reject provider options outside the
   approved contract rather than deleting them silently. Confirm UTC and direct
   session continuity during separately authorized Neon acceptance.
4. No schema migration is needed for this driver change. Neon initialization,
   SQL-created restricted roles/passwords, actual denied privileges, encrypted
   backup custody and the provider-owned postgres PUBLIC CONNECT/TEMPORARY issue
   remain separate blocked acceptance work. No permission exception is introduced.
5. Render still requires the credential-free build/runtime handoff, actual TLS
   edge/Host/cold-start checks, quota review and exact-head hosted CI. No activation
   is implied by a local pass. Neon proxy termination can make compute pg_stat_ssl
   false while client-to-proxy TLS is valid; verify client libpq TLS settings.
6. Rollback is an application/lockfile switch to a recorded reviewed artifact,
   without database downgrade, reset or replay of uncertain runs. Preserve durable
   rows and receipts and review unfinished work first. The baseline asyncpg build
   does not enforce mandatory channel binding: reverting source is possible,
   but running that build under this new production policy is **not an approved
   fallback**. The previous psycopg-binary artifact is also prohibited by its native
   CVE. Keep services stopped rather than rolling back to either insecure runtime.
   No schema downgrade or data repair is needed for the packaging change.

## Complete changed-file manifest

The list below includes the preserved migration plus this correction: **51 files**.
This remediation touches 19 of those paths: dependency declarations/lock, the
native gate and adapter guard, build/CI/collection setup, native/pool/workflow tests,
the test runner and five runbooks. Three new files are `native.py`,
`test_native_provenance.py` and `deploy/install_ci_native.sh`. The starting 45-file
migration was preserved; no pre-existing changed file was deleted. Native package
provenance does not change SQL, schema, permission policy or deadline algorithms.

- `.env.example`
- `.github/workflows/ci.yml`
- `.github/workflows/collection-discover.yml`
- `.github/workflows/collection-refresh.yml`
- `README.md`
- `alembic.ini`
- `deploy/build.sh`
- `deploy/install_ci_native.sh`
- `docs/ARCHITECTURE.md`
- `docs/AUTONOMOUS_COLLECTION.md`
- `docs/PHASE8C_B_MIGRATION_VERIFICATION.md`
- `docs/PRODUCTION_READINESS.md`
- `docs/PUBLIC_DEPLOYMENT.md`
- `migrations/env.py`
- `pyproject.toml`
- `src/github_radar/api/deployment.py`
- `src/github_radar/api/errors.py`
- `src/github_radar/cli/app.py`
- `src/github_radar/cli/automation.py`
- `src/github_radar/cli/collection.py`
- `src/github_radar/config/settings.py`
- `src/github_radar/native.py`
- `src/github_radar/recovery.py`
- `src/github_radar/services/automation.py`
- `src/github_radar/storage/collection.py`
- `src/github_radar/storage/connection.py`
- `src/github_radar/storage/db.py`
- `src/github_radar/storage/deadlines.py`
- `tests/conftest.py`
- `tests/integration/test_automation.py`
- `tests/integration/test_database_deadlines.py`
- `tests/integration/test_deployment_stack.py`
- `tests/integration/test_postgres_flow.py`
- `tests/integration/tls_relay.py`
- `tests/postgres.py`
- `tests/run_postgres.py`
- `tests/unit/test_ai_safety.py`
- `tests/unit/test_alembic_env.py`
- `tests/unit/test_api_routes.py`
- `tests/unit/test_automation.py`
- `tests/unit/test_collection_cli.py`
- `tests/unit/test_config.py`
- `tests/unit/test_connection_security.py`
- `tests/unit/test_github_client.py`
- `tests/unit/test_native_provenance.py`
- `tests/unit/test_public_deployment.py`
- `tests/unit/test_public_server.py`
- `tests/unit/test_recovery.py`
- `tests/unit/test_test_database_safety.py`
- `tests/unit/test_workspace.py`
- `uv.lock`
