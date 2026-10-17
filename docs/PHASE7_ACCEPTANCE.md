# Phase 7 acceptance review — 2026-10-17

Scope: the complete Phase 7 change, including new files and
`NETWORKING_WORKSPACE.md`. Base commit: `2fe50a9`.

**Decision: accepted for the supported local repository workspace; ready to commit.**
Developer acceptance and restore testing remain open. Public/proxy deployment is
unsupported. These limits do not block the scoped Phase 7 implementation commit.

## Operator acceptance and finalization

- The operator confirmed that the real local Networking Workspace opens,
  authenticates and supports repository watchlist operations.
- The operator applied `0005 → 0006` after creating a backup and validating its
  archive listing. A full restoration was not tested.
- Final read-only database checks independently confirmed revision `0006`,
  **25 repositories, 25 observations, 219 GitHub topics and 0 developers**.
  Hashes of every pre-existing public-data table match the pre-migration review
  snapshot, excluding Alembic bookkeeping; they also match before/after the
  final API smoke. Migrations `0001`–`0005` remain byte-identical to the base commit.
- GET-only probes against the running local server passed: anonymous, wrong-token,
  duplicate-header and query/cookie claims return 401; foreign Host/Origin,
  cross-site and forwarding headers return 403; authenticated session/lists return
  200. No-store, no private CORS permission, shell security headers and public API
  isolation were verified without printing tokens or private responses. The live
  listener was confirmed bound only to `127.0.0.1:8000`.
- `.env`, `.env.workspace`, build outputs and caches are ignored; no database dumps
  were found in the checkout. Database dump patterns are also ignored. The commit uses
  an explicit Phase 7 file allowlist and excludes credentials/generated artifacts.

The real developer watchlist cannot yet be accepted because the application has
no developers. Its behavior is covered by automated tests and synthetic browser
fixtures; no discovery or enrichment was performed to manufacture acceptance data.

## Findings and corrections

- **Fixed: delayed authentication could undo a pagehide lock.** A successful
  `/session` response arriving after `pagehide` restored the token and private
  view. A regression test reproduced the failure before the correction.
  `WorkspaceLayout` now invalidates pending unlock attempts on lock/unmount and
  ignores obsolete responses. No authentication policy or API contract changed.
- No other correctness or security defect was established in the supported
  direct-loopback configuration. Added coverage closes acceptance evidence gaps:
  all registered private endpoints when disabled, anonymous reads/writes/deletes,
  public route isolation, empty watchlists, unavailable access, both tables'
  constraints/cascades, and full legacy-row comparison during migration.
- Migration `0006` is additive: two private tables only, with primary/foreign
  keys, `ON DELETE CASCADE`, non-null fields and status/notes/version checks.
  Migrations `0001`–`0005` are unchanged. Its downgrade deletes private entries;
  use it only with an intentional plan to lose that private state.

## Boundary assessment

| Area | Result and evidence |
| --- | --- |
| Disabled default | Empty token returns 503 before route dispatch, validation or DB access; tested against every registered private method/path. |
| Authentication | Exactly one matching bearer header required; constant-time comparison. Missing/wrong credentials, cookie/query substitutes and duplicate headers fail closed. Token validation rejects malformed configuration. |
| Direct local operation | Actual peer must be loopback and Host must be local. Standard launcher rejects remote binding when enabled and disables Uvicorn proxy-header trust. |
| Forwarding and browser origin | Forwarded, X-Forwarded-* and X-Real-IP rejected; foreign/duplicate Origin and cross-site requests rejected. No private CORS permission. A proxy that strips identifying headers cannot be distinguished from a local client and is unsupported. |
| Every private endpoint | Middleware guards the whole namespace, including unknown paths/methods and encoded paths. GET lists/session, PUT and DELETE all require the boundary before touching storage. |
| Token handling | React memory only; Authorization header only; no browser storage, query credential, build-time secret or application logging of credentials. Bundle probe and repository/config inspection found no credential inclusion. Logs/OS/browser must remain admin-controlled. |
| Public API isolation | Explicit public serializers and evidence loaders do not join private tables. Regression probes cover lists, details, search, topics, explore, ecosystem, bridges, AI status/artifacts and status while private entries exist. |
| Browser data | No-store responses and HTML; CSP, no-referrer and framing restrictions. Lock/unmount/reload/pagehide remove the private view; late unlock responses cannot restore it. Plain-text notes and safe contact URLs; no remote contact fetching. |

