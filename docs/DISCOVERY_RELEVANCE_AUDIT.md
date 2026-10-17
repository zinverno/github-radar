# Discovery relevance audit — after Pilot 02A

The 25 tracked repositories form a useful exploratory sample, but the catalog mixes software products, supporting infrastructure, frameworks, extensions, registries and resource lists. Topic matches do not consistently deliver the repository role that a category name suggests. Clarifying category scope and separating resource repositories is justified before expanding collection. This audit does not justify changing scoring algorithms.

**Evidence and boundaries**

Read at **2026-10-17 09:56:19 UTC** from the configured local application PostgreSQL database, in a database-enforced **REPEATABLE READ, READ ONLY** transaction. Sources: `repositories`, `repository_topics` joined to `topics`, `repository_snapshots`, `discovery_provenance` and `collection_runs`. The snapshot contains **25 distinct GitHub repository IDs, 25 observations, 349 repository/topic associations, 30 provenance rows and two completed discovery runs**. Every repository has exactly one observation and at least one provenance row. All stored fork, archived and disabled flags are false.

Descriptions, topics, languages, stars and provenance below are stored evidence, not live GitHub verification. Descriptions are repository-authored claims; performance, security and reliability claims were not tested. Type and semantic relevance are editorial assessments, not stored classifications or new scores. No README or source inspection of the tracked projects was performed. Missing language is reported as unknown; it is not used to infer type.

Only this Markdown report was added to the checkout. No catalog, scoring, collection code or database records were changed. No discovery, enrichment, refresh, live GitHub request, commit or push was performed.

**Discovery provenance**

| Run label | Stored run ID | Collection time (UTC) | Stored plan and result |
| --- | --- | --- | --- |
| P1 — initial pilot | `88c9195e-fa20-4904-903f-b8cadb836945` | 2026-10-16 17:47:18–17:47:22 | AI Agents and Developer Tools; recent only; 3 candidates per cohort; 6 candidates → 5 new repositories, 1 repeated ID. |
| P2 — Pilot 02A | `ce3013a1-1566-474d-ba7d-5b108d70ea36` | 2026-10-17 09:41:26–09:41:43 | Six categories × two recipes × 2 candidates; 24 candidates → 20 new repositories, 4 repeated-ID occurrences. |

Both stored plans use **`sort=updated`, `order=desc`** for every executed cohort. In the review table, **R** means `recent`, **E** means `established-active`. Category membership comes only from provenance, even where other topics suggest additional semantic fits.

| Stored category | Topic selector |
| --- | --- |
| `ai-agents` | `topic:ai-agents` |
| `coding-agents` | `topic:coding-agent` |
| `mcp` | `topic:mcp` |
| `developer-tools` | `topic:developer-tools` |
| `obsidian-pkm` | `topic:obsidian` |
| `local-ai` | `topic:local-llm` |

The exact stored queries concatenate the selector above with these suffixes, in this order:

| Run / recipe | Exact query suffix |
| --- | --- |
| P1 / R | `is:public archived:false fork:false created:>=2025-10-16 stars:10..999` |
| P2 / R | `is:public archived:false fork:false created:>=2025-10-17 stars:10..999` |
| P2 / E | `is:public archived:false fork:false stars:>=1000 pushed:>=2026-09-17` |

The current [expansion proposal](../config/discovery-expansion-proposal.toml) describes these six selectors but has a larger quota of 5 per cohort. The saved P2 plan, with 2 per cohort, is the execution evidence. The separate [default catalog](../config/discovery.toml) has `data-engineering`, which has **zero tracked provenance memberships and was not searched in either pilot**. That is an unsampled category, not evidence of poor search relevance.

**Repository review**

“Strong” means the description directly supports the category's subject. “Adjacent” means supporting infrastructure, a companion or an integration fits a broad ecosystem interpretation but may disappoint a user expecting a standalone agent or application. “Tag-only” means the stored description does not substantiate that particular category capability; it does not prove the tag is false. Uncertainty concerns packaging, category scope or unverified capabilities, as specified per row. Every recorded category is assessed, including all memberships of shared repositories.

