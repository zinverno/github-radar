# Developer intelligence (Phase 3)

This document explains the developer analytics behind the `developers`,
`developer` and `emerging-developers` CLI commands (and the developer sections
of `topic`): how the observations are collected, how each score is computed,
what the labels mean, and why the results are reproducible.

Everything is a **pure function of the stored observation history plus an
explicit reference instant**. The wall clock is never read: the CLI anchors on
the newest real observation `captured_at` in the dataset (only falling back to
"now" when the dataset has no observations at all — in which case there is
nothing to report).

## 1. Observation policy

Two new append-only tables record developer history (written during
`discover`/`update`, no extra API cost):

- `developer_snapshots` — one row per profile fetch: `followers`, `following`,
  `public_repos` at `captured_at`. One observation per instant per developer.
- `contributor_snapshots` — one row per repository↔developer link per
  contributor sync: the *cumulative* contribution count at `captured_at`.

Both reuse the Phase 2 policy: deduplicated on `captured_at` (a same-instant
re-observation replaces), different instants always append.

**Cumulative counts are not activity.** GitHub's `/contributors` count is
lifetime, so a single `contributor_snapshots` row says nothing about recent
activity. Activity is only the **observed delta** between two snapshots of the
same link. A missing window end is `None`, never a fabricated zero — this is
the same anti-pattern Phase 2 guards against for repositories.

## 2. Windowed deltas

`analytics/history.py` provides `ObservationSeries[T]` (an ordered,
de-duplicated series over any object with a `captured_at`) and `window_delta`:
for a window of `N` days ending at `reference_now`, the **base** is the newest
observation at or before `reference_now − N`, the **current** is the newest at
or before `reference_now`. The delta is only reported when both ends were
observed. `complete` is false when the base sits more than half the window from
the target — reported, but flagged.

- `analytics/developers.py` → `compute_profile_deltas` computes followers /
  following / public_repos deltas over 1d / 7d / 30d
  (`PROFILE_DELTA_WINDOWS_DAYS`).
- `compute_contribution_delta` does the same for one contribution link.

`developer LOGIN` on the CLI shows these windows; an asterisk marks an
incomplete window.

## 3. Topic relevance

`compute_topic_relevance` (in `analytics/intelligence.py`) scores how connected
a developer is to one topic's tracked repositories:

```
repo_range       = clamp(relevant_repos / 5)
share            = mean over topic links of min(link_share, 0.25) / 0.25
ownership        = clamp(owned_topic_repos / 3)
recent_activity  = damped(Σ positive contribution deltas in window)
momentum         = mean(associated repo momentum) / 10

weighted = 0.25·repo_range + 0.30·share + 0.20·ownership
         + 0.15·recent_activity + 0.10·momentum
score    = clamp(weighted)
```

Notes:

- "Relevant" means the developer contributes to (or owns) a tracked repository
  carrying the topic. With no such repository, relevance is `None`.
- Per-repo contribution share is capped at 25% (`SHARE_CAP`) so one giant
  repository cannot dominate a topic.
- `damped(x) = clamp(√x / √100)` — a small contribution delta contributes
  proportionally *less*: +1 → 0.1, +100 → 1.0.
- Confidence is a deterministic coverage score (sample size, share of links
  with history, history span), mapped to `LOW`/`MEDIUM`/`HIGH` like Phase 2.

## 4. Activity

`compute_activity` measures **observed** contribution movement inside the
tracked ecosystem over a window:

```
observed_delta      = damped(Σ positive contribution deltas in window)
breadth             = active_links / usable_links
window_completeness = complete_links / usable_links

score = 0.50·observed_delta + 0.30·breadth + 0.20·window_completeness
```

`available=True` only when at least one link has a usable window (both ends
observed). History that exists but has no usable window is reported as
`available=False` — explicitly **not** zero. No observation history at all
returns `None`.

## 5. Ecosystem score

`compute_ecosystem_score` measures how strongly connected the developer is to
tracked repositories that matter *now* (momentum/trend, ownership, breadth):