The UI explains the unconfigured/local-only response after an unlock attempt;
it deliberately makes no private data request before the administrator submits
credentials. An empty watchlist directs the administrator to “All tracked”.
Unsaving retains notes/status; confirmed deletion removes only private state.
Version conflicts retain the draft. Save before navigating or locking: drafts
are not persisted and internal navigation is not a draft recovery mechanism.

## Verification

- Python: **565 passed**, including **52 guarded PostgreSQL cases**, no skips.
- Ruff and mypy: passed; frontend lint, TypeScript, **47 Vitest tests** and
  production build: passed. Existing Alembic/Starlette deprecation warnings remain.
- Guarded PostgreSQL: `0005 → 0006 → 0005`, identical complete rows in every
  pre-existing table at each comparison; reflected migration keys/constraints
  and nullability, both private table writes, FK/status/length/null checks,
  cascades, persistence and concurrent revision conflicts verified.
- Built-bundle Chromium smoke: developer save/filter, repository save/delete,
  keyboard interaction, reload lock, anonymous HTTP rejection; no console errors
  or horizontal overflow at 320/768/1024/1440 px. Synthetic SQLite fixtures only.
- Runbook shell/Python blocks pass syntax checks; the API probe passes against
  the disposable fixture server. Its normal access logs contain neither the
  synthetic credential nor acceptance note. A configured-credential scan of 217
  source/build files and a synthetic build-token probe found no credential leakage.
- Pre-migration review checks were read-only: revision `0005`, 25 repositories, no
  workspace tables, workspace disabled. Before/after hashes compare every row
  in all 12 public-schema tables without exporting row contents.

The operator has now accepted the real repository workspace. Developer operations
with real data and backup restoration remain unverified; public/proxy deployment
remains unsupported. The full suite results above are from the acceptance review.
Finalization changes only documentation and backup exclusions, with fresh read-only
local checks; it does not rerun destructive integration fixtures.
Focused pre-commit checks passed again: 77 workspace Python unit tests, 12 workspace
frontend tests, Ruff, mypy, frontend lint and TypeScript.

## Original 0005 → 0006 local acceptance runbook — reference

The installation is already at `0006` with its private token configured. The
operator completed backup/migration and repository acceptance; these original
commands are retained for reproducibility on another `0005` installation.
Do not replay the setup or write probes during read-only finalization.

Use a private terminal on the database host. Stop existing Radar servers and
scheduled collectors before the backup; leave collection stopped throughout.
Do not run discovery, enrichment, refresh, AI generation or outreach commands.
Run from this checkout, with its existing Python/frontend dependencies installed.
The configured application target reviewed here is `github_radar` on loopback
port 5433; `github_radar_test` is reserved for destructive tests.

### 1. Back up the application database

Clear shell overrides so the reviewed `.env` is authoritative. The script below
refuses another target, keeps credentials out of process arguments and creates
a private custom-format dump outside Git. It checks the archive listing, not a
full restore. Keep the resulting backup path and protect it as private data.