| Repository | Stored category · provenance | Type | Primary language · stored stars | Relevance assessment | Stored evidence | Uncertainty / expectation risk |
| --- | --- | --- | --- | --- | --- | --- |
| `100yenadmin/hermes-teammates` | `ai-agents` · P2/R | plugin/extension | Python · 79 | Strong agent-ecosystem fit; adjacent to standalone agents. | Description adds named subagent teammates to stock Hermes; topics `hermes-plugin`, `multi-agent`, `subagents`. | Depends on Hermes; extension packaging is inferred from the description plus explicit plugin topic. |
| `Artexis10/exomem` | `obsidian-pkm` · P2/R | developer infrastructure | Python · 10 | Strong PKM integration fit. | Description explicitly says self-hosted MCP server for searching Obsidian/Markdown vaults; topics `obsidian`, `mcp-server`, `knowledge-base`. | A retrieval service, not evidence of an Obsidian plugin or a standalone note editor. Performance claims unverified. |
| `Aureliolo/gdharness` | `mcp` · P2/R | developer infrastructure | TypeScript · 25 | Strong MCP integration fit. | Godot agent harness with editor addons, runtime bridge, MCP server and installation/diagnostic CLI. | Mixed components; classified by its overall tooling role. Godot-specific, not a general MCP framework. |
| `Health-525/courseraptor` | `obsidian-pkm` · P2/R | application/product | TypeScript · 36 | Adjacent; Obsidian is one supported interface. | Chinese description identifies a university AI study assistant for schedules, tasks, knowledge and documents, supporting web, QQ and Obsidian; topics `student-tools`, `course-schedule`, `obsidian`. | Obsidian support is explicit, but its depth is unknown. Student workflow is the main stated purpose. |
| `Innei/lody-ios` | `coding-agents` · P2/R | application/product | Swift · 519 | Adjacent companion/client. | Description explicitly identifies an independent iOS client for Lody with chat, sessions and coding-agent collaboration. | Does not establish that the repository implements a coding-agent engine. Related product family to `LodyAI/Lody`, but a distinct repository ID. |
| `LodyAI/Lody` | `coding-agents` · P2/E | application/product | TypeScript · 1,424 | Strong collaboration-ecosystem fit; adjacent to agent implementations. | “Share coding agents with your team on phone and desktop”; topics `collaboration`, `orchestration`, `coding-agent`. | Description establishes a sharing product, not whether it supplies its own coding agent. |
| `Navide-dev/Navide` | `ai-agents`; `developer-tools` · both P2/R | application/product | TypeScript · 36 | Strong in both recorded categories. | Agentic Development Environment coordinating AI software teams, workflows, context and human collaboration across the development lifecycle. | Description supports the role, not implementation completeness. It has `coding-agents` plural, not the catalog's singular `coding-agent` selector. |
| `ai-sdlc-framework/ai-sdlc` | `ai-agents`; `developer-tools` · both P1/R | framework/library | TypeScript · 414 | AI Agents: adjacent governance/orchestration; Developer Tools: strong. | Description explicitly says declarative governance framework for AI-augmented development; topics `ai-governance`, `quality-gates`, `orchestration`, `ai-agents`. | Framework scope is explicit; autonomous agent execution is not established by the short description. |
| `ansvisor/ansvisor` | `mcp` · P2/R | application/product | TypeScript · 144 | **Tag-only for MCP**; primary described purpose is AI search visibility/marketing analytics. | Description covers visibility, citations, competitors and content opportunities; `mcp` occurs among 20 topics. | No MCP server/client capability appears in the stored description. Integration may exist, but cannot be established here. |
| `atoma-run/atoma` | `ai-agents` · P1/R | other/uncertain | TypeScript · 20 | Strong subject-level agent fit. | “AI agents produce and check” finished work; topics `agent-orchestration`, `llm-agents`, `sandbox`. | Tagline does not distinguish application, framework or infrastructure. Type remains uncertain. |
| `bigduu/Bamboo-agent` | `coding-agents` · P2/R | developer infrastructure | Rust · 21 | Adjacent/general agent runtime; coding specialization is not demonstrated. | Description calls it a local-first agent harness/runtime with sessions, tools, MCP, subagents and workflows; topics `agent-runtime`, `agent-framework`, `coding-agent`. | Runtime role is explicit; library versus service packaging and coding-specific behavior remain unverified. Local-first does not establish local model inference. |
| `esengine/DeepSeek-Reasonix` | `coding-agents`; `developer-tools` · both P2/E | application/product | Go · 35,823 | Strong in both recorded categories. | Description explicitly says coding agent for complex software engineering; topics `cli`, `terminal`, `tui`, `coding-agent`. | Classified by stated agent function; plugin/framework topics leave packaging uncertain. Reliability claim unverified. |
| `harshalgajjar/Reminal` | `ai-agents` · P1/R | application/product | Go · 323 | **Adjacent; weak evidence for an agent itself.** | Description is browser access to remote windows, desktops and terminals; topics include `remote-desktop`, `remote-access`, `ai-agents`, `claude-code`, `codex`. | Agent-related tags may describe use cases. Autonomous agent behavior is not stated; security claims unverified. |
| `hivecommons/hive` | `developer-tools` · P2/R | other/uncertain | Go · 67 | Strong development/maintenance tooling fit. | Description says customizable AI-agent orchestration for project maintenance; topics `ci-cd`, `code-review`, `devops`, `github-automation`. | Function is clear; product, framework or hosted/runtime infrastructure packaging is not. |
| `hongfaqiu/strata-manager` | `local-ai` · P2/R | application/product | Rust · 16 | Strong local-LLM fit. | Description explicitly says desktop app managing a local inference engine, models, services, hardware, chat and inference tuning. | A manager for another engine, not the engine itself; stated platform coverage is Windows/Linux x64. |
| `kstevica/captain-claw` | `local-ai` · P2/R | framework/library | Python · 169 | Relevant but mixed local/cloud scope. | Description explicitly says self-hosted, model-agnostic, local-friendly agent framework; topics include `local-llm`, `ollama`, `openai`, `anthropic`. | Local-friendly does not establish offline completeness or local-only execution. |
| `laurent22/joplin` | `obsidian-pkm` · P2/E | application/product | TypeScript · 56,766 | Strong broad PKM fit; not evidence of an Obsidian extension. | Description explicitly identifies a cross-platform note-taking app with sync; topics `note-taking`, `obsidian`, `evernote`. | Good if the category includes alternative PKM applications; potentially misleading if the intent is Obsidian-specific software. |
| `morluto/rea` | `ai-agents`; `developer-tools`; `mcp` · all P2/E | other/uncertain | TypeScript · 297,122 | AI Agents and Developer Tools: strong; **MCP: tag-only**. | Description says reverse engineering with agents; topics `binary-analysis`, `cli`, `dsh-plugin`, `ai-agents`, `developer-tools`, `mcp`, `model-context-protocol`. | CLI/extension packaging is unresolved; the description does not state its MCP interface. Large stored star count is not independently verified. |
| `obsidianmd/obsidian-releases` | `obsidian-pkm` · P2/E | documentation/registry | unknown · 22,299 | Strong ecosystem fit; **repository-role mismatch for an application catalog**. | Description explicitly says community plugin list, theme list and Obsidian releases. | Not evidence of the Obsidian application source. Registry/release role is clear despite missing primary language. |
| `pipeshub-ai/pipeshub-ai` | `ai-agents` · P2/E | developer infrastructure | Python · 3,837 | Strong agent context infrastructure fit. | Description says context layer, permission-aware searchable company knowledge, MCP, SDKs and built-in agents. | Mixed workspace and agent functionality; infrastructure is the dominant described role. Access-control claims unverified. |
| `rafska/awesome-local-llm` | `local-ai` · P2/E | curated resource list | unknown · 2,979 | Strong topical relevance; **repository-role mismatch for runnable local AI software**. | Description explicitly says curated list of platforms, tools, practices and resources; topics `awesome-list`, `resources`, `local-llm`. | Type is well supported. List popularity/activity should not be interpreted as runtime adoption or software development. |
| `raullenchai/Rapid-MLX` | `local-ai` · P2/E | developer infrastructure | Python · 3,987 | Strong local inference fit. | Description explicitly says Apple Silicon LLM inference server built on MLX; topics `inference-server`, `local-llm`, `apple-silicon`. | Hardware-specific; compatibility and “up to 4×” performance are unverified author claims. |
| `smithersai/smithers` | `developer-tools` · P1/R | framework/library | TypeScript · 439 | Strong developer framework fit. | Description explicitly says agentic workflow framework defined in TypeScript configuration; topics `workflow-engine`, `durable-execution`, `developer-tools`. | Framework classification is clear; durability/reliability claims were not tested. |
| `tianma-if/edgeever` | `mcp` · P2/E | application/product | TypeScript · 2,314 | Strong MCP-enabled product fit; primarily a PKM application. | Description explicitly says knowledge base and Evernote/Obsidian alternative with native MCP; topics `knowledge-base`, `note-taking`, `personal-knowledge-base`. | MCP interface details unknown. No `obsidian-pkm` provenance; stored topic is `obsidian-alternative`, not `obsidian`. |
| `vibheksoni/session-export` | `developer-tools` · P1/R | developer infrastructure | Python · 13 | Strong developer utility fit. | Description says coding-session converter with SDK, CLI, MCP chat recall and trace export; topics `session-converter`, `python-sdk`, `cli-tool`. | Multi-interface utility rather than an agent implementation; infrastructure is the functional classification. |