```
topic_relevance = mean of this developer's relevance scores (over its topics)
momentum        = mean(associated repo momentum) / 10
activity        = activity score (0 when unavailable)
ownership       = clamp(owned_repos / 3)
breadth         = clamp(associated_repos / 5)

score = 0.25·topic_relevance + 0.25·momentum + 0.20·activity
      + 0.15·ownership + 0.15·breadth
```

Absolute popularity (followers, stars) never enters this score.

## 6. Emerging classification

`compute_emerging` labels every tracked developer from activity that is
**increasing**, weighted toward observed contribution movement:

```
positive_delta   = damped(Σ positive contribution deltas)
growing_repos    = share of associated repos with momentum ≥ 3.0 or trend "rising"
newcomer         = 1 if first seen within 30 days
follower_growth  = √(max(0, Δ7d followers / base followers · 100) / 100)
breadth          = clamp(active_repos / 2)

score = 0.35·positive_delta + 0.25·growing_repos + 0.20·newcomer
      + 0.10·follower_growth + 0.10·breadth
```

Labels are decided in this precedence order:

| Label | Condition |
| --- | --- |
| `INSUFFICIENT_HISTORY` | confidence < 0.30 (refuses to guess) |
| `EMERGING` | score ≥ 0.30 **and** ≥ 2 evidence points **and** a positive contribution delta observed |
| `ACTIVE` | observed contribution activity with score ≥ 0.15, without meeting the emerging criteria |
| `ESTABLISHED` | ≥ 90 days of history or ≥ 200 lifetime cumulative contributions |
| `QUIET` | association history exists but no recent positive movement was observed |

Evidence points = number of active links, +1 if the developer is a newcomer,
+1 if follower growth was observed. Protections this enforces:

- a **lone +1 contribution** saturates the delta term only slightly
  (√-dampening) and yields 1 evidence point — never `EMERGING`;
- being famous (many followers) contributes nothing — only observed growth
  does;
- thin history is labelled `INSUFFICIENT_HISTORY`, never guessed at.

## 7. Public contacts

`PublicContactMethods` is built only from profile fields GitHub exposes on
purpose: the profile URL, the public email field, the blog/website field and
the twitter username. Nothing is scraped, inferred or extrapolated. `--has-public-contact`
on `developers` keeps developers exposing at least one of these channels.

## 8. CLI commands

| Command | What it shows |
| --- | --- |
| `developers [--topic --sort --window --limit --min-confidence --min-followers --has-public-contact --repository --language]` | table of tracked developers with relevance, activity, ecosystem, emerging label + confidence |
| `developer LOGIN` | one developer: public contacts, profile deltas (1d/7d/30d), activity, ecosystem score, per-repository contribution links with observed Δ, topic relevances |
| `emerging-developers [--limit --window --min-confidence --min-followers]` | developers classified `EMERGING`, most-rising first |
| `topic NAME` | the Phase 2 repo list plus the developers relevant to the topic |

The window options accept `1d`, `7d` or `30d` (default `7d`).

## 9. Determinism

Every score above is a pure function of the stored observations and an explicit
`reference_now`. Re-running `developers` without new data gives identical
numbers; inserting historical observations changes the result reproducibly.
Confidence here is the same deterministic data-coverage idea as Phase 2, never
a statistical claim.

## 10. Tuning knobs

All weights and floors are module constants in `analytics/intelligence.py`
(e.g. `RELEVANCE_WEIGHTS`, `ACTIVITY_WEIGHTS`, `ECOSYSTEM_WEIGHTS`,
`EMERGING_WEIGHTS`, `EMERGING_THRESHOLD`, `EMERGING_MIN_CONFIDENCE`,
`REQUIRED_EVIDENCE`, `SHARE_CAP`, `NEWCOMER_DAYS`) and are covered by
regression tests in `tests/unit/test_developer_analytics.py` and
`tests/unit/test_developer_intelligence.py`.