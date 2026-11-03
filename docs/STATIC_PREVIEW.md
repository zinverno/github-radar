# GitHub Radar v0.1.0-preview

**Static demonstration prepared for review; not deployed or publication-approved.**
This reuses the existing React/Vite application and visual design. It does not
deploy the blocked Docker backend or accept any security exceptions.

Base: clean `origin/main`, `435cee961bf73b0a71fa9e84394fa3e850cf4e1c`.
Branch: `feat/static-preview`, isolated from `fix/render-native-provenance`.
All 18 existing modified/untracked Docker remediation files were hash-checked and
preserved. Backend source, native security checks, deployment scripts, existing
CI and the application dependency lock are unchanged.

## Supported behavior and data

The existing `api()` wrapper provides the integration point; there was no existing
offline dataset. A small build-time adapter supplies authored fixtures through the
same view-model contracts. It is not a second application or a backend emulator.

Every page prominently displays **DEMO DATA**, with a fictional-data footer and
fixed snapshot disclosure. The six `demo-lab/*` repositories, their metrics and
four topics are invented; none was queried from GitHub or exported from a database.
The reference date is fixed at `2026-10-01T12:00:00Z`. Scores/classifications are
illustrative fixture values, not outputs of the production analytics pipeline.

| Surface | Supported in the preview |
| --- | --- |
| Explore | Synthetic repository ranking, topic aggregates, counts and graph edge totals. |
| Repositories | Six fictional records; sort/direction, language, trend, star bounds, archived/fork filters, shareable filter URLs and empty results. The catalog fits on one UI page. |
| Repository detail | Description, topic navigation, stars/forks, illustrative momentum/confidence, 7d/30d deltas and coverage; zero growth and unavailable history remain distinct. Fictional names do not link to real GitHub accounts. |
| Topics | Four topics, repository tables and aggregates; `demo-empty` demonstrates an empty topic. Wide tables support keyboard scrolling. |
| Ecosystem | Ten nodes and nine topic edges; existing zoom, pan, keyboard node selection and links to repository/topic pages. No developer/ownership/contribution data. |
| Developers and bridges | Clearly labeled empty states; no profiles or relationships are invented. Cross-topic queries return empty bridge results. |
| AI, authentication, private workspace | Navigation hidden and direct routes disabled, including nested workspace URLs. AI/workspace screens are removed from the preview bundle. |
| Unknown records/routes | Local unavailable/not-found states; never a fallback request to a server. |

No private local database, Neon, networking workspace state or production
credentials were accessed. There is no collection, GitHub sync, live search,
AI generation, sign-in or persistence. Ordinary `npm run build` retains the live
backend UI and excludes the demo dataset; preview mode is selected only at build
time, never by a URL or by a failed API request.

## Build and review locally

Reviewed tools: **Node 24.22.2, npm 11.21.1**, existing frozen `package-lock.json`.
The Node release archive was checked against its official SHA-256 manifest.
Install/build without application credentials or user npm configuration:

```sh
cd web
env -i PATH="$PATH" npm --userconfig=/dev/null ci --ignore-scripts
env -i PATH="$PATH" npm --userconfig=/dev/null run build:preview
node scripts/audit-preview.mjs dist-preview
npm run preview -- --mode static-preview
```

Open `http://127.0.0.1:4173/github-radar/`. The publishable directory is
**`web/dist-preview/`**, containing only one HTML file, one CSS file and two JS
files. Do not publish the checkout, tests, evidence directory or `web/dist/`.
Preview configuration disables dotenv loading, environment-variable exposure,
public-directory copying and source maps. Its CSP blocks connections and form
submission; the adapter rejects writes and unknown routes without calling fetch.

The review-only workflow `.github/workflows/static-preview.yml` builds/tests/scans
the candidate and retains full and production npm audits independently. Its strict
preview decision gate remains blocked by the unapproved moderate exception and
separate npm-toolchain highs. Candidate artifacts are retained for seven days;
complete audit evidence for 90 days. Retention does not approve publication.
The separate manually triggered Pages workflow is prepared but has not been run.
See [the security decision and operator procedure](STATIC_PREVIEW_SECURITY.md).

## GitHub Pages handoff