**Category concentration and overlap**

Counts below use distinct repository IDs within each provenance category. “Shared” means also recorded in another curated category, not merely having another category's topic. For this audit only, **10,000+ stored stars** is a transparent large-repository threshold; it is not a scoring change or a maturity classification. Star totals are lifetime counts from each repository's single stored observation, not growth.

| Category | Distinct repositories | Shared with another category | 10,000+ stars | Largest repository's share of category stars | Interpretation |
| --- | ---: | ---: | ---: | --- | --- |
| `ai-agents` | 7 | 3/7 (42.9%) | 1/7 | `morluto/rea`: 98.4% | Mix of agent products, governance, extension and context infrastructure; one giant dominates star mass, not repository count. |
| `coding-agents` | 4 | 1/4 (25%) | 1/4 | `DeepSeek-Reasonix`: 94.8% | Two repositories describe Lody collaboration/client roles; one describes a general runtime. Narrow sample of coding-agent implementations. |
| `mcp` | 4 | 1/4 (25%) | 1/4 | `morluto/rea`: 99.2% | Two descriptions explicitly establish MCP (`gdharness`, `edgeever`); two provide only topic evidence (`ansvisor`, `rea`). |
| `developer-tools` | 7 | **4/7 (57.1%)** | 2/7 | `morluto/rea`: 89.0% | Majority shared. Both P2 established-active slots duplicate repositories already encountered in other categories. All seven describe AI/agent-related development tooling. |
| `obsidian-pkm` | 4 | 0/4 | **2/4 (50%)** | `joplin`: 71.8% | Both established-active slots are large, older repositories: Joplin (created 2017) and the Obsidian release registry (2020). Recent slots are a retrieval integration and a student assistant. |
| `local-ai` | 4 | 0/4 | 0/4 | `Rapid-MLX`: 55.8% | No giant domination; one of four results, and half the established-active slots, is a curated list. |

Across the dataset, **30 category memberships represent 25 repositories**. Four IDs appear in multiple categories: `ai-sdlc` (2), `Navide` (2), `DeepSeek-Reasonix` (2) and `rea` (3). These create five extra memberships. P2 itself had 4 repeated candidate occurrences out of 24 (16.7%); those are not four duplicated database records. The collector preserves shared provenance while deduplicating repository identities.

The strongest overlap is AI Agents/Developer Tools: **3 shared IDs**. AI Agents/MCP, Developer Tools/MCP and Coding Agents/Developer Tools each share one. P2 Developer Tools has 3 of 4 members shared elsewhere; its established-active cohort has 2 of 2. This supports reducing that category's redundant scope before raising quotas.

The four large repositories (`rea`, `DeepSeek-Reasonix`, `joplin`, `obsidian-releases`) are **16% of repositories but 96.1% of the 428,882 stored stars**, counting each ID once. This demonstrates concentration if raw stars are aggregated; it does not demonstrate biased momentum scores. `rea` and `DeepSeek-Reasonix` were created in April 2026, so their size does not make them old projects. The recipe name “established-active” only encodes a star floor and recent push, not longevity, stability or validation.

There are 16 recent-cohort repositories and 9 established-active repositories. Primary languages are TypeScript 11, Python 6, Go 3, Rust 2, Swift 1 and unknown 2. These describe this selected sample, not the wider ecosystem.

Provenance overlap understates semantic overlap. For example, `exomem` is explicitly an MCP server but has only Obsidian/PKM provenance; `edgeever` is explicitly a PKM application but has only MCP provenance. `Lody` and `lody-ios` are different IDs describing the same product family, accounting for half of Coding Agents. None of these observations authorizes rewriting stored membership.

**Effect of sorting by `updated`**

1. The executed search samples the most recently updated matching repositories, with only **two results per cohort in P2**. It does not sort by semantic relevance, novelty, growth or diversity. Every sampled repository's stored `updated_at` is less than 20 minutes before its first observation; 14 of 25 are within one minute. This is a snapshot of what was recently updated around two execution times, not a representative survey.
2. A topic qualifier establishes eligibility, not the repository's primary purpose. Recency ordering cannot distinguish a core MCP server from a product with an incidental MCP tag, or application code from a registry/list. The stored results contain all of those roles. The stored metadata does not explain which event caused each update; `updated_at` must not be equated with a substantive code change or measured development intensity.
3. Frequently updated repositories can repeatedly occupy the small first-page quotas, and multi-tag repositories can appear near the front of several searches. This is a sampling risk supported by the observed overlap, not a measured causal attribution to automation, bots or particular update events. Saved metadata does not contain the full search universe or excluded results.
4. `recent` means created within a year and 10–999 stars, with recency ordering; it does not mean growing or necessarily high quality. `established-active` means 1,000+ stars and a push within 30 days, with no upper star cap and no minimum age. Giant and moderately popular projects compete for the same two slots. Older projects below 1,000 stars, and all projects below 10 stars, are excluded by these recipes regardless of relevance.
5. Duplicate IDs consume candidate slots; the existing implementation does not refill a cohort until it has two new unique repositories. This explains why 24 P2 slots yielded 20 additions. It is documented sampling behavior, not a reason to change collection code in this audit. Both runs completed; the receipts do not show budget truncation as the cause of the observed composition.
6. One observation per repository cannot establish star growth, momentum, sustained activity or the stability of category composition over time. Switching to `stars` would favor popularity directly; it would not solve repository-type relevance. `best-match` is an existing configurable option worth comparing in a future approved pilot, but its quality cannot be inferred from this dataset.

The local implementation evidence is in [search parameter forwarding](../src/github_radar/github/repositories.py), [cohort planning](../src/github_radar/discovery/planning.py) and [candidate handling/provenance](../src/github_radar/services/collection.py). These files were read only.

**Catalog recommendations — proposals only**

The smallest useful next step is to clarify category definitions and manually review repository roles before another collection run. Existing records and provenance should remain historical evidence. The following changes are justified for consideration; none was applied or validated against live search.

