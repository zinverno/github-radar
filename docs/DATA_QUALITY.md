# Phase 6.5 Checkpoint C — Data Coverage & Ranking Validation

Checkpoint C adds read-only diagnostics, not new scoring algorithms, collection
execution, a scheduler or a dashboard. Baseline: `81e16a2323a16b44ce1e8adddba6a1b5b7ae54bc`.
The application dataset has only the user's first pilot. No further live
collection was performed for this checkpoint. Changes await review; Phase 7
and Phase 8 have not started.

## Existing capabilities and additions

Phases 1–6 and Checkpoints A+B already provide repository and developer
observations, 1d/7d/30d deltas, explainable momentum, trend classifications,
deterministic confidence, developer activity/emerging scores, graph bridge
protections, topic aggregates, stable repository identity, discovery provenance
and durable collection receipts. `stats`, `trending`, `topics`, `developers`,
`emerging-developers`, `bridges` and `collection-runs` expose individual views.

The missing view was dataset-wide coverage, overlap, ranking evidence and
collection costs in one reproducible report. The new commands are:

```bash
uv run --env-file .env github-radar collection quality
uv run --env-file .env github-radar collection quality --json
uv run --env-file .env github-radar collection quality \
  --as-of 2026-10-16T18:30:00+00:00 --stale-after-hours 24 --top 10 --json
uv run --env-file .env github-radar collection stats --json
```

`collection quality --config PATH` compares saved provenance to another local
catalog, including the expansion proposal below. The default remains
`config/discovery.toml`; changing a catalog does not reclassify stored data.
`collection-runs` and all existing collection commands remain available.

These commands require database access, not GitHub credentials. They open a
PostgreSQL **REPEATABLE READ, READ ONLY** transaction, never construct an HTTP
client, never run migrations and never acquire collection ownership. They can
read a consistent committed snapshot while a guarded collector works. They
load evidence in batches, reusing storage helpers and the graph loader.

JSON includes schema version 1, raw evidence and report assumptions. Text is a
compact summary; use JSON for distributions, per-repository windows, provenance,
score components and per-run durations. Reports are deterministic for the same
stored state, catalog and explicit assessment timestamp; ties use stable GitHub
IDs. `--as-of` is an age/staleness assessment time, **not** a historical database
reconstruction. A timestamp before loaded observations is rejected. Repository
scores retain each repository's latest observation as their reference; developer
and bridge scores retain the existing dataset reference. Staleness is separate.

## Coverage definitions

- Repository totals use stable GitHub IDs once. Category memberships use sets;
  one repository found by two recipes in one category counts once in that
  category. A repository in two categories counts once in the global total,
  once in each category, and once in their pairwise overlap. Membership totals
  may exceed the global total; they must not be summed as distinct repositories.
- Curated categories come only from `discovery_provenance`. GitHub topics come
  from repository metadata and have separate aggregates. No tag implies a
  curated category. Empty catalog categories, missing provenance and names
  outside the current catalog are visible. Actual query, recipe and last run
  ID accompany each provenance entry.
- Language is the current primary GitHub language, with an explicit unknown
  bucket. Age uses GitHub `created_at`, not `first_seen_at`: under 30 days,
  30–364 days, 365–1094 days, 1095+ days, unknown or future timestamp.
- Lifetime star bands use the newest observation: 0–9, 10–99, 100–999,
  1000–9999, 10000+, or unknown. They describe size, not current growth.
- Archived/fork flags, never-observed repositories and observation counts
  (0, 1, 2, 3+) are separate. Stale means the latest observation is at least
  `--stale-after-hours` old; never-observed is not silently counted as fresh.
- Window eligibility is reported for **star deltas** at 1, 7 and 30 days:
  complete, partial or unavailable. Completeness reuses the existing at-or-before
  baseline and maximum half-window base-gap rule. It does not mean daily or
  continuous observation coverage. Actual span and baseline gap remain visible.