```sh
cd /home/zinvernix/projects/github-radar
unset DATABASE_URL TEST_DATABASE_URL WORKSPACE_ADMIN_TOKEN
umask 077
chmod 600 .env
uv run --env-file .env alembic current
# Must report 0005. Stop if the revision or target differs.
uv run --env-file .env python - <<'PY'
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.engine import make_url
from github_radar.config import Settings

url = make_url(Settings().require_database_url())
assert url.database == 'github_radar'
assert url.host in ('127.0.0.1', 'localhost') and url.port == 5433
assert not url.query, 'Review custom connection parameters before backup'
directory = Path.home() / '.local/state/github-radar/backups'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
directory.chmod(0o700)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
dump = directory / f'pre-0006-{stamp}.dump'
env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
env.update(PGHOST=url.host, PGPORT=str(url.port), PGDATABASE=url.database,
           PGUSER=url.username or '', PGPASSWORD=url.password or '')
with dump.open('xb') as output:
    subprocess.run(['pg_dump', '--format=custom'], env=env, stdout=output, check=True)
subprocess.run(['pg_restore', '--list', str(dump)], stdout=subprocess.DEVNULL, check=True)
assert dump.stat().st_size > 0
print('Backup:', dump)
print('SHA256:', hashlib.sha256(dump.read_bytes()).hexdigest())
PY
```

If backup or archive validation fails, stop. Do not migrate. For stronger
recovery assurance, independently restore to a new disposable database first;
never restore over the application database as an acceptance test.

### 2. Apply only migration 0006

Only after the backup succeeds and the operator accepts this review:

```sh
uv run --env-file .env alembic upgrade 0006
uv run --env-file .env alembic current
# Must report 0006. Do not use init-db, schema resets or downgrade as a retry.
```

### 3. Configure a separate private token and build

The file is ignored by Git and created exclusively, so this command refuses to
overwrite an existing token. It does not display the generated value. Open
`.env.workspace` in a trusted local editor to copy the value into the browser;
do not paste it into a URL, shell command, issue, screenshot or shared log.

```sh
uv run python - <<'PY'
import os
import secrets
with os.fdopen(os.open('.env.workspace', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
    output.write('WORKSPACE_ADMIN_TOKEN=' + secrets.token_urlsafe(32) + '\n')
PY
git check-ignore .env.workspace
npm --prefix web run build
```

### 4. Start the supported local application

```sh
uv run --env-file .env --env-file .env.workspace github-radar serve --host 127.0.0.1 --port 8000
```

Keep this terminal open. Use `http://127.0.0.1:8000/workspace`. Do not expose a
reverse proxy, remote bind or tunnel. Do not substitute a bare Uvicorn invocation
that trusts forwarded headers.

### 5. Verify the API locally, then the UI

In a second private terminal, this manually run probe reads the token from the
private file. It checks rejections, creates/edits/unsaves/deletes one temporary
entry per available kind, and refuses to overwrite existing private entries.
It uses only the local API and prints neither credentials nor private responses.
If a check fails, stop and inspect locally; a temporary note may remain. Do not
blindly retry writes. No stored developers means developer operations must remain
an explicit acceptance gap; do not fetch GitHub data to fill it in this phase.

```sh
cd /home/zinvernix/projects/github-radar
unset DATABASE_URL TEST_DATABASE_URL WORKSPACE_ADMIN_TOKEN
uv run --env-file .env --env-file .env.workspace python - <<'PY'
import httpx
from github_radar.config import Settings

token = Settings().workspace_admin_token.get_secret_value()
assert token
base = 'http://127.0.0.1:8000/api/v1'
auth = {'Authorization': 'Bearer ' + token}
with httpx.Client(base_url=base, trust_env=False, follow_redirects=False) as client:
    for method, path in [('GET', '/workspace/developers'), ('GET', '/workspace/repositories'),
                         ('PUT', '/workspace/developers/1'), ('DELETE', '/workspace/repositories/1')]:
        denied = client.request(method, path)
        assert denied.status_code == 401
        assert denied.headers['cache-control'] == 'no-store'
    assert client.get('/workspace/session', headers={'Authorization': 'Bearer incorrect'}).status_code == 401
    assert client.get('/workspace/session', headers={**auth, 'X-Forwarded-For': '127.0.0.1'}).status_code == 403
    assert client.get('/workspace/session', headers={**auth, 'Host': 'foreign.example'}).status_code == 403
    assert client.get('/workspace/session', headers={**auth, 'Origin': 'https://foreign.example'}).status_code == 403
    assert client.get('/workspace/session', headers=auth).status_code == 200
    for kind in ('developers', 'repositories'):
        listing = client.get(f'/workspace/{kind}?saved=all&limit=100', headers=auth)
        listing.raise_for_status()
        entity = next((item for item in listing.json()['items'] if item['workspace']['version'] is None), None)
        if entity is None:
            print(kind + ': no unused stored entity on this page; verify manually without overwriting notes')
            continue
        path = f"/workspace/{kind}/{entity['id']}"
        edit = dict(saved=True, notes='PHASE7_LOCAL_ACCEPTANCE', relationship_status='interested', version=None)
        created = client.put(path, headers=auth, json=edit)
        assert created.status_code == 200
        version = created.json()['version']
        assert client.put(path, headers=auth, json=edit).status_code == 409
        changed = client.put(path, headers=auth, json={**edit, 'saved': False, 'relationship_status': 'connected', 'version': version})
        assert changed.status_code == 200 and changed.json()['notes'] == edit['notes']
        assert client.delete(path, headers=auth, params={'version': version}).status_code == 409
        removed = client.delete(path, headers=auth, params={'version': changed.json()['version']})
        assert removed.status_code == 200
        public_path = '/developers/' + entity['login'] if kind == 'developers' else '/repositories/' + entity['full_name']
        public = client.get(public_path)
        assert public.status_code == 200 and edit['notes'] not in public.text
        print(kind + ': protected create/edit/unsave/conflict/delete passed; public entity retained')
print('Local API checks completed')
PY
```