| Priority | Proposed catalog change | Stored evidence and expected benefit | Tradeoff / limit |
| --- | --- | --- | --- |
| 1 | Define whether each category includes applications, frameworks, supporting infrastructure, plugins and resource repositories. Keep registry/list results in a separately identified resource track if software discovery is the intent. | `obsidian-releases` and `awesome-local-llm` are clearly relevant resources but represent different repository roles from software implementations. | Resource repositories remain useful; blanket removal would lose ecosystem context. Manual review can implement the distinction without a new algorithm. |
| 1 | Clarify `coding-agents` as either agent implementations or “coding-agent tools and companions”; separate companion/client results in the review if implementations are the goal. | `lody-ios`, `Lody` and `Bamboo-agent` fill 3/4 slots with a client, collaboration product and general runtime. | Their relevance is real under the broader definition. Rejecting them solely for not being an engine would require that narrower intent first. |
| 1 | Separate core MCP servers/SDKs from MCP-enabled applications, or explicitly name the catalog scope “MCP ecosystem.” Consider a distinct `topic:mcp-server` query for a future reviewed plan. | `ansvisor` and `rea` lack descriptive MCP evidence; `gdharness` and `edgeever` explicitly mention it. `exomem`, discovered elsewhere, is explicitly an MCP server. | `mcp-server` excludes clients/SDKs and is still self-described metadata. It is a narrower sampling aid, not proof of implementation. |
| 1 | Split or rename `obsidian-pkm`: use “Obsidian ecosystem” for `topic:obsidian`; use a separate broader PKM track for applications and alternatives. Candidate additional selectors include `topic:personal-knowledge-base`, `topic:note-taking`, `topic:obsidian-alternative`. | Joplin is strong broad PKM; the release registry is Obsidian infrastructure; `edgeever` is explicitly an alternative yet has no exact `obsidian` topic. | Broader selectors increase overlap and may find unrelated note-taking projects. Review candidates rather than retroactively changing category membership. |
| 2 | Rename `local-ai` to “Local LLM ecosystem” if retaining only `topic:local-llm`; separate inference software, management apps, local-capable frameworks and resource lists in review. | Current sample includes Rapid-MLX, Strata Manager, Captain Claw and a curated list. The selector does not cover all local AI modalities. | Do not infer offline operation from `local-first`, `self-hosted` or `local-friendly`. Broader local-AI coverage needs a separately justified catalog expansion. |
| 2 | Treat `developer-tools` as an overlapping umbrella or narrow it into concrete tool areas, rather than allocating another undifferentiated expansion quota. | 4/7 memberships are shared; both P2 established-active results were already found elsewhere. All seven current results concern AI/agent development. | The sample does not identify which non-AI tool areas users most want. No arbitrary exclusion of legitimate multi-category projects is justified. |
| 2 | Review singular/plural topic coverage in separate queries where warranted, for example `coding-agent` and `coding-agents`. | Navide and Rapid-MLX have plural `coding-agents`; the catalog searches singular `coding-agent`. Other relevant descriptions also occur outside recorded category membership. | Topic variants do not establish equivalence or suitability. Multiple queries can duplicate IDs and require an explicit candidate/request budget. |
| 2 | Split the existing established recipe into a middle band, such as 1,000–9,999 stars, and a separately limited 10,000+ reference band; label them by size rather than maturity. | Four large repositories dominate star mass; the current `stars:>=1000` cohort has no upper bound. Existing recipe configuration already supports `max_stars`. | The cutoff is a proposed sampling policy, not a relevance rule. Preserve useful giant references; revise total slot/request budgets rather than silently adding cohorts. |
| 3 | For a later approved pilot, compare a limited `best-match` cohort with `updated`, using a fixed candidate budget and the same manual type/relevance rubric. Consider an older-small-project cohort only if that gap matters to the intended audience. | Current results are concentrated within minutes of collection, and the two recipes omit older sub-1,000-star projects. | No evidence here establishes that another sort will improve relevance. Additional recipes can increase overlap/cost; do not execute them automatically. |

Do not silently exclude `ansvisor`, `rea`, Reminal or any other ambiguous result based only on this metadata. Record the uncertainty and resolve it through a separately authorized source review if their precise capabilities become important. No scoring change or automatic reclassification is recommended from this 25-repository sample.

**Stored metadata appendix**

The following entries reproduce every stored description and complete topic list, with stable identity, primary language, stars, observation time, creation/update/push times and provenance. They are included so the assessments can be reviewed without querying GitHub. Run labels and query suffixes above resolve every provenance entry to its exact query and saved execution plan.

**`100yenadmin/hermes-teammates`**

- GitHub ID: `1405349254`; local repository ID: `7`.
- Stored description: Named subagent teammates for Hermes Agent: an operator-defined roster, context-carried follow-ups, live steering and a Kanban teammates lane. Stock Hermes; no patched core.
- Complete stored topics: `ai-agents`, `hermes-agent`, `hermes-plugin`, `kanban`, `multi-agent`, `subagents`.
- Primary language: **Python**; stars: **79**, observed `2026-10-17 09:41:28.255603+00:00`.
- Created `2026-10-05 07:41:23+00:00`; updated `2026-10-17 09:41:10+00:00`; pushed `2026-10-17 09:41:05+00:00`.
- First tracked `2026-10-17 09:41:28.255603+00:00`; last seen `2026-10-17 09:41:28.255603+00:00`.
- Provenance: `ai-agents` / `recent` / P2; first recorded `2026-10-17 09:41:28.588627+00:00`, last recorded `2026-10-17 09:41:28.588627+00:00`.

**`Artexis10/exomem`**

- GitHub ID: `1242670581`; local repository ID: `13`.
- Stored description: Self-hosted MCP server that makes your Obsidian/markdown vault searchable — text, PDFs, Office docs, images, audio — from any MCP client. Hybrid retrieval over a typed, governed corpus, sub-second at 50k notes; your files stay plain markdown.
- Complete stored topics: `agent-memory`, `ai-agents`, `claude`, `hybrid-search`, `knowledge-base`, `markdown`, `mcp`, `mcp-server`, `obsidian`, `rag`, `self-hosted`, `semantic-search`.
- Primary language: **Python**; stars: **10**, observed `2026-10-17 09:41:33.068208+00:00`.
- Created `2026-05-18 16:34:03+00:00`; updated `2026-10-17 09:36:28+00:00`; pushed `2026-10-17 09:37:23+00:00`.
- First tracked `2026-10-17 09:41:33.068208+00:00`; last seen `2026-10-17 09:41:33.068208+00:00`.
- Provenance: `obsidian-pkm` / `recent` / P2; first recorded `2026-10-17 09:41:33.487267+00:00`, last recorded `2026-10-17 09:41:33.487267+00:00`.

**`Aureliolo/gdharness`**