- Momentum availability reuses the current stars/forks baseline requirements;
  a complete star window alone need not provide momentum (e.g. zero fork base).
  Available momentum and a complete 7d window are separate counts.
- Developer history is counted per observed contributor relationship. A usable
  window requires an actual delta; complete relationship counts remain separate.
  Zero observed change is usable. Cumulative lifetime contributions never stand
  in for a missing delta. Profile/contributor observation totals are explicit.

## Current local evidence

Read-only assessment at **2026-10-16 18:29:31 UTC** confirmed the existing pilot receipt
`88c9195e-fa20-4904-903f-b8cadb836945`. There are:

| Measure | Observed result |
| --- | --- |
| Repositories / repository observations | 5 / 5; exactly one observation each |
| Curated category coverage | AI Agents 3, Developer Tools 3, Data Engineering 0 |
| Overlap | 1 shared ID (`1152413600`, `ai-sdlc-framework/ai-sdlc`) |
| Provenance | 6 memberships across 5 repositories; none missing |
| Languages | TypeScript 3, Go 1, Python 1 |
| Creation age | All 5 between 30 and 364 days; all newly tracked |
| Lifetime stars | 2 at 10–99; 3 at 100–999 |
| Archived / forked | 0 / 0 |
| Complete or partial 1d/7d/30d deltas | 0; all five unavailable |
| Available momentum | 0 |
| Stale at the audit, with 24-hour threshold | 0; this changes with assessment time |
| GitHub topics | 63; all topic confidence LOW |
| Developers / contributor history / profile history | 0 / 0 / 0 |

No growth, emerging developers or ranking-bias verdict can be inferred from
these observations. Each of the two populated categories covers 60% of distinct
repositories; that overlap does not establish ecosystem representativeness.
All results came from the `recent` cohort, which excluded high-star established
repositories. The language and cohort concentration are measured sampling
limitations. Topic aggregate numeric zero with zero usable momentum is missing
evidence, not measured zero growth; `observed_momentum_total` is null in that case.

## Ranking diagnostics and reliability audit

Reports include ranked identity order, size/age/category composition, small
star baselines, incomplete windows and sparse high-confidence results. Developer
reports retain lifetime contributions, observed deltas, emerging components,
follower counts and single-repository/bridge evidence. Top composition is
descriptive: there is no causal or statistical bias claim.

Deterministic synthetic fixtures demonstrate the following existing behavior:

1. Missing history stays unavailable. Equal observations at distinct timestamps
   produce a genuine zero delta. A single high-star observation has no momentum.
2. A repository growing from 1 to 2 stars and 1 to 2 forks scores above one
   growing from 100000 to 101000 stars and 1000 to 1001 forks. Relative-growth
   weighting explains this; the 100% cap limits it but does not remove the
   small-base effect. Large lifetime counts do not directly increase momentum.
3. Two observations exactly seven days apart produce existing confidence
   **0.796 / HIGH**. This measures endpoint coverage under the current formula,
   not dense history, statistical probability or a 79.6% chance of being correct.
   The report explicitly counts HIGH results with fewer than three observations.
4. Absolute follower counts do not increase emerging or bridge scores; observed
   follower **growth** does affect emerging scores and evidence. A large lifetime
   contribution count with one observation does not become recent activity.
5. A single repository can satisfy emerging evidence through an observed delta
   plus newcomer/profile signals. Single-repository counts stay visible rather
   than implying broad activity. The existing bridge small-sample cap is 0.35;
   adding many topics to one repository does not remove that cap.

The 500-repository fixture tests deterministic aggregation, overlap and ranking
diagnostics, not production latency or real ranking quality. There is no formula
or weight change in Checkpoint C.

### Existing correctness issues: separate proposed fixes

These are documented for a later, explicitly reviewed change, not repaired here:

- `classify_trend` can describe a partial fallback span as “7-day” growth and
  classify a six-hour decline as declining. `compute_momentum` also accepts
  partial windows by existing design. The diagnostic reports the raw existing
  label and flags `trend_supported_by_complete_7d: false`. Proposed narrow fix:
  make trend explanations use the actual observed span and explicitly label
  partial coverage; requiring complete windows for classification/ranking is a
  separate policy decision. Never infer a negative full-period trend from
  insufficient coverage.
- `storage.newest_observation_at` returns newest contributor time whenever one
  exists, otherwise newest repository time. It can therefore precede newer
  repository observations, contrary to its “newest anywhere” description.
  Proposed narrow fix: return the maximum of both non-null timestamps and add
  a regression where repository history is newer. The current diagnostic keeps
  existing semantics and exposes
  `developer_anchor_precedes_repository_observation`. It is false in this pilot
  and true in the dedicated synthetic PostgreSQL regression.

## Receipt costs and scaling estimates

The pilot consumed seven attempt reservations: two search (28.57%), five detail
(71.43%), zero contributors/profiles. Five new repositories gives **1.4 attempts
per new repository**. One of six candidates was a duplicate: **16.67%**. Duration
was **3.805 seconds**. One completed run, zero partial/unfinished runs and zero
budget exhaustion are observed frequencies, not reliability guarantees.

All receipts contribute to accounting, including partial and unfinished runs;
newer durable `requests_json` reservations take precedence over old report JSON.
Only completed discovery runs with final durations feed the illustrative yield
projection. Refresh and enrichment do not pollute the discovery denominator.
With no completed yield, estimates are null. Unfinished duration is only the
last checkpoint duration, never an inferred final duration. Reservations can
conservatively overcount wire sends if a process dies before transport.

| New repository target | Linear estimate from 7/5 | Separate scenario HTTP cap |
| --- | ---: | ---: |
| 50 | 70 | 261 |
| 100 | 140 | 486 |
| 300 | 420 | 1386 |
| 500 | 700 | 2286 |

The estimates are **not statistically reliable**: one run with five saved
repositories cannot establish typical overlap, result availability, retries or
future costs. These are additional-new-repository targets, not total dataset
sizes. Zero enrichment calls does not establish free enrichment.

The separate upper-bound scenario assumes 12 balanced cohorts, 100 items/page,
candidate slots equal to 1.5 times the target rounded up, at most three attempts
per search page or candidate detail, and no enrichment. Its cap is
`3 × (planned search pages + candidate cap)`; it would apply only if configured
and is **not** a guarantee of the target yield or a recommended single run.
It deliberately allows all candidates to require details/retries. Actual
configured request/runtime limits may stop earlier. The smaller next-pilot
budget below is independent of these illustrative 50–500 scenarios.

## Controlled expansion proposal — not executed

`config/discovery-expansion-proposal.toml` is a separate reviewable configuration.
The default discovery catalog and collection implementation remain unchanged.

| Category | Query |
| --- | --- |
| AI Agents | `topic:ai-agents` |
| Coding Agents | `topic:coding-agent` |
| MCP | `topic:mcp` |
| Developer Tools | `topic:developer-tools` |
| Obsidian & PKM | `topic:obsidian` |
| Local AI | `topic:local-llm` |

These are proposed tag-based sampling queries, not verified complete category
definitions. Obsidian is only part of PKM, and local-LLM tags are only part of
Local AI. Overlapping categories and already tracked IDs reduce new yield.

Each category has two cohorts, five candidates apiece: `recent` (created within
365 days, 10–999 stars) and `established-active` (1000+ stars, pushed within 30
days). Both sort by latest update, with public/nonarchived/nonfork filters.
This provides 12 cohorts, 60 candidate slots and 12 search pages before retries.
The aim is **30–50 additional repositories**, with a hard cap of **50 new**,
**180 HTTP attempts** (search 36, details 144), **0 contributor/profile attempts**,
concurrency **1**, and a **300-second** collection deadline. Allow roughly
30–180 seconds under ordinary latency; this is unbenchmarked, not an SLA.

