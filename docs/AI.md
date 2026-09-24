# AI synthesis layer (Phase 5)

The `ai` command group turns the deterministic Phase 2–4 analytics into a short,
coherent **narrative** about a repository, topic, developer, ecosystem or
bridge pair. It is, by construction, a *narration* layer: the LLM never
measures, counts, scores, ranks, or invents anything. Every number, label and
finding the model writes must trace back to the allowlisted evidence
(Phases 2–4) it is given.

Two guarantees underpin the whole design and are the reason nothing here is
decorative:

1. **Deterministic, fingerprint-addressable evidence.** Each artifact's prompt
   is built from a canonical, allowlisted evidence bundle whose ids (`E1`,
   `E2`, ...) and sha256 *fingerprint* depend only on the underlying measured
   facts. Rebuilding the same evidence in any order produces the same ids and
   the same fingerprint.
2. **Cached, confidence-labelled results.** The synthesized artifact is keyed by
   (entity, artifact type, window, evidence fingerprint, prompt version, model)
   and replayed until the evidence actually changes. Its `confidence`
   (`LOW` / `MEDIUM` / `HIGH`) is derived from the same deterministic
   data-coverage confidence Phases 2–4 already compute — never from a model
   self-assessment.

Since the model reads only allowlisted fields and untrusted repository text is
structurally delimited, the layer adds *explanation* without adding *judgement*.

## Data flow

```
snapshot history → analytics (Phases 2–4) → evidence builders (ai/evidence.py)
  → EvidenceBundle (stable E1.. ids + fingerprint)
  → cache lookup (SqlArtifactStore, keyed by fingerprint+version+model)
       hit → replay artifact (no model call)
       miss → build system+user prompt → provider.generate_structured()
       → validate exactly once (bounded repair)
       → persist AIArtifact + source evidence snapshot
```

`ai status` shows the configured provider, limits and the number of artifacts
currently cached per artifact type.

## The evidence allowlist

`ai/evidence.py` renders **only** explicit fields:

- *repository*: metadata, latest observed counters, window deltas, momentum,
  trend, confidence, topics, top contributors, snapshot history, plus (wrapped)
  README / latest releases / latest commits;
- *topic*: aggregate repository count, momentum, coverage confidence, the top
  contributing repositories;
- *developer*: login, display name, follower count, tracked-since, associated
  repositories, observed activity, ecosystem score, emerging classification,
  topic relevance, graph footprint — **never** e-mails, blogs, twitter handles,
  company, location or bio (contact/PII excluded by construction);
- *ecosystem*: graph scale, connectivity, hubs, top developer bridges;
- *bridge*: the bridge aggregate and per-bridge evidence between two topics.

Untrusted repository text (README, releases, commit messages) is normalized,
truncated and wrapped in `<untrusted-data> kind=…</untrusted-data>` so hostile
content cannot masquerade as instructions. A final `redact_secrets` pass scrubs
any occurrence of the configured credentials from rendered payloads.

## Caching and invalidation

Artifacts live in the `ai_artifacts` table (migration `0004`), unique on
`(entity_type, entity_key, artifact_type, window_days, source_fingerprint,
prompt_version, model)`.

- Re-running `ai repo acme/widget` with unchanged evidence is a **cache hit** —
  no model call, no spend.
- Changing a star count, a window, the prompt version or the model changes the
  key, so a stale-looking summary is never served as current.
- `--force` bypasses the lookup and re-synthesizes (replacing the artifact at
  the same key).
- A provider failure, validation failure or repair failure is **never**
  persisted; `--force` on a failed request leaves the previous artifact intact.
- Old artifacts are never deleted; `ai status` reports how many exist per type.

## Deterministic confidence

`ai/confidence.py` maps the deterministic coverage signals to the artifact's
`confidence`:

- repository → `repo_window_confidence` (snapshot count, span, base gap);
- topic → `TopicAggregate.confidence_level`;
- developer → strongest of the observed activity / emerging coverage levels;
- ecosystem → node-type presence (MEDIUM) escalated by the strongest bridge;
- bridge → strongest single bridge confidence (a lone well-evidenced bridge is
  never hidden behind thin ones).

## Provider behaviour

`ai/openai_compatible.py` talks to any OpenAI-compatible `chat/completions`
endpoint over httpx (no SDK). Contract:

- **retry** — only `429` (honouring `Retry-After`, capped at 120 s), `5xx` and
  transport failures; bounded attempts with exponential backoff;
- **fail fast** — `401` / `403` / `400` / `422` and other 4xx raise
  `AIProviderError` without retrying;
- **repair** — exactly ONE bounded repair attempt when the content is not valid
  JSON for the requested schema; a second failure raises
  `AIResponseValidationError` and nothing is persisted;
- **tokens** — `usage.prompt_tokens` / `usage.completion_tokens` are captured
  when the provider reports them and shown as "unavailable" otherwise; never
  fabricated.

## Bounded spend

`AI_MAX_REQUESTS_PER_RUN` caps the number of model calls a single command may
make (`ai *` commands include the cache-hit rule: cached artifacts do not
consume budget). Exhaustion raises `AIRequestLimitError` and `ai repo acme/widget`
exits cleanly. Textual evidence re-reads are also bounded: README ≤ 9000 chars
(1 h TTL cache), latest 5 releases ≤ 2000 chars each, latest 20 commits ≤ 400
chars each, with `github_evidence_requests` / `evidence_cache_hits` /
`evidence_cache_misses` counters.

## CLI reference

```
github-radar ai repo acme/widget [--window 7d] [--trend] [--force] [--json]
github-radar ai topic mcp [...]
github-radar ai developer alice [...]
github-radar ai ecosystem [...]
github-radar ai bridge mcp browser-agents [...]
github-radar ai status
```

`--json` prints the full `AIArtifact` (headline, summary, grounded key points
with evidence ids, unknowns, confidence, tokens, source fingerprint) plus the
cache-hit flag — useful for piping into other tools. Human output prints the
same fields compactly with the measured-facts payload trimmed to 600 chars.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `AI_API_KEY` | Bearer token for the chat endpoint (required for `ai *`) | — |
| `AI_BASE_URL` | chat endpoint base (`{base}/chat/completions`) | — |
| `AI_MODEL` | model id (also part of the cache key) | — |
| `AI_TIMEOUT_SECONDS` | per-request timeout | `60` |
| `AI_MAX_OUTPUT_TOKENS` | `max_tokens` in the request body | `2048` |
| `AI_TEMPERATURE` | sampling temperature | `0.2` |
| `AI_MAX_REQUESTS_PER_RUN` | model-call cap per command | `20` |
| `AI_MAX_EVIDENCE_CHARS` | cap on rendered evidence per prompt | `12000` |

Non-AI commands work without any `AI_*` configuration. `ai *` raise a clear
configuration error telling you which value to add to `.env`.

## What Phase 5 does NOT do

- No agents, tool use, function calling, embeddings, vector store or retrieval;
- no scraping, outreach, notifications or schedulers — this layer only reads
  what Phases 1–4 already stored;
- no model-derived metrics, rankings or scores — those stay deterministic,
  and the prompt explicitly forbids the model from producing them;
- no personal data: developer evidence excludes every contact/PII field.