- GitHub ID: `1361899509`; local repository ID: `11`.
- Stored description: A harness for driving a Godot 4 project from an agent: editor addons, a runtime bridge into the running game, an MCP server in front of them, and a CLI that installs and diagnoses the Godot side.
- Complete stored topics: `gamedev`, `gdscript`, `godot`, `godot-engine`, `godot4`, `mcp`, `mcp-server`, `model-context-protocol`.
- Primary language: **TypeScript**; stars: **25**, observed `2026-10-17 09:41:30.540521+00:00`.
- Created `2026-09-08 20:59:23+00:00`; updated `2026-10-17 09:41:10+00:00`; pushed `2026-10-17 09:41:07+00:00`.
- First tracked `2026-10-17 09:41:30.540521+00:00`; last seen `2026-10-17 09:41:30.540521+00:00`.
- Provenance: `mcp` / `recent` / P2; first recorded `2026-10-17 09:41:31.128342+00:00`, last recorded `2026-10-17 09:41:31.128342+00:00`.

**`Health-525/courseraptor`**

- GitHub ID: `1345039516`; local repository ID: `14`.
- Stored description: 大学生的开源 AI 学习助手：导入课表，一句话查课、记待办、整理知识、生成文档。支持网页、QQ、Obsidian，本地优先，自选模型。
- Complete stored topics: `ai-agent`, `course-schedule`, `llm`, `local-first`, `obsidian`, `qq-bot`, `student-tools`, `timetable`, `typescript`, `vercel-ai-sdk`.
- Primary language: **TypeScript**; stars: **36**, observed `2026-10-17 09:41:33.501439+00:00`.
- Created `2026-08-24 14:18:53+00:00`; updated `2026-10-17 09:22:12+00:00`; pushed `2026-10-17 09:22:30+00:00`.
- First tracked `2026-10-17 09:41:33.501439+00:00`; last seen `2026-10-17 09:41:33.501439+00:00`.
- Provenance: `obsidian-pkm` / `recent` / P2; first recorded `2026-10-17 09:41:34.200755+00:00`, last recorded `2026-10-17 09:41:34.200755+00:00`.

**`Innei/lody-ios`**

- GitHub ID: `1358465489`; local repository ID: `8`.
- Stored description: Independent iOS client for Lody — native chat, sessions, and coding-agent collaboration on iPhone
- Complete stored topics: `agent`, `coding-agent`, `collaboration`, `expo`, `ios`, `iphone`, `local-first`, `react-native`, `swift`.
- Primary language: **Swift**; stars: **519**, observed `2026-10-17 09:41:28.994401+00:00`.
- Created `2026-09-05 18:37:43+00:00`; updated `2026-10-17 09:41:23+00:00`; pushed `2026-10-17 09:41:19+00:00`.
- First tracked `2026-10-17 09:41:28.994401+00:00`; last seen `2026-10-17 09:41:28.994401+00:00`.
- Provenance: `coding-agents` / `recent` / P2; first recorded `2026-10-17 09:41:29.316741+00:00`, last recorded `2026-10-17 09:41:29.316741+00:00`.

**`LodyAI/Lody`**

- GitHub ID: `1326099407`; local repository ID: `20`.
- Stored description: Share coding agents with your team on phone and desktop
- Complete stored topics: `agent`, `agentic-workflow`, `coding-agent`, `collaboration`, `local-first`, `multi-agent`, `multiplayer`, `orchestration`, `vibe-coding`.
- Primary language: **TypeScript**; stars: **1,424**, observed `2026-10-17 09:41:37.992893+00:00`.
- Created `2026-08-07 02:09:16+00:00`; updated `2026-10-17 09:30:01+00:00`; pushed `2026-10-17 09:32:30+00:00`.
- First tracked `2026-10-17 09:41:37.992893+00:00`; last seen `2026-10-17 09:41:37.992893+00:00`.
- Provenance: `coding-agents` / `established-active` / P2; first recorded `2026-10-17 09:41:38.349475+00:00`, last recorded `2026-10-17 09:41:38.349475+00:00`.

**`Navide-dev/Navide`**

- GitHub ID: `1252390722`; local repository ID: `6`.
- Stored description: Navide is an open-source Agentic Development Environment (ADE) that orchestrates AI software teams across the software development lifecycle. Coordinate AI agents, workflows, context, and human collaboration from idea to deployment.
- Complete stored topics: `agentic-development-environment`, `ai-agents`, `claude-code`, `codex`, `coding-agents`, `developer-tools`, `electron`, `multi-agent`, `open-source`, `typescript`.
- Primary language: **TypeScript**; stars: **36**, observed `2026-10-17 09:41:27.665848+00:00`.
- Created `2026-05-28 13:26:03+00:00`; updated `2026-10-17 09:41:18+00:00`; pushed `2026-10-17 09:41:15+00:00`.
- First tracked `2026-10-17 09:41:27.665848+00:00`; last seen `2026-10-17 09:41:27.665848+00:00`.
- Provenance: `ai-agents` / `recent` / P2; first recorded `2026-10-17 09:41:28.243955+00:00`, last recorded `2026-10-17 09:41:28.243955+00:00`.
- Provenance: `developer-tools` / `recent` / P2; first recorded `2026-10-17 09:41:31.645947+00:00`, last recorded `2026-10-17 09:41:31.645947+00:00`.

**`ai-sdlc-framework/ai-sdlc`**

- GitHub ID: `1152413600`; local repository ID: `1`.
- Stored description: Declarative governance framework for AI-augmented software development lifecycles
- Complete stored topics: `ai-agents`, `ai-coding-agents`, `ai-governance`, `developer-tools`, `devops`, `golang`, `llm-ops`, `open-source`, `orchestration`, `progressive-autonomy`, `python`, `quality-gates`, `sdlc`, `typescript`.
- Primary language: **TypeScript**; stars: **414**, observed `2026-10-16 17:47:19.375198+00:00`.
- Created `2026-02-07 20:54:12+00:00`; updated `2026-10-16 17:47:10+00:00`; pushed `2026-10-16 17:47:06+00:00`.
- First tracked `2026-10-16 17:47:19.375198+00:00`; last seen `2026-10-16 17:47:19.375198+00:00`.
- Provenance: `ai-agents` / `recent` / P1; first recorded `2026-10-16 17:47:19.864811+00:00`, last recorded `2026-10-16 17:47:19.864811+00:00`.
- Provenance: `developer-tools` / `recent` / P1; first recorded `2026-10-16 17:47:21.975342+00:00`, last recorded `2026-10-16 17:47:21.975342+00:00`.

**`ansvisor/ansvisor`**