Local planning and dry-run only:

```bash
uv run github-radar discover-plan \
  --config config/discovery-expansion-proposal.toml \
  --as-of 2026-10-16 > /tmp/github-radar-expansion-reviewed.json
uv run github-radar discover-execute /tmp/github-radar-expansion-reviewed.json --dry-run
```

Set `--as-of` to the intended planning date when preparing the actual review.
After separate authorization, the existing `discover-execute` command can use
that exact reviewed file. No execution command has been run in Checkpoint C.
Use the existing bounded retry behavior (at most three attempts/request at the
default setting); retries consume the shared caps. Never auto-restart a partial
run or increase limits. Stop on authentication/ownership loss, quota refusal,
deadline or budget exhaustion. If the new-repository cap prevents finishing
the requested cohorts, the run is honestly partial. Item failures may allow
remaining bounded work but still prevent success. Inspect the receipt and
quality report before any next authorization. Do not backfill to force 50.

## Separate follow-up observation proposal — not executed

Dataset expansion alone does not create growth evidence. After discovery,
manually re-observe the tracked cohort after at least 24 hours, around day 7,
and around day 30; preferably repeat daily at a similar time when authorized.
Keep observations near each target: a sparse day-30 poll with only day-7 history
cannot provide a complete 1d or 7d window. New timestamps record unchanged
counters; same-instant writes remain idempotent.

For at most 55 total repositories, a separately authorized refresh could use:

```bash
# Proposal only; performs live HTTP if explicitly executed.
uv run --env-file .env github-radar refresh --limit 55 --min-refresh-hours 24 \
  --max-seconds 180 \
  --budget '{"total":65,"search":0,"repository_details":65,"contributors":0,"profiles":0}'
```

The 65-attempt cap provides limited retry headroom; failures may make the run
partial. Selection remains stale-first. Inspect actual observation times before
repeating, rather than assuming every repository was refreshed. Do not call
unchanged state missing growth. No scheduler is introduced.

Developer ranking validation requires a distinct opt-in enrichment proposal:
for example, five explicitly selected tracked repositories, at most five
contributors each, at most ten profile attempts, contributor cap 10, detail cap
5, total cap 25 and 120 seconds. Profile/contributor TTL reuse means a second
call inside the default seven-day TTL may produce no fresh history. An initial
enrichment and another after TTL expiry are needed for meaningful contributor
deltas. Choose repositories across categories and then reassess coverage; do
not equate cumulative contributions with recent work or this sample with all
developers. This enrichment proposal is also unexecuted.

## Verification and limitations

Final checks: **455 tests passed** in 34.02 seconds, including **42 PostgreSQL
integration cases**, with no skips. The 28 new tests passed independently first.
Ruff passed; mypy passed for 129 source files; `git diff --check` passed.
Three existing deprecation warnings remain (Alembic configuration and Starlette
HTTP-422). The full suite used `uv run --env-file .env pytest` so the dedicated
test database was available. Frontend gates were not rerun because no frontend
files changed. The real application was queried read-only; the local expansion
plan was generated and previewed without execution.

Tests cover empty/pilot datasets, overlapping recipes/categories, unknown
metadata, snapshot bands, complete/partial/missing windows, observed zero,
staleness, creation vs discovery, size/fame/single-repository effects, receipts,
interruption accounting, CLI failures and JSON. PostgreSQL cases use only
`github_radar_test` with the original safety guard plus an exact-name check.
The diagnostic transaction is verified read-only; fake HTTP calls do not
increase while commands run. Synthetic fixtures extend to 500 repositories.

All evidence is loaded in memory for a manual report. Batched database reads
avoid per-repository report queries, but arbitrarily large lifetime history and
all-pairs category overlap are not streaming operations. There is no new
retention system, historical metadata reconstruction or statistical inference.
The known scoring/anchor issues above require separate review. No schema,
analytics formula, safety guard, collection behavior or frontend change is part
of this checkpoint.
