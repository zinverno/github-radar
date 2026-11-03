# Static preview security and publication decision

**2026-11-03: NO-GO for publication. Ready for operator review only.**
PR #6 remains Draft. This review changes neither the backend nor Docker, Neon,
Render, collection, secrets, Pages settings or environment protection settings.
The reviewed UI starts at `d84cb4a8504a36d3ac7b64eab7b2e63849b47525`.
Its four published-file candidates remain byte-identical after this change.

## Current npm disposition

Fresh `npm audit --json`: **12 moderate package findings, one advisory**, exit 1.
Fresh `npm audit --omit=dev --json`: **zero findings**, exit 0.
Complete reports, dependency paths and the inactive proposal are checked in under
[`docs/security/static-preview-*`](security/static-preview-policy.json).
The review/publication workflows retain complete fresh reports, their exit codes,
the decision, artifact hashes and emitted module inventory for **90 days**.
No advisory is hidden from the raw audit. Audit endpoint/errors fail closed.

[GHSA-g3hv-75wg-m249 / CVE-2026-118764](https://github.com/advisories/GHSA-g3hv-75wg-m249)
is rated moderate by GitHub (CVSS v4 6.9; npm currently reports CVSS score 0 with
no vector). Its range is `<11.0.1`, fixed in **11.0.1**. Pattern amplification in
minimatch 10.2.6 with `optimizationLevel >= 2` can exhaust CPU/memory. The locked
copies are **3.1.5** and **10.2.6**. We retain both findings; inspection of current
callers does not substitute for a vendor non-affected determination.

All 13 affected installation paths have `dev: true` in the frozen lock. Twelve
package records include transitive audit effects, not twelve distinct advisories:

| Audit package | Exact installed path and version | Reported dependency chain (`via`) |
| --- | --- | --- |
| `@eslint-community/eslint-utils` | `node_modules/@eslint-community/eslint-utils` (4.10.1) | eslint |
| `@eslint/config-array` | `node_modules/@eslint/config-array` (0.21.2) | minimatch |
| `@eslint/eslintrc` | `node_modules/@eslint/eslintrc` (3.3.7) | minimatch |
| `@typescript-eslint/eslint-plugin` | `node_modules/@typescript-eslint/eslint-plugin` (8.71.1) | @typescript-eslint/parser, @typescript-eslint/type-utils, @typescript-eslint/utils, eslint |
| `@typescript-eslint/parser` | `node_modules/@typescript-eslint/parser` (8.71.1) | @typescript-eslint/typescript-estree, eslint |
| `@typescript-eslint/type-utils` | `node_modules/@typescript-eslint/type-utils` (8.71.1) | @typescript-eslint/typescript-estree, @typescript-eslint/utils, eslint |
| `@typescript-eslint/typescript-estree` | `node_modules/@typescript-eslint/typescript-estree` (8.71.1) | minimatch |
| `@typescript-eslint/utils` | `node_modules/@typescript-eslint/utils` (8.71.1) | @eslint-community/eslint-utils, @typescript-eslint/typescript-estree, eslint |
| `eslint` | `node_modules/eslint` (9.39.5) | @eslint-community/eslint-utils, @eslint/config-array, @eslint/eslintrc, minimatch |
| `eslint-plugin-react-refresh` | `node_modules/eslint-plugin-react-refresh` (0.4.26) | eslint |
| `minimatch` | `node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch` (10.2.6); `node_modules/minimatch` (3.1.5) | GHSA-g3hv-75wg-m249 |
| `typescript-eslint` | `node_modules/typescript-eslint` (8.71.1) | @typescript-eslint/eslint-plugin, @typescript-eslint/parser, @typescript-eslint/typescript-estree, @typescript-eslint/utils, eslint |

The path tree is retained in
[static-preview-minimatch-paths.json](security/static-preview-minimatch-paths.json).
ESLint consumes the root minimatch through its own matcher, config-array and
eslintrc. typescript-eslint reaches the nested copy through typescript-estree;
its parser/plugin/type-utils/utils and ESLint peers propagate the finding.
The React refresh ESLint plugin and eslint-utils are lint-only peer effects.
These operations consume reviewed source/config globs in ephemeral builds, not
visitor-supplied input. Install scripts are disabled and the preview build runs
with an empty inherited environment except PATH, without application credentials.
This reduces exposure; it does not eliminate build availability or supply-chain
risk. [ESLint 9 is EOL](https://eslint.org/version-support/) since 2026-08-06, which
is an additional limitation the operator must explicitly consider.

### Compatible remediation check

Public registry metadata was refreshed on November 3 and retained in
[static-preview-latest-parents.json](security/static-preview-latest-parents.json):

| Latest parent | Required minimatch |
| --- | --- |
| eslint 10.14.0 | `^10.2.6` |
| @eslint/config-array 0.23.5 | `^10.2.4` |
| @eslint/eslintrc 3.3.7 | `^3.1.5` |
| @typescript-eslint/typescript-estree 8.72.1 | `^10.2.2` |

The patched major 11 is outside all those ranges (latest minimatch is 11.0.4).
A manifest-only disposable `npm audit fix --dry-run --ignore-scripts --json`
with no `--force` still reports 12 moderate findings. npm's suggested
`typescript-eslint@8.14.0` is an out-of-range downgrade, not a verified safe fix.
No compatible complete remediation was available at review time. No forced
resolutions, overrides, broad upgrades or lockfile changes were made. Recheck
upstream availability before accepting/renewing any exception.

### Absence from the static artifact

The build now inspects Vite/Rollup's actual emitted chunk module IDs and rejects
any unknown or development npm package. The retained
[module inventory](security/static-preview-bundle-modules.json) contains only
React, React DOM, React Router and Scheduler as emitted npm packages; no minimatch,
ESLint, typescript-eslint, Node/npm executable or development dependency tree.
React Router's internal `dist/development` filename is part of its production
npm dependency; it is not an installed development package.

The exact four files match the previously reviewed local **and hosted** artifact
from run `41671117813`. All pages therefore retain the tested DEMO DATA banner,
hash navigation, no-request adapter, CSP and disabled private/AI routes.
The artifact guard checks every file, rejects maps/symlinks/unexpected files,
known credential patterns, backend paths and URLs outside the reviewed inert
library documentation/XML namespace/router-origin literals. The publication gate
also requires every file's exact size and SHA-256 to match the committed policy,
so changed/encoded endpoints or new data cannot pass by evading a pattern scan.
A fresh Trivy secret scan detected zero secrets. These are bounded scanner and
source/build-provenance checks, not a guarantee against arbitrary supply-chain
compromise.

Artifact: **4 files, 253,406 bytes**, `/github-radar/` asset base. Exact file hashes
are in [static-preview-policy.json](security/static-preview-policy.json).
Local review ZIP SHA-256:
`3dcede524c0ca5a5c3e1ea5d878faecaa9fa00934ac08f55b46327a04a91fdb2`.
The Pages action packages the same verified files as a tar artifact; container
archive hashes differ with archive metadata. The trust anchor is the reviewed
per-file manifest in Git, not a hash supplied by an arbitrary downloaded artifact.

## Proposed exception — INACTIVE

Scope: **only GitHub Radar v0.1.0-preview static artifact**, advisory
`GHSA-g3hv-75wg-m249`, npm source ID `1244012`, the exact 12 records/13 paths above,
versions, full vulnerability-record fingerprint, lockfile and artifact hashes.
Hard expiry: **2026-11-17T00:00:00Z**, maximum 14 days from the review. No renewal.
**`approval: null`**: there is no approver, approval date or approval record.
The proposal is not acceptance, and a workflow dispatch is not risk acceptance.

Only after explicit operator review may a separate reviewed change fill
`approval.approver` (GitHub login), `approval.approved_at` (UTC timestamp), and
`approval.record` (the operator's PR #6 issue-comment/review permalink).
Do not fabricate an approval record or remove findings to make CI green.
Acceptance must acknowledge the build-only DoS/supply-chain exposure and EOL
linter. It grants no backend beta or toolchain-high exception.

`web/scripts/preview-security.mjs` enforces complete audit schema/counts and raw
exit codes, zero production findings, no high/critical findings, exact vulnerability
record fingerprint, exact lock and artifact hashes, development-only paths,
valid scope, an unexpired window and a named recorded approval. New or changed
advisories, affected paths, ranges, severities, fix metadata or lock/artifact bytes
block and require a fresh review. It also blocks the separate npm toolchain highs
below, even if the moderate exception is approved.

The full audit step retains its nonzero exit and is allowed to continue only so
the strict decision step and evidence upload can run. The decision step has no
`continue-on-error`; unapproved/expired exceptions fail the job. Production audit
failure independently fails it. Existing `.github/workflows/ci.yml` and all backend
security audits remain unchanged and strict. If its full-audit check is required
for merging, it remains an independent merge blocker; this preview proposal does
not disable it or authorize bypassing branch protection.

### Separate pinned npm toolchain blocker

npm **11.21.1** itself bundles **Undici 6.29.0**, independently of the application's
package-lock/audit. The installed version was rechecked and these vendor advisories
remain high, fixed starting at **6.29.1**:

- [GHSA-2m5v-pch6-74hx / CVE-2026-109383](https://github.com/nodejs/undici/security/advisories/GHSA-2m5v-pch6-74hx): custom TLS verification callback can be lost.
- [GHSA-xjj6-f33q-929c / CVE-2026-109869](https://github.com/nodejs/undici/security/advisories/GHSA-xjj6-f33q-929c): response content-encoding can cause process termination.
- [GHSA-qrxm-fqhp-jwvm / CVE-2026-87018](https://github.com/nodejs/undici/security/advisories/GHSA-qrxm-fqhp-jwvm): TLS renegotiation can cause process termination.

These are absent from the static artifact. Prior source review traced the bundled
module to node-gyp's downloader, which this scripts-disabled build does not invoke.
That bounded non-use is not vendor confirmation and is not risk acceptance.
The policy's separate `toolchain_blockers` therefore remains populated. It must
not be cleared by approving the minimatch exception. A verified supported npm
release/fix and a fresh toolchain scan/review are required before clearing it.
This inventory is a release hold, not a replacement for a general toolchain scanner.
A fresh filesystem vulnerability scan was attempted but its database extraction
exceeded local `/tmp` capacity; **no successful fresh toolchain scan is claimed**.
Vendor checks and installed-version verification are the evidence for the hold.
No Docker image, engine, library override or backend code was changed.

## Operator-controlled GitHub Pages workflow

[static-preview-pages.yml](../.github/workflows/static-preview-pages.yml) has only
`workflow_dispatch`. It accepts a full reviewed SHA and an explicit publication
checkbox; it runs only in `zinverno/github-radar`, on `refs/heads/main`, when that
SHA equals the selected workflow revision. No PR event, arbitrary checkout ref,
artifact download input, ordinary push, schedule or automatic publication exists.

The prepare job has only `contents: read` and `actions: read`. It first reads the
existing `github-pages` environment and fails if required reviewers, prevention
of self-review, or a sole `main` branch policy are missing. It
never creates/configures an environment or Pages site. Missing API permission or
missing settings fails closed. It checks out the exact SHA without persisted
credentials, runs frozen scripts-disabled install, frontend checks/build, both
audits, module audit, full artifact scan and hash/approval gate. No application
secrets are passed. Evidence is uploaded even when that gate blocks. Only success
can package `web/dist-preview/` with the SHA-pinned official Pages upload action.

The dependent deployment job uses the protected **github-pages** environment,
with **only `pages: write` and `id-token: write`**. It runs no repository/build code
and consumes only the artifact from this same successful workflow run. After the
human environment approval it rechecks expiration and limits the audit age to
one hour; waiting longer requires another manual run. SHA-pinned `deploy-pages`
then publishes that artifact. GitHub's documented permission requirements are
[here](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
No settings or deployment run were activated during preparation.

GitHub's environment GET schema does not expose the admin-bypass setting; the
operator must verify that control in Settings. The workflow does not claim to
enforce a field that the API does not supply.

Expected URL: **https://zinverno.github.io/github-radar/** (not yet asserted live).
Deep links use `#/repositories/demo-lab/signal-kit`; no SPA server fallback needed.

### Required settings and remaining operator steps

1. Review the exact minimatch proposal and reject it or record explicit, bounded
   acceptance. Resolve the separate npm toolchain highs; this proposal cannot waive
   them. Re-run fresh audits, toolchain review and hosted checks. Keep PR #6 Draft
   until the security decision is reviewed. No merge/branch-protection bypass is
   authorized here.
2. After separate merge approval, make the reviewed workflow available on `main`.
   Configure **Settings → Pages → Build and deployment → Source: GitHub Actions**.
   Keep the default project URL, enable HTTPS, and do not add a custom domain.
3. Pre-create **Settings → Environments → github-pages**: required trusted reviewer,
   **Prevent self-review**, disable **Allow administrators to bypass**, selected
   deployment branches/tags with exactly one **Branch: main** rule. No environment
   secrets are needed. Use a second authorized operator for approval when self-review
   is prevented. GitHub Free supports these protections for public repositories.
4. Run **Publish reviewed static preview** manually on `main`, provide its exact
   reviewed full SHA and check publication consent. Inspect retained audit/hash
   evidence and approve the protected environment job within one hour. This is the
   first-publication approval; merely merging/committing never publishes.
5. Check the project URL, DEMO DATA disclosure, deep-link reload and no API requests.
   To withdraw a first preview, use **Settings → Pages → Unpublish site**. A later
   rollback must use a newly reviewed manifest/source through the same checks;
   never automatically republish an expired/vulnerable older artifact.

[GitHub environment protection instructions](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).
No Pages/environment settings were modified or claimed verified remotely.

## Verification of this change

- Fresh full/production audits and compatible-fix dry run, with complete raw JSON.
- 62 frontend tests, lint, TypeScript, artifact guard and 24 security/environment tests
  pass. The actual committed proposal returns **BLOCKED / UNAPPROVED**, exit 1.
  A hypothetical approval is tested only in memory, including rejection when
  toolchain highs remain. No approval is written to the policy.
- Preview build with emitted-module guard passes and reproduces the exact reviewed
  file hashes; Trivy secret scan reports zero. Workflow actionlint passes.
- Prior hosted functional, 48 responsive route/viewport, 12 axe and keyboard/no-network
  checks apply to identical artifact bytes. No UI code changed, so those browser
  checks were not repeated. The live publication path/environment approval is
  **NOT RUN** because publication is unauthorized and the risk gate is blocked.
- All 18 original dirty Docker remediation files remain byte-identical; no database,
  production credentials, backend service or collection is used by this work.

Local fresh evidence: `build/preview-security-review/`; hosted evidence is linked
from PR #6 after its updated review-only CI run. This is a **No-Go release decision**,
not an invitation to dispatch the publication workflow before the blockers clear.