- GitHub ID: `1229754331`; local repository ID: `10`.
- Stored description: Open-source AI Search Intelligence Platform — track, analyze, and improve AI visibility, citations, prompts, competitors, and content opportunities across ChatGPT, Claude, Gemini, Google AI Overviews, Google AI Mode, Perplexity, Grok, and Microsoft Copilot.
- Complete stored topics: `aeo`, `ai-agent`, `ai-search`, `answer-engine-optimization`, `brand-monitoring`, `chatgpt`, `claude`, `copilot`, `docker`, `gemini`, `geo`, `google-ai-overviews`, `grok`, `mcp`, `nextjs`, `open-source`, `perplexity`, `seo`, `supabase`, `typescript`.
- Primary language: **TypeScript**; stars: **144**, observed `2026-10-17 09:41:30.126514+00:00`.
- Created `2026-05-05 10:57:46+00:00`; updated `2026-10-17 09:41:25+00:00`; pushed `2026-10-17 09:41:21+00:00`.
- First tracked `2026-10-17 09:41:30.126514+00:00`; last seen `2026-10-17 09:41:30.126514+00:00`.
- Provenance: `mcp` / `recent` / P2; first recorded `2026-10-17 09:41:30.532614+00:00`, last recorded `2026-10-17 09:41:30.532614+00:00`.

**`atoma-run/atoma`**

- GitHub ID: `1213948861`; local repository ID: `3`.
- Stored description: Watch a request turn into finished work. AI agents produce and check it
- Complete stored topics: `agent-orchestration`, `agpl`, `ai-agents`, `cost-optimization`, `jev`, `llm-agents`, `mcp`, `sandbox`, `typesafe`, `typescript`.
- Primary language: **TypeScript**; stars: **20**, observed `2026-10-16 17:47:20.401668+00:00`.
- Created `2026-04-18 00:01:45+00:00`; updated `2026-10-16 17:47:05+00:00`; pushed `2026-10-16 17:47:01+00:00`.
- First tracked `2026-10-16 17:47:20.401668+00:00`; last seen `2026-10-16 17:47:20.401668+00:00`.
- Provenance: `ai-agents` / `recent` / P1; first recorded `2026-10-16 17:47:20.954411+00:00`, last recorded `2026-10-16 17:47:20.954411+00:00`.

**`bigduu/Bamboo-agent`**

- GitHub ID: `1164779771`; local repository ID: `9`.
- Stored description: Bamboo — a local-first Rust agent harness and runtime with sessions, tools, skills, MCP, sub-agents, workflows, schedules, and context assembly over HTTP, SSE, and WebSocket.
- Complete stored topics: `agent`, `agent-framework`, `agent-harness`, `agent-runtime`, `agentic-ai`, `ai`, `ai-agents`, `anthropic`, `autonomous-agents`, `claude`, `coding-agent`, `llm`, `llm-agent`, `local-first`, `mcp`, `rust`, `sse`, `workflow`.
- Primary language: **Rust**; stars: **21**, observed `2026-10-17 09:41:29.324916+00:00`.
- Created `2026-02-23 13:29:34+00:00`; updated `2026-10-17 09:40:57+00:00`; pushed `2026-10-17 09:40:52+00:00`.
- First tracked `2026-10-17 09:41:29.324916+00:00`; last seen `2026-10-17 09:41:29.324916+00:00`.
- Provenance: `coding-agents` / `recent` / P2; first recorded `2026-10-17 09:41:29.678999+00:00`, last recorded `2026-10-17 09:41:29.678999+00:00`.

**`esengine/DeepSeek-Reasonix`**

- GitHub ID: `1216785679`; local repository ID: `19`.
- Stored description: A reliable coding agent for complex software engineering tasks.
- Complete stored topics: `agent`, `agent-framework`, `ai-agent`, `ai-coding`, `cli`, `coding-agent`, `deepseek`, `developer-tools`, `dsh`, `dsh-plugin`, `ink`, `llm`, `prompt-caching`, `r1`, `terminal`, `tool-use`, `tui`, `typescript`.
- Primary language: **Go**; stars: **35,823**, observed `2026-10-17 09:41:37.480623+00:00`.
- Created `2026-04-21 08:27:02+00:00`; updated `2026-10-17 09:39:43+00:00`; pushed `2026-10-17 09:39:38+00:00`.
- First tracked `2026-10-17 09:41:37.480623+00:00`; last seen `2026-10-17 09:41:37.480623+00:00`.
- Provenance: `coding-agents` / `established-active` / P2; first recorded `2026-10-17 09:41:37.977764+00:00`, last recorded `2026-10-17 09:41:37.977764+00:00`.
- Provenance: `developer-tools` / `established-active` / P2; first recorded `2026-10-17 09:41:39.739241+00:00`, last recorded `2026-10-17 09:41:39.739241+00:00`.

**`harshalgajjar/Reminal`**

- GitHub ID: `1274006240`; local repository ID: `2`.
- Stored description: Close the lid and walk away — every window, desktop and terminal on your machines, live in any browser. E2E-encrypted, no open ports, no client, no account.
- Complete stored topics: `ai-agents`, `claude-code`, `cli`, `codex`, `end-to-end-encryption`, `golang`, `headless`, `linux`, `macos`, `mcp`, `remote-access`, `remote-desktop`, `screen-sharing`, `self-hosted`, `ssh`, `terminal`, `tmux`, `webrtc`, `windows`.
- Primary language: **Go**; stars: **323**, observed `2026-10-16 17:47:19.876955+00:00`.
- Created `2026-06-19 05:08:18+00:00`; updated `2026-10-16 17:47:07+00:00`; pushed `2026-10-16 17:47:01+00:00`.
- First tracked `2026-10-16 17:47:19.876955+00:00`; last seen `2026-10-16 17:47:19.876955+00:00`.
- Provenance: `ai-agents` / `recent` / P1; first recorded `2026-10-16 17:47:20.394085+00:00`, last recorded `2026-10-16 17:47:20.394085+00:00`.

**`hivecommons/hive`**

- GitHub ID: `1213602620`; local repository ID: `12`.
- Stored description: AI agent orchestration for open and closed source — a fully customizable fleet of AI agents covering every level of project maintenance, from brainstorming to full autonomy
- Complete stored topics: `agent-orchestration`, `ai-agents`, `autonomous-agents`, `ci-cd`, `code-review`, `developer-tools`, `devops`, `github-automation`, `golang`, `hacktoberfest`, `kubernetes`, `llm`, `multi-agent-systems`.
- Primary language: **Go**; stars: **67**, observed `2026-10-17 09:41:31.654583+00:00`.
- Created `2026-04-17 15:00:19+00:00`; updated `2026-10-17 09:40:48+00:00`; pushed `2026-10-17 09:40:46+00:00`.
- First tracked `2026-10-17 09:41:31.654583+00:00`; last seen `2026-10-17 09:41:31.654583+00:00`.
- Provenance: `developer-tools` / `recent` / P2; first recorded `2026-10-17 09:41:32.363033+00:00`, last recorded `2026-10-17 09:41:32.363033+00:00`.

**`hongfaqiu/strata-manager`**

