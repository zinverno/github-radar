# Web API and SPA (Phase 6)

Phase 6 exposes the Phase 2–5 intelligence through two surfaces that share one
process:

1. **The REST API** — a FastAPI application mounted at `/api/v1` with a typed
   JSON contract, validation and a uniform error envelope.
2. **The SPA** — a React 18 + TypeScript single-page app in `web/` that
   consumes the API and is served by the API process from `web/dist`.

There is no separate web server: `uv run github-radar serve` binds one port
that serves the SPA (`/`), the API (`/api/v1`) and the interactive API console
(`/api/v1/docs`).

Phase 7 adds `/workspace/developers` and `/workspace/repositories`, backed by
authenticated `/api/v1/workspace/*` routes. These are private and disabled by
default; the public intelligence routes do not include notes or watchlist state.
See [Networking Workspace](NETWORKING_WORKSPACE.md) for setup, API contracts,
the direct-loopback restriction and release blockers.

## API contract

- **Mounting.** Everything lives under `/api/v1`. OpenAPI: `/api/v1/openapi.json`,
  docs: `/api/v1/docs`. Unknown API paths return a JSON 404 envelope (never
  the SPA shell).
- **Envelopes.** Paged `GET`s return
  `{"items": [...], "limit": n, "offset": n, "total": n, "has_more": bool}`
  (bridges add `returned`/ordering fields where they matter). Pagination
  defaults to `limit=25`, caps at `100`, and out-of-range values fail with
  `422 validation_error`. `null` always means "unavailable" — never zero
  (a repo with no history reports `stars_7d: 1`, `trend: "new"`; a missing
  count reports `null`).
- **Errors.** Every failure is
  `{"error": {"code", "message", "details": {}}}`. Codes: `invalid_window`
  (400, defensive), `validation_error` (422, actually produced by the OpenAPI
  pattern), `not_found`, `bad_request`, `ai_configuration_error` (503),
  `ai_provider_error` (502), `ai_repair_failed` (502), `internal_error`
  (500). Details are machine-readable and safe to render.
- **Windows.** Exactly `1d | 7d | 30d`, default `7d`. The OpenAPI pattern
  `^(1|7|30)d?$` rejects everything else as `422 validation_error` before the
  route runs.
- **Paths with slashes.** Repository ids are `owner/name`. The detail routes
  use `{ref:path}` converters so `octo/beta` reaches the route intact; static
  routes (`/repositories/summary`, `/bridges/repos`, `/bridges/cross-topic`)
  are declared first so they win over the converters.

## Endpoint map

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | liveness probe |
| `GET /status` | dataset counts + snapshot range + AI configuration summary |
| `GET /explore` | one-page dashboard digest (repositories, topics, developers, ecosystem highlights) |
| `GET /search?q=…&types=…` | search tracked repositories, topics, developers |
| `GET /repositories` | tracked repositories, ordered by last seen, with latest counts |
| `GET /repositories/summary` | aggregate momentum/trends across tracked repos |
| `GET /repositories/{ref:path}` | one repository: metrics, trend, developers, graph, tags, link |
| `GET /topics` | observed topics with confidence + repo counts |
| `GET /topics/{name}` | one topic: metrics, repositories, related topics, graph |
| `GET /developers` | tracked developers with relevance/activity/ecosystem scores, filters + sort |
| `GET /developers/emerging` | developers classified `EMERGING`, most-rising first |
| `GET /developers/high-activity` | high-confidence active developers |
| `GET /developers/{login}` | one developer: profile, signals, topic relevance, graph, bridge |
| `GET /ecosystem` | graph report: node/edge counts, components, centrality, top bridges |
| `GET /ecosystem/nodes` | paged graph nodes (`DEVELOPER`/`REPOSITORY`/`TOPIC`, filterable) |
| `GET /ecosystem/edges` | paged graph edges (`source`/`target` as `[node_type, node_id]` pairs) |
| `GET /bridges` | developers ranked as cross-ecosystem bridges |
| `GET /bridges/repos` | repositories ranked as cross-ecosystem bridges |
| `GET /bridges/developers/{login}` | one developer's bridge explanation |
| `GET /bridges/repositories/{ref:path}` | one repository's bridge explanation |
| `GET /bridges/cross-topic?topic_a=…&topic_b=…` | the bridge ecosystem between two topics |
| `GET /ai/status` | provider configuration, limits, cache size |
| `GET /ai/artifacts` | most recent cached artifacts |
| `GET /ai/artifacts/{entity_type}/{entity_key}` | cached artifacts for one entity |
| `POST /ai/generate` | synthesize (or replay cached) one artifact for an entity |

