# Phase 8A — frontend dependency security review

Review date: **2026-10-27**. This follow-up preserves the existing uncommitted
production-readiness implementation at Phase 7 HEAD
`a587f2ed665aee0a71dd0f47c85bcc643f322a36`. It changes the frontend dependency tree,
compatibility configuration/tests, CI installer/audit gates and documentation.
No application database operation, collection, AI request, deployment or commit
is part of this review.

## Audit result

| Native npm audit | Before | After |
| --- | --- | --- |
| All dependencies | 8 affected packages: 2 critical, 1 high, 5 moderate | **0 vulnerabilities** |
| Production dependencies (`--omit=dev`) | 2 moderate packages | **0 vulnerabilities** |

The initial graph contains **16 distinct GHSA advisories**. npm's summary counts
vulnerable package nodes at their highest severity, including propagated findings;
it does not count unique advisories. In particular, `react-router-dom` inherits
its findings from `react-router`, and `vite-node` inherits Vite findings.
Both before/after checks queried the npm advisory service; no threshold, ignore,
override, `--force` or optional failing check was used. A clean result describes
known advisories at review time, not a guarantee against undisclosed vulnerabilities.

## Smallest supported upgrades

| Direct dependency | Previously locked | Now locked | Why this branch |
| --- | --- | --- | --- |
| Vite | 5.4.21 | **6.4.5** | Vite 5 remains affected. The maintained 6.x security backport clears the findings without requiring Vite 7/8. |
| Vitest | 2.1.9 | **4.1.12** | Vitest 3.2.7 still uses affected `@vitest/mocker` 3.2.7. 4.1.12 fixes manual mock registration and removes the vulnerable tinypool/vite-node tree. |
| react-router-dom | 6.30.6 | **7.18.4** | The affected range includes all 6.x; fixes start at 7.18.0. Use the patched 7.x line, retaining React 18 and declarative routing. |

React/React DOM remain **18.3.1**, `@vitejs/plugin-react` remains **4.7.0**, and
TypeScript, ESLint, jsdom and all other direct dependencies remain unchanged.
Vite 6 retains the prior default production browser target. No application route,
analytics formula or workspace behavior is intentionally changed. Compatibility
edits only import `defineConfig` from `vitest/config` and remove two obsolete
React Router future-flag props now enabled by version 7; all test assertions remain.

