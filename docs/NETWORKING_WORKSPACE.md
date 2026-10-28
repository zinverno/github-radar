# Phase 7 — Networking Workspace MVP

The workspace saves developers and repositories already present in GitHub Radar,
with private notes and relationship status. It shares the existing FastAPI,
PostgreSQL, Alembic and React application. It performs no discovery, enrichment,
refresh, outreach, email sending or AI message generation.

**Local acceptance:** the operator confirmed that the real workspace opens,
authenticates and supports repository watchlist operations. The application is
at migration `0006`, with 25 repositories, 25 observations and 219 GitHub topics
preserved. A backup was created and its archive listing validated. Real developer
watchlist acceptance and a full backup restoration remain untested; there are no
stored developers yet. See the [acceptance record](PHASE7_ACCEPTANCE.md).

**Supported operation and access boundary**

This is one administrator's workspace, not a multi-tenant service. No supported
remote administrator authentication existed before Phase 7. This implementation
therefore supports **direct loopback operation with a separate admin bearer token**.
It is **not approved for public deployment**.

The complete `/api/v1/workspace` namespace is guarded before route dispatch,
body validation or database dependencies, including unknown routes and methods:

- An empty `WORKSPACE_ADMIN_TOKEN` disables access (503).
- Configuration requires 32–256 URL-safe ASCII characters. Use a randomly
  generated token, not a memorable password or the GitHub API credential.
- Each request requires exactly one matching `Authorization: Bearer …` header,
  checked in constant time. Missing or invalid credentials return 401. Cookies,
  query strings and claims of being an admin do not authenticate a caller.
- The actual ASGI peer must be loopback and the Host must name `localhost`,
  `127.0.0.1` or `::1`. Remote peers and foreign hosts return 403.
- Forwarded, X-Forwarded-* and X-Real-IP headers are rejected. The supported
  `github-radar serve` launcher disables Uvicorn proxy-header trust and refuses
  a non-loopback bind while a workspace token is configured.
- Requests with an Origin must match the exact request scheme and Host.
  Conflicting Origin headers and cross-site browser requests are rejected.
  There is no CORS permission for private API access.
- Private responses use `Cache-Control: no-store`. The SPA HTML is also
  uncacheable and carries a CSP, anti-framing, no-sniff and no-referrer headers,
  including when the user enters through a public page and navigates to Workspace.
- React keeps the token and private results in memory. There are no workspace
  cookies, localStorage/sessionStorage credentials, service workers or private
  browser persistence. Lock, reload, leaving the workspace and pagehide clear
  access; 401/403/503 responses lock the workspace and remove the private view.
  Locking does not revoke a stolen token; rotate the configured secret and restart.

The workspace HTML/JavaScript shell is not private data and can load without
authentication. All data reads and writes require the boundary above. Existing
public analytics remain public; adding a private note does not make a developer's
public GitHub metadata private. Existing AI generation endpoints retain their
previous behavior and are not an administrator authentication system.

The threat model covers anonymous/remote HTTP clients, malicious websites,
host/forwarding spoofing, unsafe stored links, stale browser writes and accidental
private-data inclusion in public serializers. It assumes a trusted local OS,
browser and database administrator. Loopback alone is not authentication: the
bearer token is required even for local clients. A local process that steals the
token, or a proxy that deliberately strips identity headers and presents local
traffic, is outside this deployment boundary.

**Local setup**

The [final acceptance review and exact local runbook](PHASE7_ACCEPTANCE.md) covers
backup, the explicit 0006 upgrade, private token setup, local launch and API/UI
checks. It retains the original setup commands for a new installation; do not
repeat migration/token creation on the already accepted installation.

Migration `0006` adds `workspace_developers` and `workspace_repositories`; migrations
0001–0005 are unchanged. The operator applied the migration after backup and
configured the separate token in private `.env.workspace`. Keep that file readable
only by the administrator. Never put its contents in frontend build variables,
URLs, source files, screenshots or shared logs. The accepted local startup is:

```sh
uv run --env-file .env --env-file .env.workspace github-radar serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/workspace`, enter the token, then choose Developer
watchlist or Repository watchlist. “All tracked” exposes existing entities for
manual saving. An empty developer dataset is an honest empty state; this phase
does not fetch profiles to populate it.

For development, Vite binds to `127.0.0.1` and preserves the browser Host through
its local API proxy so same-origin validation works. It does not add forwarding
headers. The built SPA served by `github-radar serve` is the supported operating
path; do not expose Vite or a tunnel to the internet. If running Uvicorn directly
for development, explicitly use `--host 127.0.0.1 --no-proxy-headers`.

**Data and editing contract**

Each table has one primary-key/foreign-key entry per existing internal entity ID:
`entity_id`, `saved`, `notes`, `relationship_status`, opaque `version`, and UTC
`updated_at`. Renames retain the association. Deleting an entity cascades its
private entry. There are no per-user keys or tenancy claims.

- Status is one of `interested`, `contacted`, `connected`, `collaborating`, `paused`.
  For repositories it describes the administrator's relationship with the project.
  Changing a status is a manual record; it sends nothing to the developer/project.
- Notes are plain text, limited to 10,000 characters, and cannot contain null bytes.
  Database constraints also enforce status, length and a nonempty version.
- Unsaving preserves notes/status. The separate confirmed delete action removes
  only the workspace entry, including its notes/status, not the tracked entity.
- Creates send `version: null`; later writes and deletes must send the last
  returned version. Atomic conditional writes and primary-key uniqueness produce
  409 on conflicts. A new random revision on each write also prevents a stale
  request from matching an entry that was deleted and recreated.
- Failed edits retain the browser draft and display an error. Save before
  navigating or locking. Drafts are deliberately not persisted in browser storage.
  After a conflict, copy the draft, cancel/reload, inspect the current version and
  reapply the desired edits; there is no automatic overwrite or merge.

