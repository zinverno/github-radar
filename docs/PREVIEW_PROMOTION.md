# Exact static artifact promotion — pending operator approval

**2026-11-04: technically verified; NO-GO for publication until approval and
repository setup.** This separate branch starts from main
`435cee961bf73b0a71fa9e84394fa3e850cf4e1c`. PR #6 remains Draft at
`d98a446baf9e133804c923d693182bc06b19d574`; none of its application, build, audit-policy
or rebuilding-deployment changes must be merged to use this path. Existing CI is
byte-for-byte unchanged. No npm exception has been accepted or activated.

## Exact artifact and provenance

| Identity | Required value |
| --- | --- |
| Repository / ID | `zinverno/github-radar` / `1381365136` |
| Source workflow | `.github/workflows/static-preview.yml`, ID `405923559` |
| Source run / attempt | [41689975111](https://github.com/zinverno/github-radar/actions/runs/41689975111) / **1** |
| Reviewed head | `d98a446baf9e133804c923d693182bc06b19d574` |
| Actual PR merge checkout | `f1a905fcb5af18601a29cd6cb7d44e6d222275d3` |
| Artifact | [13256888181](https://github.com/zinverno/github-radar/actions/runs/41689975111/artifacts/13256888181), `github-radar-v0.1.0-preview-review` |
| Archive SHA-256 | `7f9ff80c9d3fb4dde2b0b2ccd513de2022527c8a7716dd17ee0c126c6ceca932` |
| Archive / expanded size | 80,048 / 253,406 bytes; exactly four files |
| Hard publication deadline | **2026-11-10T18:03:39Z**, the original artifact expiry |

GitHub metadata and the source job's checkout log establish the source run and
checkout. Its merge parents are the pinned main base and reviewed head, and the
merge tree equals the reviewed head tree. The workflow and committed manifest
were independently retrieved from those immutable commits and hash-checked.
GitHub's linked PR head/base fields can change after the run; they are not used
as build identity. The immutable run head and checkout commit parents are pinned.
The run and job intentionally concluded **failure**: functional checks, build,
artifact checks and production audit passed, while the source security gate
reported **UNAPPROVED**. Promotion does not relabel that run as successful.

[preview-promotion.json](preview-promotion.json) pins the original ZIP digest,
all four per-file hashes copied from the reviewed committed manifest, exact
source steps and the original audit artifact `13256713232`. Independently
downloaded files match every size and hash. No source/build input is exported
from a database or private workspace.

The verifier checks live repository/run/attempt/workflow/head/artifact metadata,
source commit ancestry/tree, workflow and manifest bytes, expiry, ZIP digest,
entry set, regular-file type, expanded sizes and each file hash **before writing
any extracted file**. Extra/duplicate entries, traversal/absolute/backslash paths,
links, special files and changed hashes fail. It uses no `extractall()` and never
executes artifact code. The existing reviewed content guard additionally rejects
source maps, known secret patterns, backend/API paths and unexpected URLs, and
requires DEMO DATA, preview version, the `/github-radar/` base and no-connect CSP.
Only HTML, CSS and compiled JavaScript go into the Pages package. All pages retain
the reviewed DEMO DATA banner and fixture behavior because their bytes are unchanged.

This is provenance established through GitHub API/log evidence and pinned hashes;
it is **not an independent signed build attestation**. If any source object becomes
unavailable, an identity changes, or the original artifact expires, STOP. Do not
substitute a rerun, another artifact, a local cached ZIP or a rebuilt preview.

## Remaining historical risk and proposed decision

The artifact was built using npm 11.21.1 with bundled Undici 6.29.0. The original
full npm audit still records **12 moderate development-package findings / 13 paths**
for [GHSA-g3hv-75wg-m249 / CVE-2026-118764](https://github.com/advisories/GHSA-g3hv-75wg-m249).
The original production audit reports zero vulnerabilities. These are the retained
November 3 reports, not a claim of a newly executed npm audit.

Three separate high toolchain findings remain in that historical npm distribution:
[CVE-2026-109383](https://github.com/nodejs/undici/security/advisories/GHSA-2m5v-pch6-74hx),
[CVE-2026-109869](https://github.com/nodejs/undici/security/advisories/GHSA-xjj6-f33q-929c),
and [CVE-2026-87018](https://github.com/nodejs/undici/security/advisories/GHSA-qrxm-fqhp-jwvm).
The build disabled install scripts and did not use node-gyp's downloader, but
that non-use is not vendor confirmation or an accepted waiver. ESLint 9 was also
out of support. The prior fresh general toolchain scan was incomplete due to disk
capacity. These facts remain in the source review; this change does not fix them.

Promotion executes no npm, dependency install, frontend build, source-run rerun,
Docker operation, backend or downloaded JavaScript. Hash verification proves that
the bytes are the reviewed bytes; it **cannot establish that a historically
compromised build produced safe bytes**. Secret scanning and prior functional,
accessibility and no-network tests are additional bounded evidence. Residual
historical build supply-chain risk requires an explicit artifact decision.

Proposed operator decision, **not accepted**:

> Approve publication only of artifact 13256888181, archive SHA-256
> 7f9ff80c9d3fb4dde2b0b2ccd513de2022527c8a7716dd17ee0c126c6ceca932,
> with exactly the four committed file hashes, before 2026-11-10T18:03:39Z.
> Accept the documented historical minimatch and Undici build risks for these
> existing static bytes. This does not approve executing that toolchain again,
> an npm audit exception, CI bypass, another artifact, PR #6's application changes,
> or any Docker/backend/Neon/Render/collection release.

`approval` remains **null**. After explicit review, a separately approved change
may record `approver` (GitHub login), `approved_at` (the comment's exact GitHub
`created_at` UTC value), `record` (the operator's conversation-comment permalink
on **PR #7**, ending `#issuecomment-ID`), `artifact_sha256` and all four exact
`accepted_historical_risks` strings from the manifest. PR review permalinks are
not accepted. Test approvals exist only in memory.

The operator must post the following exact comment themselves, only if accepting
the decision. Line-ending differences and surrounding whitespace are allowed;
additional text, quotes or omitted risks are rejected:

```text
I approve publication of GitHub Radar v0.1.0-preview artifact 13256888181.
SHA-256: 7f9ff80c9d3fb4dde2b0b2ccd513de2022527c8a7716dd17ee0c126c6ceca932
I accept the following historical build-toolchain risks for this exact artifact:
- GHSA-g3hv-75wg-m249 / CVE-2026-118764
- GHSA-2m5v-pch6-74hx / CVE-2026-109383
- GHSA-xjj6-f33q-929c / CVE-2026-109869
- GHSA-qrxm-fqhp-jwvm / CVE-2026-87018
This approval does not authorize a rebuild, an npm exception, or a Docker/backend release.
```

Every approval check independently reads GitHub's
[issue-comment API](https://docs.github.com/en/rest/issues/comments#get-an-issue-comment)
over HTTPS. It verifies the comment ID, repository/PR URLs, human author login,
creation time and complete acceptance text against the reviewed configuration.
Public comment reads use no credentials or additional workflow permissions.
Missing/deleted comments, API failures, mismatches, future approval and expiry all
block publication. **Any edit** (`updated_at` differs from `created_at`) blocks;
post a fresh comment and separately review its new record instead. The check
authenticates the current GitHub record, not a cryptographic operator signature;
protected-environment approval remains independently required.

The source audit artifact is independently pinned by its ZIP digest and all seven
file hashes. Its complete raw JSON, exit codes and blocked decision are retained
unchanged in a separate Actions evidence artifact for 90 days on promotion checks.
Neither source npm policy nor any production CI audit is edited or relaxed.

## Workflow and repository feasibility

[promote-preview.yml](../.github/workflows/promote-preview.yml) has only
`workflow_dispatch`. It requires the exact reviewed promotion commit on `main`
and explicit publication consent, uses fixed source/artifact IDs, and has no PR,
push, schedule or workflow-run deployment trigger. The verification job gets only
`contents: read` and `actions: read`. It retrieves and verifies the original bytes,
retains audit evidence and checks recorded approval and existing environment rules.
It uploads only the four verified files. The dependent `github-pages` job gets
only `pages: write` and `id-token: write`, waits for protected-environment approval,
checks the original expiry again, and invokes the SHA-pinned official Pages action.
Node is used only for standard-library file checks and official Actions; npm and
its node-gyp/Undici distribution are not invoked. No tool runtime enters the site.

Read-only checks on November 4 found:

- main still at `435cee961bf73b0a71fa9e84394fa3e850cf4e1c`; branch protection API:
  **Branch not protected**, applicable rules and repository rulesets: empty.
- Pages API: **404**, no configured Pages site; `github-pages` environment: **404**.
- No promotion workflow exists on the default branch. GitHub requires manual
  workflows to exist on the [default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

Therefore the existing repository can host this path after a **separately reviewed
and approved promotion-only PR** reaches main; PR #6 need not be merged. There is
no active branch-protection check to bypass today. Absence of protection is not
permission to ignore CI: existing strict frontend audits remain red, and any
repository policy requiring all checks green must still be satisfied. This work
does not authorize a merge with failed checks or change those checks. Re-read
protections before integration; if new requirements block it, STOP rather than
use an admin override. Ordinary existing CI may still run its own build jobs on
PR/merge; they are unchanged and are not part of the promotion workflow.

Minimum remaining operator steps:

1. Review the exact artifact risk decision and the promotion-only code; record
   approval if accepted. Obtain separate approval for the normal integration into
   main, including a decision on unchanged existing CI failures under repository
   policy. Do not merge PR #6 or mark its audits passing.
2. Separately configure **Pages → Source: GitHub Actions**, HTTPS and default project
   path. Create `github-pages` with an independent required reviewer, prevent
   self-review, and exactly one deployment rule **Branch: main**; disable admin
   bypass in Settings. No secrets are needed. The workflow reads these rules and
   refuses missing protection; the API does not expose admin bypass, so verify that
   setting in the UI. No settings were changed during this task.
3. Before expiry, manually run **Promote exact reviewed preview** on the approved
   main SHA, inspect the verification evidence, and approve the environment job.
   Expected URL: **https://zinverno.github.io/github-radar/**, with hash deep links.
   Check DEMO DATA and deep-link reload after publication. Withdrawal uses Pages
   **Unpublish site**; no automatic fallback or expired-artifact rollback is provided.

## Verification performed

- Fresh authenticated artifact metadata/download, original checkout/tree and
  workflow/manifest verification, safe extraction and all four independent hashes:
  pass. Original full audit remains exit 1 / 12 moderate; production audit exit 0.
- Existing content guard and fresh Trivy secret scan of the extracted directory:
  pass, zero detected secrets. Four files total 253,406 bytes.
- Standard-library tests reject changed provenance, foreign repositories, reruns,
  failed build steps, expired artifacts, wrong archives/file hashes, extra and
  duplicate entries, traversal, symlinks/special files and incomplete approval.
  Focused approval tests cover an independently fetched valid comment, wrong
  PR/author/time, edits, missing/deleted records, API failures, missing risks,
  wrong artifact/hash, quoted or negated acceptance and appended revocation.
  Content and environment checks reject leaks, maps, endpoints and absent protection.
  Workflow actionlint passes. Actual approval check exits nonzero: **UNAPPROVED**.
- No preview rebuild or npm invocation. Existing UI/browser tests were not repeated
  because the extracted bytes match the reviewed artifact. No deployment was run.
  Local raw evidence is retained in ignored `build/promotion-evidence/`.