- GitHub ID: `1413208143`; local repository ID: `15`.
- Stored description: Strata Manager makes Niko1221/Strata, the local LLM inference engine, easier to set up and use. Manage local models and services, monitor hardware and API usage, chat and tune inference. Open-source desktop app for Windows / Linux x64.
- Complete stored topics: `llm-inference`, `local-llm`, `model-management`, `model-monitoring`, `qwen3-8-flash-next`, `strata`.
- Primary language: **Rust**; stars: **16**, observed `2026-10-17 09:41:34.624598+00:00`.
- Created `2026-10-10 13:16:52+00:00`; updated `2026-10-17 09:39:33+00:00`; pushed `2026-10-17 09:39:28+00:00`.
- First tracked `2026-10-17 09:41:34.624598+00:00`; last seen `2026-10-17 09:41:34.624598+00:00`.
- Provenance: `local-ai` / `recent` / P2; first recorded `2026-10-17 09:41:35.299712+00:00`, last recorded `2026-10-17 09:41:35.299712+00:00`.

**`kstevica/captain-claw`**

- GitHub ID: `1160004652`; local repository ID: `16`.
- Stored description: Self-hosted framework for orchestrating fleets of specialist AI agents — ensemble reasoning and a full agentic coding pipeline, model-agnostic and local-friendly.
- Complete stored topics: `agent-orchestration`, `agentic-coding`, `agentic-workflow`, `ai-agent`, `ai-agents`, `anthropic`, `autonomous-agents`, `claw`, `cli`, `code-review`, `langchain-alternative`, `llm`, `llm-inference`, `local-llm`, `multi-agent`, `multiagent`, `ollama`, `openai`, `python`, `self-hosted`.
- Primary language: **Python**; stars: **169**, observed `2026-10-17 09:41:35.311495+00:00`.
- Created `2026-02-17 12:33:42+00:00`; updated `2026-10-17 09:35:15+00:00`; pushed `2026-10-17 09:35:10+00:00`.
- First tracked `2026-10-17 09:41:35.311495+00:00`; last seen `2026-10-17 09:41:35.311495+00:00`.
- Provenance: `local-ai` / `recent` / P2; first recorded `2026-10-17 09:41:35.706252+00:00`, last recorded `2026-10-17 09:41:35.706252+00:00`.

**`laurent22/joplin`**

- GitHub ID: `79162682`; local repository ID: `23`.
- Stored description: Joplin - the privacy-focused note taking app with sync capabilities for Windows, macOS, Linux, Android and iOS.
- Complete stored topics: `android`, `dropbox`, `electron`, `enex-files`, `evernote`, `javascript`, `joplin`, `nextcloud`, `nodejs`, `note-taking`, `notesnook`, `obsidian`, `onedrive`, `react-native`, `standardnotes`, `synchronisation`, `web-clipper`, `webdav`.
- Primary language: **TypeScript**; stars: **56,766**, observed `2026-10-17 09:41:40.980704+00:00`.
- Created `2017-01-16 21:49:41+00:00`; updated `2026-10-17 09:30:36+00:00`; pushed `2026-10-17 08:23:54+00:00`.
- First tracked `2026-10-17 09:41:40.980704+00:00`; last seen `2026-10-17 09:41:40.980704+00:00`.
- Provenance: `obsidian-pkm` / `established-active` / P2; first recorded `2026-10-17 09:41:41.397494+00:00`, last recorded `2026-10-17 09:41:41.397494+00:00`.

**`morluto/rea`**

- GitHub ID: `1209966933`; local repository ID: `17`.
- Stored description: Reverse engineer anything with agents, from app behavior down to native binaries.
- Complete stored topics: `agent-skills`, `ai-agents`, `binary-analysis`, `claude-code`, `cli`, `codex`, `cordis`, `decompiler`, `developer-tools`, `dsh`, `dsh-plugin`, `ghidra`, `hopper`, `llm`, `mcp`, `model-context-protocol`, `rea`, `reverse-engineering`, `static-analysis`, `touhou`.
- Primary language: **TypeScript**; stars: **297,122**, observed `2026-10-17 09:41:36.196329+00:00`.
- Created `2026-04-14 00:51:32+00:00`; updated `2026-10-17 09:41:25+00:00`; pushed `2026-10-17 09:41:15+00:00`.
- First tracked `2026-10-17 09:41:36.196329+00:00`; last seen `2026-10-17 09:41:36.196329+00:00`.
- Provenance: `ai-agents` / `established-active` / P2; first recorded `2026-10-17 09:41:36.577215+00:00`, last recorded `2026-10-17 09:41:36.577215+00:00`.
- Provenance: `developer-tools` / `established-active` / P2; first recorded `2026-10-17 09:41:39.728391+00:00`, last recorded `2026-10-17 09:41:39.728391+00:00`.
- Provenance: `mcp` / `established-active` / P2; first recorded `2026-10-17 09:41:38.751625+00:00`, last recorded `2026-10-17 09:41:38.751625+00:00`.

**`obsidianmd/obsidian-releases`**

- GitHub ID: `262342594`; local repository ID: `22`.
- Stored description: Community plugins list, theme list, and releases of Obsidian.
- Complete stored topics: `bases`, `jsoncanvas`, `markdown`, `md`, `obsidian`, `obsidian-md`.
- Primary language: **unknown (NULL)**; stars: **22,299**, observed `2026-10-17 09:41:40.435481+00:00`.
- Created `2020-05-08 14:18:27+00:00`; updated `2026-10-17 09:36:53+00:00`; pushed `2026-10-17 09:26:29+00:00`.
- First tracked `2026-10-17 09:41:40.435481+00:00`; last seen `2026-10-17 09:41:40.435481+00:00`.
- Provenance: `obsidian-pkm` / `established-active` / P2; first recorded `2026-10-17 09:41:40.966480+00:00`, last recorded `2026-10-17 09:41:40.966480+00:00`.

**`pipeshub-ai/pipeshub-ai`**

- GitHub ID: `944080744`; local repository ID: `18`.
- Stored description: The open-source context layer for AI agents. PipesHub turns your company's knowledge (Slack, Drive, Jira, GitHub, Microsoft 365 and 40+ connectors) into a permission-aware workspace that agents can search, grep, navigate and cite. MCP, SDKs and built-in agents. Self-hosted.
- Complete stored topics: `agentic-rag`, `ai`, `ai-agents`, `claude-code`, `context-engineering`, `context-layer`, `deep-research`, `enterprise-search`, `glean`, `graphrag`, `hybrid-search`, `knowledge-graph`, `llm`, `mcp`, `mcp-server`, `ollama`, `python`, `rag`, `self-hosted`, `workplace-ai`.
- Primary language: **Python**; stars: **3,837**, observed `2026-10-17 09:41:36.586225+00:00`.
- Created `2025-03-06 18:51:10+00:00`; updated `2026-10-17 09:40:08+00:00`; pushed `2026-10-17 09:40:03+00:00`.
- First tracked `2026-10-17 09:41:36.586225+00:00`; last seen `2026-10-17 09:41:36.586225+00:00`.
- Provenance: `ai-agents` / `established-active` / P2; first recorded `2026-10-17 09:41:36.991870+00:00`, last recorded `2026-10-17 09:41:36.991870+00:00`.