In the browser, manually verify:

1. Wrong token shows an error with no private data. Correct token unlocks; the
   initial empty Saved lists explain how to use “All tracked”.
2. For a developer and repository, open “All tracked”, add an entry, check
   “Saved to watchlist”, enter a temporary note/status, save, and find it under Saved.
3. Reopen and edit; filter by saved state, topic and relationship. For developers,
   check minimum relevance and the existing deterministic intelligence display.
4. Inspect GitHub/public website/X link destinations and public email text when
   available. Clicking external links is unnecessary for acceptance.
5. Unsave and verify the note/status under “Not saved”. Confirm deletion and
   verify the public tracked entity remains. Do not delete pre-existing notes.
6. Lock, reload, navigate away/back and verify credentials/private notes disappear.
   In two tabs, stale edits must return a conflict without overwriting newer data.

### 6. Verify disabled behavior and stop for review

Stop the server with Ctrl-C. Restart without the private token file:

```sh
unset WORKSPACE_ADMIN_TOKEN
uv run --env-file .env github-radar serve --host 127.0.0.1 --port 8000
```

In another terminal, `curl -i http://127.0.0.1:8000/api/v1/workspace/session`
must return 503 with `workspace_disabled` and `Cache-Control: no-store`.
The UI shell may load; submitting the previous token must show that the workspace
is not configured and reveal no private data. Stop the server with Ctrl-C.
Record the results; do not proceed to deployment or Phase 8.

## Remaining risks / release blockers

- Repository acceptance, backup creation and migration are complete. Developer
  acceptance with real data and full backup restoration are pending.
- Public/proxy deployment remains blocked: no remote login/session lifecycle, TLS
  boundary verification, multi-user isolation or brute-force control.
- Token holders have full access. No expiry or per-device revocation; rotate the
  private file and restart to revoke. Header-stripping proxies, compromised local
  processes/browsers and DB administrators are outside the supported boundary.
- Notes/backups are not application-encrypted. Logs and backups need local access
  controls; live deletion does not purge backups. Do not log Authorization headers
  or enable request/SQL payload tracing with real private data.
- Whole-dataset intelligence loading remains; large-dataset latency is unverified.
  Automated browser acceptance used isolated Chromium with synthetic data; the
  operator additionally confirmed the real repository workspace. Neither is an
  application-database restore exercise or real developer-workspace acceptance.

The operator performed the application migration. Review/finalization performed
no scoring/discovery/collection changes, application database writes/resets, live
GitHub requests, StoryOps changes, deployment or push. Only the intended Phase 7
implementation, migration, tests, documentation and backup exclusions are committed.
