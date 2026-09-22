# Trending & topic analytics (Phase 2)

This document explains the Phase 2 analytics behind the `trending`, `topics`
and `topic` CLI commands: how scores are computed, what the labels mean, and
why the results are reproducible.

Everything is a **pure function of the stored snapshot history plus an
explicit reference instant**. The wall clock is never read, so running the same
command twice (or after simply inserting snapshots) always produces the same
numbers.

## 1. Momentum score

Defined in `analytics/momentum.py`. One explainable, bounded number per
repository (or `None` when it cannot be computed):

```
stars_growth = clamp(100 · Δstars / base_stars, 0, 100)
forks_growth = clamp(100 · Δforks / base_forks, 0, 100)
recency      = clamp(1 − days_since_last_push / 90, 0, 1)
score        = 0.05 · stars_growth
             + 0.03 · forks_growth
             + 1.00 · recency
```

- `Δstars` / `Δforks` and `base_*` come from the **7-day window** embedded in
  `RepoMetrics`: the base is the newest snapshot at or before
  `newest_captured_at − 7 days`, if one exists with a positive base.
- Growth is expressed in **percentage points**, capped at
  `MAX_GROWTH_PCT = 100`. Recency uses `RECENCY_HALF_LIFE_DAYS = 90`.
- The score is bounded: `MAX_SCORE = 0.05·100 + 0.03·100 + 1.0·1 = 9.0`.
- `compute_momentum(...)` returns `None` when either growth delta or its base
  is missing/zero — momentum is never fabricated. `trending` therefore only
  lists repositories with at least two snapshots spanning the window.

The CLI's reference instant is the repository's newest `captured_at` (the
latest stored snapshot), so the "recency" term answers "how stale is the last
push **as of our newest observation**".

## 2. Trend label

Defined in `analytics/trends.py`. Every tracked repository is classified from
its 7-day star growth and recency, in this precedence order:

| Label | Condition |
| --- | --- |
| `new` | fewer than `TREND_MIN_SNAPSHOTS` (2) snapshots |
| `rising` | star growth ≥ `RISING_GROWTH_PCT` (+5 pp) |
| `declining` | star growth ≤ `DECLINING_GROWTH_PCT` (−5 pp) |
| `inactive` | recency < `INACTIVE_RECENCY` (0.05), i.e. no push for ~85 days |
| `steady` | everything else (including no usable star baseline) |

`rising`/`declining` outrank staleness; `inactive` outranks a flat middle.

## 3. Confidence (data coverage)

Defined in `analytics/confidence.py`. Confidence is **not** a p-value — it
answers "how much of the data the metric actually had to work with", as a
deterministic `[0, 1]` score mapped to `LOW` / `MEDIUM` / `HIGH`.

**Repository window confidence** (weights sum to 1.0):

```
count_term = 0.34 · clamp(snapshot_count / 5, 0, 1)
span_term  = 0.33 · clamp(window_span_days / window_days, 0, 1)
gap_term   = 0.33 · clamp(1 − |base_gap_days| / (window_days/2), 0, 1)
```

The base is the newest snapshot at or before `newest_captured_at − window_days`.
A missing base makes span/gap terms zero → LOW. `HIGH ≥ 0.75`, `MEDIUM ≥ 0.45`.

**Topic confidence** blends size, momentum coverage and complete windows:

```
score = 0.50 · clamp(count / 10, 0, 1)
      + 0.30 · share_with_momentum
      + 0.20 · share_complete_windows
```

A topic with fewer than `TOPIC_MIN_REPOS_FOR_HIGH` (10) repositories can never
reach `HIGH`.

## 4. Topic aggregation

Defined in `analytics/topics.py`. `aggregate_topics` turns the per-topic
reports into:

```
TopicAggregate(name, repository_count, total_momentum,
               avg_momentum, share_with_momentum)
```

`total_momentum` sums the (possibly `None`) per-repository momentum scores
(zero for repos without one); `avg = total / count`.
Sorted by total momentum **descending**, then name ascending.

## 5. CLI commands

All three commands read only the local database — no GitHub calls.

| Command | Output (columns) |
| --- | --- |
| `github-radar trending --limit 10` | full_name, stars, score, stars %, forks %, recency, confidence |
| `github-radar topics --limit 10` | topic, repository count, total momentum, avg momentum, coverage %, confidence |
| `github-radar topic mcp --limit 10` | repositories of topic `mcp`, ranked by momentum with confidence |

- Columns that come from an unavailable computation render as `-`; floats are
  formatted with two decimals. `Confidence` shows the numeric score
  (`0.00–1.00`) for the repository tables (`trending`, `topic`) and the coarse
  level (`LOW` / `MEDIUM` / `HIGH`) for the topic table (`topics`).
- Topic names are normalized the same way as storage
  (`trim` + lowercase, via `storage.normalize_topic`).
- `--limit` defaults to 10 for each command.

## 6. Determinism & correctness guarantees

- No `datetime.now()` anywhere in scoring: the reference instant is always
  explicit (derived from `captured_at` in the CLI).
- Every dataclass in `analytics/` is `frozen`; computations are pure.
- Weights, thresholds and windows are module-level constants so tuning is a
  one-line change, and threshold changes are regression-tested
  (`tests/unit/test_confidence.py`, `test_trends.py`, `test_momentum.py`).
- Repositories without a computable momentum are excluded from `trending`
  rather than assigned a fabricated score.

## 7. Observations, not just state changes

Analytics reflect the **observation log**: a 7-day window is a truthful `0`
only when the repository was observed at both ends and the counters were equal
(`metrics.stars_7d.delta == 0`). When a repository was observed only once, the
window is *missing* (`stars_7d is None`, momentum `None`, trend `new`) — never
a fabricated zero. The two cases are therefore distinguishable:

| History | 7-day delta | Momentum | Confidence |
| --- | --- | --- | --- |
| observed once | `None` (unavailable) | `None` | `LOW` |
| observed twice, unchanged | `0` (true zero) | scored | window terms computed |

To record an unchanged re-poll the storage boundary exposes
`record_observation` (writes every observation; same-instant dupes are
idempotent). `insert_snapshot_if_changed` remains the state-change-only filter
used by `discover`/`update`. **No production scheduler exists** — Phase 2 only
makes periodic observation possible from storage.

## 8. Tuning knobs

| Constant | Default | Effect |
| --- | --- | --- |
| `WEIGHTS` (`momentum.py`) | `0.05 / 0.03 / 1.0` | momentum component weights |
| `MAX_GROWTH_PCT` | `100` | growth pp cap |
| `RECENCY_HALF_LIFE_DAYS` | `90` | recency decay over days |
| `WINDOW_DAYS` | `7` | growth window |
| `RISING_GROWTH_PCT` / `DECLINING_GROWTH_PCT` | `5` / `-5` | trend boundaries |
| `INACTIVE_RECENCY` | `0.05` | staleness threshold |
| `W_COUNT` / `W_SPAN` / `W_GAP` | `0.34 / 0.33 / 0.33` | repo confidence weights |
| `TOPIC_W_COUNT` / `TOPIC_W_MOMENTUM` / `TOPIC_W_WINDOW` | `0.50 / 0.30 / 0.20` | topic confidence weights |
| `TOPIC_MIN_REPOS_FOR_HIGH` | `10` | topic HIGH floor |