The `POST /ai/generate` body is `{"entity_type": "repository|topic|developer|
ecosystem|bridge", "entity_key": "…", "window": "…"}` (entity_key omitted for
`ecosystem`, `"topic_a topic_b"` for `bridge`). Reads never generate — only
this endpoint can spend model budget, and it is a cache hit whenever the
evidence fingerprint is unchanged.

## SPA structure

```
web/
  index.html / package.json / tsconfig.json / eslint.config.js / vite.config.ts
  src/
    main.tsx, App.tsx, styles.css      # entry, routes, dark theme + a11y styles
    lib/
      api.ts          # fetch wrapper: unwraps envelopes, throws typed ApiError
      endpoints.ts    # typed endpoint helpers (paths + params, error codes)
      types.ts        # API response mirrors (huge)
      format.ts       # numbers, percentages, dates, relative times
      useAsync.ts     # data/loading/error/reload hook
    components/       # Layout/nav, basic (Loading/Error/Empty), confidence pills,
                      # bars, Pager, WindowSwitcher, EcoGraph (SVG graph)
    pages/            # Explore, Repositories(+detail), Topics(+detail),
                      # Developers(+detail), Ecosystem, Bridges, AI, NotFound
  src/test/setup.ts   # jest-dom matchers for vitest
  src/lib/__tests__/, src/components/__tests__/   # vitest + testing-library
```

- **Routing.** `react-router-dom` BrowserRouter. Detail pages live at
  `repositories/:owner/:name`, `topics/:name`, `developers/:login` — the
  API serves `index.html` for those deep links (single-page fallback).
- **Error handling.** `api<T>` throws an `ApiError` carrying
  `status`/`code`/`message`; pages render a shared `ErrorState` with a retry
  button. No raw stack traces ever reach the UI.
- **Graph.** `EcoGraph` renders `nodes` + `edges` (edges are fetched as
  `[node_type, node_id]` pairs and converted to objects) into a
  deterministic, layered SVG with a legend. Nodes are focusable and navigate
  to their detail page.
- **a11y.** Semantic `main`/`nav`/`header`, a skip-to-content link, visible
  `:focus-visible`, `aria-pressed` on window switchers, labels on all inputs,
  and no `dangerouslySetInnerHTML` (React escaping is the default). Requires
  the tree itself to be keyboard navigable.

## Conventions / rules

- **No duplication.** Routes call the same Phase 2–5 `analytics/` and `graph/`
  functions as the CLI. If a number differs between `trending` and the API,
  that is a bug.
- **Null is not zero.** When history is too thin to measure a delta, the API
  returns `null` (see `trends/confidence` coverage rules). The SPA renders
  "—" and never fabricates a value.
- **The UI reads only.** Writes are limited to `POST /ai/generate` (cache the
  synthesis) — and that is 503 when `AI_API_KEY` is missing, so the rest of
  the app keeps working without an LLM.

## Development workflow

```bash
uv run github-radar serve        # terminal 1: real API + built SPA on :8000
cd web && npm run dev            # terminal 2: Vite on :5173, proxies /api to :8000
```

Frontend gates (run from `web/`): `npm run lint`, `npm run typecheck`,
`npm run test` (vitest, jsdom), `npm run build` (output into `web/dist`, which
`serve` picks up automatically at startup). A freshly cloned repo serves the
day-0 placeholder until `npm run build` has produced a `web/dist`.

## Backend tests

`tests/unit/test_api_routes.py` exercises the whole API through an in-memory
sqlite app built with `create_app(settings=…, engine=…, session_factory=…)`.
It seeds the storage tables directly and asserts on the JSON envelopes,
statuses and error codes. Note: sqlite needs a `BigInteger → INTEGER`
compiler override (already installed in the test module) so auto-increment
primary keys work under `create_all`.