**`rafska/awesome-local-llm`**

- GitHub ID: `995323854`; local repository ID: `24`.
- Stored description: A curated list of awesome platforms, tools, practices and resources that helps run LLMs locally
- Complete stored topics: `ai`, `awesome`, `awesome-list`, `llm`, `local`, `local-ai`, `local-llm`, `resources`, `self-hosted`, `selfhosted`.
- Primary language: **unknown (NULL)**; stars: **2,979**, observed `2026-10-17 09:41:42.078204+00:00`.
- Created `2025-06-03 10:01:04+00:00`; updated `2026-10-17 09:34:01+00:00`; pushed `2026-10-17 09:33:57+00:00`.
- First tracked `2026-10-17 09:41:42.078204+00:00`; last seen `2026-10-17 09:41:42.078204+00:00`.
- Provenance: `local-ai` / `established-active` / P2; first recorded `2026-10-17 09:41:42.631466+00:00`, last recorded `2026-10-17 09:41:42.631466+00:00`.

**`raullenchai/Rapid-MLX`**

- GitHub ID: `1166182351`; local repository ID: `25`.
- Stored description: Rapid-MLX is an open-source (Apache 2.0) OpenAI- and Anthropic-compatible LLM inference server for Apple Silicon, built on MLX, focused on reliable tool calling for coding agents. Up to 4× faster than Apple's MLX (mlx-lm) on the same weights.
- Complete stored topics: `anthropic-api`, `apple-silicon`, `claude-code`, `coding-agents`, `continuous-batching`, `inference-server`, `llm-inference`, `llm-server`, `lm-studio-alternative`, `local-ai`, `local-llm`, `macos`, `mcp`, `mlx`, `mlx-lm`, `ollama-alternative`, `omlx-alternative`, `openai-compatible`, `speculative-decoding`, `tool-calling`.
- Primary language: **Python**; stars: **3,987**, observed `2026-10-17 09:41:42.638573+00:00`.
- Created `2026-02-25 00:41:44+00:00`; updated `2026-10-17 09:25:43+00:00`; pushed `2026-10-17 09:25:40+00:00`.
- First tracked `2026-10-17 09:41:42.638573+00:00`; last seen `2026-10-17 09:41:42.638573+00:00`.
- Provenance: `local-ai` / `established-active` / P2; first recorded `2026-10-17 09:41:43.024462+00:00`, last recorded `2026-10-17 09:41:43.024462+00:00`.

**`smithersai/smithers`**

- GitHub ID: `1128579105`; local repository ID: `5`.
- Stored description: Smithers is an agentic workflow framework for defining workflows in simple TypeScript configuration files and executing them quickly, durably, and reliably
- Complete stored topics: `agent-orchestration`, `ai-agents`, `background-agents`, `claude-code`, `codex`, `developer-tools`, `durable-execution`, `human-in-the-loop`, `llm`, `multi-agent`, `react`, `typescript`, `workflow-engine`.
- Primary language: **TypeScript**; stars: **439**, observed `2026-10-16 17:47:21.982558+00:00`.
- Created `2026-01-05 21:13:36+00:00`; updated `2026-10-16 17:46:39+00:00`; pushed `2026-10-16 17:46:34+00:00`.
- First tracked `2026-10-16 17:47:21.982558+00:00`; last seen `2026-10-16 17:47:21.982558+00:00`.
- Provenance: `developer-tools` / `recent` / P1; first recorded `2026-10-16 17:47:22.415886+00:00`, last recorded `2026-10-16 17:47:22.415886+00:00`.

**`tianma-if/edgeever`**

- GitHub ID: `1281033762`; local repository ID: `21`.
- Stored description: AI-native knowledge base & Evernote/Obsidian alternative with instant sync & native MCP. Free on Cloudflare or self-hosted via Docker.
- Complete stored topics: `ai-agent`, `cloudflare`, `cloudflare-d1`, `cloudflare-r2`, `cloudflare-workers`, `docker`, `electron`, `evernote`, `evernote-alternative`, `knowledge-base`, `mcp`, `note-taking`, `obsidian-alternative`, `offline-first`, `personal-knowledge-base`, `pwa`, `self-hosted`, `sqlite`, `tiptap`, `web-clipper`.
- Primary language: **TypeScript**; stars: **2,314**, observed `2026-10-17 09:41:38.760528+00:00`.
- Created `2026-06-26 06:59:58+00:00`; updated `2026-10-17 09:41:19+00:00`; pushed `2026-10-17 09:41:12+00:00`.
- First tracked `2026-10-17 09:41:38.760528+00:00`; last seen `2026-10-17 09:41:38.760528+00:00`.
- Provenance: `mcp` / `established-active` / P2; first recorded `2026-10-17 09:41:39.316351+00:00`, last recorded `2026-10-17 09:41:39.316351+00:00`.

**`vibheksoni/session-export`**

- GitHub ID: `1298568201`; local repository ID: `4`.
- Stored description: Convert AI coding CLI sessions between Codex, Claude Code, Pi, OpenCode, Devin, and Factory. SDK, CLI, MCP chat recall, and trace export for fine-tuning.
- Complete stored topics: `agent-memory`, `ai-cli`, `ai-coding`, `chat-history`, `claude-code`, `cli-tool`, `codex-cli`, `coding-agent`, `conversation-history`, `developer-tools`, `devin`, `mcp-server`, `model-context-protocol`, `opencode`, `pi-coding-agent`, `python-sdk`, `session-converter`, `session-export`, `session-migration`, `session-search`.
- Primary language: **Python**; stars: **13**, observed `2026-10-16 17:47:21.471812+00:00`.
- Created `2026-07-12 19:32:29+00:00`; updated `2026-10-16 17:47:11+00:00`; pushed `2026-10-16 17:47:07+00:00`.
- First tracked `2026-10-16 17:47:21.471812+00:00`; last seen `2026-10-16 17:47:21.471812+00:00`.
- Provenance: `developer-tools` / `recent` / P1; first recorded `2026-10-16 17:47:21.957605+00:00`, last recorded `2026-10-16 17:47:21.957605+00:00`.
