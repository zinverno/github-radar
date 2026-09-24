# Ecosystem graph (Phase 4)

Phase 4 turns the *whole tracked dataset* into one derived **ecosystem graph**:
developers, repositories and topics connected by real, evidence-backed edges.
Everything below is a pure function of the stored PostgreSQL dataset — there is
no separate graph store, no graph database, and no wall-clock dependence.
Results are reproducible between runs because the reference instant is a real
observation time, never "now".

The graph is deliberately a **co-contribution and co-occurrence** model, not a
social graph:

* Two developers are connected only when they both contributed to at least one
  tracked repository in common.
* Two repositories are connected only when they share a contributor or an
  owner.
* Two topics are connected only when they are tagged onto the same tracked
  repository.

Nothing more is implied by any of it.

## Model

| Edge | Meaning | Evidence carried |
| --- | --- | --- |
| `OWNS` (Developer → Repository) | the repository's `owner_id` relationship | — |
| `CONTRIBUTES_TO` (Developer → Repository) | the developer appears in the repository's contributor list | cumulative contributions, per-repository share, full contributor-snapshot observation history |
| `TAGGED_WITH` (Repository → Topic) | the repository carries the topic tag | — |

Duplicate relational rows never produce duplicate edges: every edge family is
keyed on its natural identifiers, so re-adding the same relationship collapses
into the existing edge. Repository nodes also carry their Phase 2 momentum so
bridge and relationship scores can reuse it.

The graph is assembled fresh from PostgreSQL by `graph/loader.py`
(`load_graph`) using a **fixed number of batched queries** (`repositories +
latest`, `topics by repository ids`, `snapshots by repository ids`,
contributor links, contributor snapshots, developers by id) — never one query
per repository or per contributor link. The regression is pinned in the
integration suite: doubling the dataset does not proportionally increase the
statement count.

`reference_now` is anchored to the newest observed `captured_at` anywhere in
the dataset, falling back to the wall clock only when nothing has ever been
observed.

## Overlap

`graph/overlap.py` computes a jaccard `Overlap` (intersection, union, score)
shared by all pairwise relationships. Every derived relationship keeps its
overlap components **separate** — no opaque combined number is ever produced
without its parts being visible:

* **Repository ↔ repository**: developer overlap and topic overlap are reported
  separately; a combined `relationship_score` (`0.50 · developer_overlap +
  0.50 · topic_overlap`) exists only alongside its components, and requires at
  least one shared developer or one shared topic.
* **Topic ↔ topic**: repository overlap and developer overlap stay separate;
  topics need at least one shared repository or one shared developer.
* **Developer ↔ developer (co-contribution)**: strength is a log-scaled
  breadth term plus capped per-repo shares, momentum and observed co-activity,
  and every result references at least one real shared repository.

## Reach & centrality

`graph/metrics.py`:

* **Reach** — how many nodes of each type one node touches through its direct
  edges:
  * `DeveloperReach` — contributed / owned / total distinct repositories,
    distinct peer developers, distinct topics reached.
  * `RepositoryReach` — contributing developers, owning developers, topics,
    sibling repositories.
  * `TopicReach` — repositories, contributing / owning / associated
    developers.
* **Centrality** — deterministic degree and weighted-degree centrality per
  node type (neighbourhoods are homogeneous, so degrees are comparable within a
  type):
  * developer neighbourhood = shared **associated** repositories (contributed
    or owned), weight = number of shared associated repositories;
  * repository neighbourhood = a shared associate (contributor or owner),
    weight = shared-associate count plus one when a common owner is present;
  * topic neighbourhood = shared tagged repositories, weight = number of
    co-occurring repositories.
* **Connected components** — all direct edges treated as undirected links.
  Components are deterministically ordered by `(-node_count, canonical node)`
  so identical graphs produce identical components; isolated nodes form their
  own singleton components. A component is just "these nodes are transitively
  reachable through tracked repositories and topics" — no social meaning.

## Confidence

`graph/confidence.py` reuses the Phase 2 confidence philosophy: a deterministic
data-coverage score in `[0, 1]` mapped to `LOW` / `MEDIUM` / `HIGH`, blending
evidence-repository count, history coverage, recent-activity coverage and
topic sample size. A relationship resting on 3+ repositories with observed
activity scores far above one old cumulative-contribution observation; missing
coverage contributes zero. Values below 3 distinct evidence repositories can
never reach `HIGH`.

## Bridge intelligence

A **bridge** is a node whose evidence *genuinely spans separate topic
ecosystems* — as opposed to one repository carrying many arbitrary tags. All
bridge scores in `graph/bridges.py` are bounded in `[0, 1]`, explainable
(weight × bounded-term components, weights listed as module constants), and
protected by small-sample caps.

### Developer bridge (`bridges develop`)

A developer is a bridge when they have meaningful memberships in **at least
two** tracked topic ecosystems (`BRIDGE_MIN_TOPICS = 2`) with at least 2
distinct supporting repositories and a lifetime of at least 10 total
contributions. Weighted components:

| Component | Weight |
| --- | --- |
| topics (memberships, saturate at 4) | 0.30 |
| topic_strength (mean per-topic evidence strength) | 0.25 |
| repo_span (distinct supporting repositories, saturate at 3) | 0.20 |
| distribution (evenness across memberships) | 0.10 |
| recent_activity (observed positive contribution movement) | 0.10 |
| momentum (supporting repositories) | 0.05 |