Watchlist state stays in separate tables without relationships attached to the
public entity serializers. Public listing, search, topics and explore responses
are regression-tested for private-data absence. Workspace modules do not call
GitHub or an AI provider and do not feed private state into intelligence evidence.

**API**

All paths below are relative to `/api/v1/workspace`. Send the bearer header on
every request. List responses use the existing pagination envelope (default 25,
maximum 100). Ordering is deterministic: relevance then GitHub ID for developers;
GitHub ID for repositories.

| Method and path | Behavior |
| --- | --- |
| `GET /session` | Authenticated local-admin probe; no database access. |
| `GET /developers` | Existing developer intelligence plus private state and public contact links. |
| `GET /repositories` | Existing repository metadata/topics plus private state and GitHub link. |
| `PUT /developers/{id}` or `/repositories/{id}` | Create or replace a private entry using its expected version. |
| `DELETE /developers/{id}?version=…` or `/repositories/{id}?version=…` | Delete a matching private revision only. |

Both lists accept `saved=saved|unsaved|all` (default saved), optional `status`,
`topic`, `limit`, `offset`. A status filter matches only existing private entries.
Developer lists also accept `min_relevance` (0–1) and `window=1d|7d|30d` (default
7d). Missing relevance does not pass a minimum filter. Topic relevance uses the
selected topic, or the maximum available existing topic score if none is selected.

The React page uses the 7-day window and shows existing relevance, activity,
ecosystem, emerging classification, confidence and explanation. These are direct
projections of the existing developer intelligence and serializers. Missing
activity remains unavailable; there is no new networking score or AI ranking.

PUT requires JSON such as:

```json
{"saved": true, "notes": "Met at a meetup", "relationship_status": "connected", "version": null}
```

Unknown fields, invalid booleans/statuses, invalid revision formats and oversized
notes are rejected. A missing tracked entity is 404. Contact links use a canonical
GitHub profile/repository URL and, when already stored in the public GitHub profile,
an HTTP(S) website or valid public X handle. Published profile email is displayed
as text. Unsafe URL schemes are omitted. External links use `noopener noreferrer`;
no contact source is fetched, scraped, verified or messaged by this workspace.

**Validation and review**

Final acceptance validation on 2026-10-17: **565 Python tests passed, no skips**, including
**52 guarded PostgreSQL integration cases**; Ruff and mypy passed (141 source
files). Frontend ESLint, TypeScript, all **47 Vitest tests**, and the production
Vite build passed. Existing Alembic configuration and Starlette HTTP-422
deprecation warnings remain. No dependencies were added or updated.

Tests exercise anonymous/disabled/remote access, forged forwarding/Host/Origin
headers, cookie/query-token rejection, validation before data access, plain-text
notes, retention on unsave, deletion, stale revisions, deterministic intelligence
parity, filters and pagination. PostgreSQL integration tests additionally exercise
concurrent creation/update, committed persistence in a new session/application,
database constraints/cascades, and the 0005 → 0006 → 0005 migration with identical
rows in every pre-existing table. A regression test also verifies that delayed
unlock responses cannot undo a pagehide lock. Every destructive PostgreSQL fixture uses the existing
test-database safety guard.

Run the full checks from the project root:

```sh
uv run --env-file .env pytest -q
uv run ruff check .
uv run mypy .
cd web
npm run lint
npm run typecheck
npm test
npm run build
```

`TEST_DATABASE_URL` must identify the separate guarded test database. Never use
the application database for tests. GitHub integrations in the existing suite use
fake responses, not live collection.

Browser validation used the built production bundle, actual FastAPI HTTP handling
and synthetic in-memory SQLite fixtures in isolated Chromium. Developer save/filter,
repository save/delete, keyboard interaction, reload lock and anonymous rejection
passed with no console errors or horizontal overflow at 320, 768, 1024 and 1440 px.
These browser checks did not read or modify application records.

**Security limitations and release blockers**

- **Public deployment is blocked.** There is no supported remote login, session
  lifecycle, multi-user isolation, brute-force control or internet-facing TLS/auth
  deployment verification. Do not remove the loopback/Host/Origin restrictions or
  put an unreviewed reverse proxy/tunnel in front of the private workspace.
- Local repository watchlist acceptance is complete. Developer watchlist behavior
  has automated/synthetic coverage, but no real developers are present for an
  operator test. Do not collect more data merely to close this acceptance gap.
- Backup creation and archive listing validation are complete; restoration to a
  separate database has not been tested.
- Private notes are stored as PostgreSQL text; they are not application-encrypted.
  Database roles, host/disk protection and backups remain operator responsibilities.
  Manual deletion removes live workspace rows, not historical backups. There is
  no automatic retention policy, change-history audit log or backup purge mechanism.
- A token holder has full access to the single workspace. There is no per-device
  revocation or expiry; change the secret and restart to revoke access. A compromised
  browser, local process or database administrator is outside this boundary.
- The current API reuses the existing whole-dataset intelligence loader, then
  filters/paginates its results. This matches the MVP architecture; large-dataset
  latency and multi-admin load have not been established.

Scoring, discovery catalogs/algorithms, collection ownership/budgets, stored
observations and prior migration history are unchanged. StoryOps was not modified.
The operator performed the application migration. Finalization used read-only
local checks; no live GitHub collection, deployment or push was performed.

## Public deployment mode (Phase 8A)

`DEPLOYMENT_MODE=public` refuses workspace credentials and all workspace routes,
regardless of proxy or bearer claims. The local direct-loopback boundary remains
required. Local HTTP AI generation now uses the same admin boundary; public mode
disables the entire AI API. See [production readiness](PRODUCTION_READINESS.md).