Vite uses `/github-radar/` as its preview asset base. `HashRouter` keeps navigation
after `#`, so the server only needs the static directory index. A reloadable link
will have the form
`https://zinverno.github.io/github-radar/#/repositories/demo-lab/signal-kit`.
This is a target URL, **not an assertion that a site is live**.
Topic links, filter URLs and the skip link were checked under that project path
using a plain static HTTP server without an SPA fallback.

After separate publication/security approval, follow the protected manual
workflow and required settings in [STATIC_PREVIEW_SECURITY.md](STATIC_PREVIEW_SECURITY.md).
It rebuilds on reviewed main and compares every file against the committed
manifest before publication. No Pages setting, publishing branch or environment
was created here. A different project path or
custom domain requires reviewing the Vite base and rebuilding/rescanning.
[Vite's Pages guidance](https://vite.dev/guide/static-deploy.html#github-pages)
and [HashRouter](https://reactrouter.com/api/declarative-routers/HashRouter)
describe these hosting/navigation requirements.

## Verification — 2026-11-03

- **62 frontend tests and one artifact-gate test pass**; lint and TypeScript pass.
  Tests cover no network fallback, denied writes/private paths, filter behavior,
  preserved typing focus, hash links, remount/deep-link behavior and missing data.
- Preview and ordinary backend-frontend builds pass. A second preview build
  produces identical file names and bytes; the normal build contains no demo data.
- **48 route/viewport checks** (12 routes at 320/375/768/1440 px): zero document
  overflow, unexpected requests, HTTP failures or console errors/warnings.
- **12 axe WCAG A/AA checks at 320 px**: zero violations. Keyboard checks cover
  skip-to-content without changing the route, filters, table scrolling and graph
  node selection/navigation. Browser is isolated Chromium; Playwright 1.64.1 and
  axe Playwright 4.14.0 are optional test tools installed outside the app lock.
  This is automated coverage plus screenshot inspection, not a full assistive-
  technology certification or cross-browser acceptance.
- **PRE-BETA-UX-001 reproduced at 360 px document width / 320 px viewport, then
  fixed at 320/320.** The shared grid now shrinks below its former 340 px minimum.
  Related preview checks found and fixed a narrow filter overflow, filter input
  remount/focus loss and interactive graph naming/role errors.
- Exact artifact guard and Trivy 0.76.1 secret scan pass: no source maps, API path,
  credential patterns, unexpected files or symlinks. The four files contain only
  the compiled app/styles and reviewed fictional data. No production data export
  was used as input. The archive entries match the scanned file hashes.
- **Production npm audit: 0 findings. Full audit: FAIL, 12 moderate development
  package findings**, the existing minimatch advisory
  [GHSA-g3hv-75wg-m249 / CVE-2026-118764](https://github.com/advisories/GHSA-g3hv-75wg-m249).
  No dependency override, advisory suppression or risk exception was introduced.
  Known build-tool risks from the separate Docker review are not accepted by this
  preview. Hosted CI status must be read from the PR, not inferred from local tests.

Candidate: `build/github-radar-v0.1.0-preview.zip`, **79,749 bytes**;
four files totaling **253,406 bytes** uncompressed.
Archive SHA-256: `3dcede524c0ca5a5c3e1ea5d878faecaa9fa00934ac08f55b46327a04a91fdb2`.
Raw browser/audit/scan reports, screenshots, file hashes and build logs are retained
locally in `build/preview-evidence/`; the CI workflow retains its own candidate and
audit records. Nothing has been published, so these are candidate artifact checks.

To rerun the browser matrix, install the two pinned test tools in a disposable
directory, serve a copy of `dist-preview` at `/github-radar/` with a plain static
server, then from `web` run:

```sh
node scripts/verify-preview.mjs /path/to/disposable-test-tools
```

The script defaults to loopback port 4173 and `/usr/bin/chromium`; set
`PREVIEW_CHROMIUM` for another installed Chromium executable. It blocks external
and API requests and writes evidence outside the published directory.

## Remaining work for the real backend beta

The static preview grants no backend release approval. Runtime and builder
vulnerability dispositions, npm/build-tool risk decisions, hosted acceptance,
Render's credential-free build/runtime handoff, production TLS/SCRAM/native
provenance checks, approved public-data publication and real cold-start/resource
acceptance remain separate gates. The existing Render Free service remains
unchanged. No Neon credentials, private records or collection activation may be
introduced as part of publishing this synthetic preview.