Followers never enter the score. Evidence below the minimums is **hard-capped**
at `SMALL_SAMPLE_CAP = 0.35` and flagged `small_sample` — one tiny contribution
can never be a bridge, and a celebrity with no contributions scores exactly
`0.0`.

### Cross-topic bridge (`bridges cross-topic A B`)

For two given topics, lists every developer with observed evidence in **both**
ecosystems and shows the shared tracked repositories that make the bridge real,
together with an aggregate of the whole bridge ecosystem between the two topics
(bridge count, distinct bridging developers, average score). The per-pair score
combines mean topic evidence strength, per-side repository span and observed
movement in both ecosystems (`0.45 / 0.30 / 0.25`).

### Repository bridge (`bridges repos`)

A repository bridges when the people working on it cross into other ecosystems:

| Component | Weight |
| --- | --- |
| contributors (log-dampened diversity, saturate at 20) | 0.30 |
| topics (log-dampened breadth, saturate at 10) | 0.20 |
| cross_repo_share (contributors also active in other repos) | 0.25 |
| cross_topic_share (contributors who are multi-topic themselves) | 0.15 |
| momentum | 0.10 |

When a repository has no contributors, `cross_repo_share` / `cross_topic_share`
are listed as **missing** (never a fabricated zero) and the score cannot reach
the full bound. A repository with many tags but no contributor ecosystem scores
below a real one.

## Health / honesty rules

* **Missing is not zero.** Unavailable momentum, recent activity, history
  coverage or cross-ecosystem shares contribute zero but are listed in the
  result's `missing_components` — the shortfall stays visible.
* **Small samples are capped.** Every producer enforces a hard ceiling
  (`SMALL_SAMPLE_CAP`) and flags the row so thin evidence is loud in the CLI.
* **Deterministic everywhere.** Sorting, saturation, weighting and component
  membership are all pure functions of the graph; the exact same dataset
  produces the exact same output on every run.

## Reports

`graph/reports.py` projects the graph into frozen report dataclasses:

* `EcosystemGraphReport` — node/edge counts, connected components, top
  developer / repository / topic hubs (centrality), top developer bridges.
* `DeveloperGraphReport` — reach, centrality, co-contributors, bridge.
* `RepositoryGraphReport` — reach, centrality, related repositories, bridge.
* `TopicGraphReport` — reach, centrality, related topics.
* `CrossTopicBridgeReport` — the bridge ecosystem between two topics.

A node that is not in the graph yields an empty report (zero reach, `None`
centrality/bridge), never an exception.

## CLI

All commands are reachable at the **top level**, and `bridges` is additionally
mounted inside the `graph` group so the older `graph bridges develop` spelling
keeps working.

```bash
github-radar ecosystem                          # whole graph: counts, edges, components, hubs, top bridges
github-radar bridges develop --topic mcp        # developers bridging topic ecosystems
github-radar bridges develop --login alice      # full per-developer bridge explanation
github-radar bridges cross-topic mcp ai         # bridge ecosystem between two topics
github-radar bridges repos                      # repositories ranked as cross-ecosystem bridges
github-radar related-topics mcp                 # topic graph footprint + related topics
github-radar related-repos octo/servers         # repository graph footprint + related repositories
github-radar developer adalovelace              # ... plus a "Graph footprint" section
github-radar topic mcp                          # ... plus a "Graph footprint" section
```

Common options: `--window 1d|7d|30d` (default `7d`), `--limit/-n`,
`--min-confidence`, `--centrality-limit`, `--bridge-limit`. Every bridge table
shows its `Components`, `Confidence` (score + level) and, for singular
reports, the supporting repositories and per-topic evidence.

## Module map

| Module | Responsibility |
| --- | --- |
| `graph/model.py` | `EcosystemGraph` + node/edge dataclasses, incremental construction, typed access, `filter()` |
| `graph/loader.py` | build the graph from PostgreSQL with a fixed number of batched queries |
| `graph/overlap.py` | jaccard `Overlap` |
| `graph/scoring.py` | shared `clamp` / `mean` / weighted-score helpers + `recent_activity` |
| `graph/confidence.py` | data-coverage confidence for relationships and bridges |
| `graph/relationships.py` | co-contributors, related repositories, related topics |
| `graph/metrics.py` | reach, degree/weighted-degree centrality, connected components |
| `graph/bridges.py` | developer / cross-topic / repository bridges |
| `graph/reports.py` | deterministic report projections used by the CLI |

## JSON export (deferred)

Machine-readable graph export (e.g. `github-radar graph export --json` writing
a node/edge JSON document for external tooling) is an explicit non-goal for
the moment: the CLI is terminal-first and the report dataclasses already expose
everything the JSON would contain. It remains a cheap add-on behind
`graph/reports.py` if a consumer appears.

## Known limitations

* The graph is a **snapshot of tracked data** — discovery is query/topic
  scoped, so connectivity reflects what is in the dataset, not all of GitHub.
* `CONTRIBUTES_TO` carries GitHub's *cumulative* contribution count; a single
  observation is never treated as recent activity (recent movement comes only
  from *deltas* between two contributor snapshots of the same link).
* Centrality is structural (degree-based), not spectral — deliberately, so it
  stays deterministic and explainable.