Upstream references: [Vite 6.4.5](https://github.com/vitejs/vite/releases/tag/v6.4.5),
[Vitest 4.1.12](https://github.com/vitest-dev/vitest/releases/tag/v4.1.12),
[React Router 7.18.4](https://github.com/remix-run/react-router/releases/tag/react-router@7.18.4),
[React Router migration](https://reactrouter.com/upgrading/v6).

## Dependency chains and actual exposure

- **Runtime:** application → `react-router-dom` → `react-router`. The SPA uses
  `BrowserRouter` with fixed internal route prefixes and stored identifiers, not
  arbitrary redirect destinations. No SSR hydration or server-side error
  deserialization is configured. This reduces exposure to the two known paths;
  it is not a reason to retain the vulnerable runtime dependency.
- **Build/development:** application → `vite` → `esbuild`; plugin-react also uses
  Vite as a peer. The deployed design serves static `web/dist` through FastAPI,
  not a Vite/esbuild development or preview server. Local Vite development still
  needs the fixes: loopback binding alone does not stop browser-origin attacks.
  Windows-specific advisories do not directly apply to this Linux verification,
  but remain relevant to supported development environments.
- **Tests:** application → `vitest` → `@vitest/mocker`, `tinypool`, `vite-node`;
  mocker and vite-node also reference Vite. This project runs `vitest run` in
  jsdom, without the Vitest UI/browser server or a standalone mock interceptor
  exposed to a network. Tinypool's RCE gadget requires prior prototype pollution
  and controllable worker/run options; no production HTTP path runs this pool.
  Test/build dependencies can still execute with developer/CI permissions, so
  all findings are remediated rather than excluded from the gate.

The table gives the npm advisory range applicable to the old installed branch,
not a claim that every other upstream major has that same range. Follow each GHSA
for upstream prerequisites and patched ranges.

| Advisory | Package and old installed version | Affected range reported by npm | Severity | Path / issue |
| --- | --- | --- | --- | --- |
| [GHSA-82fw-gwwq-j7x9](https://github.com/advisories/GHSA-82fw-gwwq-j7x9) | @vitest/mocker 2.1.9 | `>=2.1.0 <4.1.11` | moderate | Vitest: Path Traversal / Arbitrary File Read via @vitest/mocker Redirect Mock |
| [GHSA-qxg6-89mg-rqv8](https://github.com/advisories/GHSA-qxg6-89mg-rqv8) | @vitest/mocker 2.1.9 | `>=2.1.0-beta.7 <4.1.12` | moderate | Vitest: Code injection via unauthenticated manual mock registration |
| [GHSA-67mh-4wv8-2f99](https://github.com/advisories/GHSA-67mh-4wv8-2f99) | esbuild 0.21.5 | `<=0.24.2` | moderate | esbuild enables any website to send any requests to the development server and read the response |
| [GHSA-wrjc-x8rr-h8h6](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6) | react-router 6.30.6 | `>=6.0.0 <7.18.0` | moderate | React Router: Open redirect via backslash in <Link> and useNavigate (CVE-2025-68470 bypass) |
| [GHSA-337j-9hxr-rhxg](https://github.com/advisories/GHSA-337j-9hxr-rhxg) | react-router 6.30.6 | `>=6.4.0 <7.18.0` | moderate | React Router: Arbitrary Constructor Injection via deserializeErrors() in React Router SSR Hydration |
| [GHSA-5gmw-xhrv-c9v3](https://github.com/advisories/GHSA-5gmw-xhrv-c9v3) | tinypool 1.1.1 | `<=2.1.0` | critical | Tinypool: Prototype Pollution gadget in worker options leads to Remote Code Execution |
| [GHSA-85c8-ppgw-ccpr](https://github.com/advisories/GHSA-85c8-ppgw-ccpr) | tinypool 1.1.1 | `<2.1.2` | critical | Tinypool: Prototype Pollution Gadget to RCE in run() options |
| [GHSA-4w7w-66w2-5vf9](https://github.com/advisories/GHSA-4w7w-66w2-5vf9) | vite 5.4.21 | `<=6.4.1` | moderate | Vite Vulnerable to Path Traversal in Optimized Deps `.map` Handling |
| [GHSA-v6wh-96g9-6wx3](https://github.com/advisories/GHSA-v6wh-96g9-6wx3) | vite 5.4.21 | `<=6.4.2` | moderate | launch-editor: NTLMv2 hash disclosure via UNC path handling on Windows |
| [GHSA-fx2h-pf6j-xcff](https://github.com/advisories/GHSA-fx2h-pf6j-xcff) | vite 5.4.21 | `<=6.4.2` | high | vite: `server.fs.deny` bypass on Windows alternate paths |
| [GHSA-698v-4r3x-3vcv](https://github.com/advisories/GHSA-698v-4r3x-3vcv) | vite 5.4.21 | `<=6.4.3` | moderate | launch-editor: escapeCmdArgs() omits % escaping on Windows |
| [GHSA-r825-w8mm-5pxw](https://github.com/advisories/GHSA-r825-w8mm-5pxw) | vite 5.4.21 | `<=6.4.3` | moderate | launch-editor: Editor CLI flag misinterpretation causing arbitrary command execution for some editors |
| [GHSA-587h-7wmj-h5pm](https://github.com/advisories/GHSA-587h-7wmj-h5pm) | vite 5.4.21 | `<=6.4.3` | high | launch-editor: `fs.existsSync()` existence check can be bypassed on Windows |
| [GHSA-9jrq-w75r-8gcw](https://github.com/advisories/GHSA-9jrq-w75r-8gcw) | vite 5.4.21 | `<=6.4.3` | low | Vite: Cross origin script execution through Vite custom middleware |
| [GHSA-rq7h-c2jc-7f22](https://github.com/advisories/GHSA-rq7h-c2jc-7f22) | vite 5.4.21 | `<=6.4.3` | moderate | Vite: Files outside `server.fs.allow` could be served when project paths match system paths on non-Windows machine |
| [GHSA-5xrq-8626-4rwp](https://github.com/advisories/GHSA-5xrq-8626-4rwp) | vitest 2.1.9 | `<3.2.6` | critical | When Vitest UI server is listening, arbitrary file can be read and executed |

## Complete lockfile package changes

`—` means added/removed. Platform-specific esbuild entries are optional native
binaries; only the host-compatible binary is installed. All package paths with
changed versions or membership are listed; unchanged packages are omitted.

| Package | Before | After |
| --- | --- | --- |
| `@esbuild/aix-ppc64` | 0.21.5 | 0.25.12 |
| `@esbuild/android-arm` | 0.21.5 | 0.25.12 |
| `@esbuild/android-arm64` | 0.21.5 | 0.25.12 |
| `@esbuild/android-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/darwin-arm64` | 0.21.5 | 0.25.12 |
| `@esbuild/darwin-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/freebsd-arm64` | 0.21.5 | 0.25.12 |
| `@esbuild/freebsd-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-arm` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-arm64` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-ia32` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-loong64` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-mips64el` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-ppc64` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-riscv64` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-s390x` | 0.21.5 | 0.25.12 |
| `@esbuild/linux-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/netbsd-arm64` | — | 0.25.12 |
| `@esbuild/netbsd-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/openbsd-arm64` | — | 0.25.12 |
| `@esbuild/openbsd-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/openharmony-arm64` | — | 0.25.12 |
| `@esbuild/sunos-x64` | 0.21.5 | 0.25.12 |
| `@esbuild/win32-arm64` | 0.21.5 | 0.25.12 |
| `@esbuild/win32-ia32` | 0.21.5 | 0.25.12 |
| `@esbuild/win32-x64` | 0.21.5 | 0.25.12 |
| `@remix-run/router` | 1.23.4 | — |
| `@standard-schema/spec` | — | 1.1.0 |
| `@types/chai` | — | 5.2.3 |
| `@types/deep-eql` | — | 4.0.2 |
| `@vitest/expect` | 2.1.9 | 4.1.12 |
| `@vitest/mocker` | 2.1.9 | 4.1.12 |
| `@vitest/pretty-format` | 2.1.9 | 4.1.12 |
| `@vitest/runner` | 2.1.9 | 4.1.12 |
| `@vitest/snapshot` | 2.1.9 | 4.1.12 |
| `@vitest/spy` | 2.1.9 | 4.1.12 |
| `@vitest/utils` | 2.1.9 | 4.1.12 |
| `cac` | 6.7.14 | — |
| `chai` | 5.3.3 | 6.3.0 |
| `check-error` | 2.1.3 | — |
| `cookie` | — | 1.1.1 |
| `deep-eql` | 5.0.2 | — |
| `es-module-lexer` | 1.7.0 | 2.3.2 |
| `esbuild` | 0.21.5 | 0.25.12 |
| `loupe` | 3.2.1 | — |
| `obug` | — | 2.2.1 |
| `pathe` | 1.1.2 | 2.0.3 |
| `pathval` | 2.0.1 | — |
| `react-router` | 6.30.6 | 7.18.4 |
| `react-router-dom` | 6.30.6 | 7.18.4 |
| `set-cookie-parser` | — | 2.7.3 |
| `std-env` | 3.10.0 | 4.3.0 |
| `tinyexec` | 0.3.2 | 1.3.1 |
| `tinypool` | 1.1.1 | — |
| `tinyrainbow` | 1.2.0 | 3.2.0 |
| `tinyspy` | 3.0.2 | — |
| `vite` | 5.4.21 | 6.4.5 |
| `vite-node` | 2.1.9 | — |
| `vitest` | 2.1.9 | 4.1.12 |

## Installation and CI policy

`web/.npmrc` disables lifecycle scripts by default and enforces the declared
Node/npm engines. Install with **Node 22.12+ (22.x) or Node >=24**, and **npm
11.11.x** (manifest accepts >=11.11.0 <12). CI retains Node 22 and explicitly
installs npm 11.11.0 before `npm ci`; the manifest records that installer version.
Local verification used Node **24.14.1**, npm **11.11.0**. Node 22 on the actual
hosted runner remains **NOT TESTED**. Use supported Node LTS releases operationally.

A fresh `npm ci` succeeded with scripts disabled; esbuild's optional native package
was available and the real production build passed. Do not omit optional
platform dependencies or re-enable all install scripts to hide a broken install.
`npm audit signatures` verified **283 registry signatures and 84 attestations**.
Deprecation notices for unchanged `whatwg-encoding` and ESLint are not suppressed;
they are maintenance items, not unresolved audit vulnerabilities.

CI now fails on **all severities** in both `npm audit` and
`npm audit --omit=dev`. It does not use `continue-on-error`, an audit exception list,
or forced dependency resolution. Least-privilege GitHub permissions, pinned action
references and the isolated PostgreSQL workflow are preserved.

From `web/`, reproduce the frontend gates:

```sh
npm ci
npm audit
npm audit --omit=dev
npm audit signatures
npm run lint
npm run typecheck
npm test
npm run build
```

## Verification and release decision

- **TESTED:** clean `npm ci`, both advisory audits, signature verification, lint,
  TypeScript, **53 tests in 9 files**, and production build. Build output is
  **248.82 kB JS / 77.21 kB gzip**, 10.42 kB CSS, no source maps. The JS increase
  from the earlier build is 17.91 kB / 6.46 kB gzip; no performance claim is made.
- **TESTED:** **730 Python tests including all 53 guarded PostgreSQL integration
  cases**, no skips; Ruff and strict mypy (140 files). The disposable cluster used
  port **15449** with a fresh generated credential, separate from the application
  cluster. Existing migration, private-access, public route/method, origin/host/
  forwarded-header, secret-redaction, private-sentinel and advisory-lock
  regressions passed unchanged.
- **TESTED:** Chromium 155.0.8059.79 against the production bundle and real local,
  public and workspace-disabled FastAPI processes. Routing/history/deep links,
  stored analytics, both watchlists, persisted edits, deletion, filters, public
  contacts, auth failures/locking and public/private exclusions passed with zero
  unexpected console errors or external requests. Workspace width checks passed
  at 320/768/1440 px. **REMAINING UI GAP:** repository detail overflows by 40 px
  at 320 px, reproduced with the original dependency lockfile in an isolated
  baseline build; wider checks pass. This pre-existing layout issue is not changed
  by dependency remediation. Details are in
  [the Phase 8A readiness review](PRODUCTION_READINESS.md).
- **NOT TESTED again in this dependency follow-up:** the prior real-data backup/
  restore drill and application database row fingerprints. That evidence belongs
  to the preceding Phase 8A review; this follow-up never connects to that database.
- **BLOCKED for public release:** successful hosted CI, real HTTPS/proxy and external
  PostgreSQL/TLS validation, edge resource/rate limits, external backup/recovery and
  the existing manual release checklist. The local npm vulnerability blocker is
  resolved; the dependency changes do not certify the deployment environment.

Real developer watchlist acceptance is still absent because the application has
no collected developers. Synthetic browser coverage does not replace that operator
acceptance. Public/proxy workspace access remains deliberately unsupported.
