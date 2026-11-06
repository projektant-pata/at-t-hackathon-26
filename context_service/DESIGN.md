# Context Service — Design, Validation & Trade-offs

## 1. Problem framing

Engineering knowledge is scattered across code, SQL, configs, API contracts, wikis, ADRs, meeting notes, issues,
incidents, release notes and diagrams. These sources disagree with each other, and the disagreement is the point.
A useful Context Service must not "connect everything". It has to state **how much each relation can be
trusted, why, and on whose evidence**.

**Core principle: no edge without a claim, and no claim without a grounded quote.**

## 2. Architecture

```
 att-hackathon-task/ (read-only)
        │
        ▼
 ┌─────────────────────────── LAYER 1 · Agentic ETL (etl_pipeline/) ───────────────────────────┐
 │ router.py      crawl → modality + authority tier + metadata line (date / owner / status / ID) │
 │ parsers/       Tier-1/2 DETERMINISTIC: TS/JS, Python-AST, Java routes & calls; SQL migration │
 │                replay; OpenAPI; GraphQL; compose; .env; CODEOWNERS; package.json; 8 sec rules │
 │ extractors.py  LLM: prose → claims/aliases/policies · VLM: diagrams (+2nd verification pass)  │
 │                LLM code review (code + contract + governing policies)                        │
 │ grounding.py   every LLM quote must exist in the cited file (exact or fuzzy ≥ 0.9)            │
 │ resolver.py    alias table + lexical/fuzzy matching + archive scope guard                     │
 │ validator.py   deterministic cross-checks → candidates → adversarial LLM judge per cluster    │
 │                → claim statuses → edge aggregation (epistemic status + confidence)           │
 └──────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                ▼  validated_context_graph.json + etl_audit_report.md
 ┌──────────────────────── LAYER 2 · Knowledge store (knowledge_store/) ───────────────────────┐
 │ Neo4j 5.26: property graph + Lucene full-text + native vector indexes (one store, no sync) │
 │ hybrid_retriever.py: seeds (alias/record-id exact + full-text + vector, RRF)               │
 │   → 2-hop expansion → Personalized PageRank × status weight × confidence → top-30 edges     │
 │   → relations-first serialization + risk warnings + <untrusted_source> passages            │
 └──────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                ▼
 ┌──────────────────────── LAYER 3 · Delivery (delivery_gateway/) ────────────────────────────┐
 │ MCP (streamable HTTP :8765/mcp or stdio): 9 read-only tools + 2 resources → IDE agents     │
 │ FastAPI REST (:8000, /docs): same operations → humans, CI gates, dashboards               │
 └──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Everything runs in Docker: `neo4j`, `context-etl` (one-shot), `context-api` and `context-mcp`, all built from
one image.

## 3. Knowledge representation (claim-based / reified graph)

| Layer | Node | Purpose |
|---|---|---|
| World | `Entity:<NodeType>` | SERVICE, FRONTEND_APP, LIBRARY, API_ENDPOINT, DATASTORE, TABLE, COLUMN, CONFIG_KEY, DEPLOY_UNIT, TEAM, DECISION, INCIDENT, ISSUE, RELEASE, MEETING, DOCUMENT, DIAGRAM, **POLICY**, CONCEPT (fallback for out-of-schema concepts) |
| Evidence | `Claim` | One source's statement: subject –predicate→ object, **mode** (observed / asserted / proposed / deprecated / rejected), snippet, file:line, tier, date, extractor, judge status |
| Provenance | `Source` | Path, kind, authority tier, date, owner, status, record id, sha256 (drives incremental reload) |
| Derived | aggregated typed edge | `CALLS`, `EXPOSES`, `OWNED_BY`, … carrying **status** + **confidence** + `claim_ids` |
| Findings | `Risk` | category, severity, both-sided evidence, current truth, **guidance_for_codegen** |
| Semantic | `Chunk` | paragraph of prose with an embedding, linked `ABOUT` the entities claimed on those lines |

**Epistemic status of an edge** is computed from its claims and never just asserted by an LLM:

| Status | Rule |
|---|---|
| VERIFIED | at least one Tier-1 observed claim (code / SQL / deployment config / CODEOWNERS) |
| DOCUMENTED | Tier-2 contract only |
| ASSERTED | prose or diagram only. Confidence is 0.4 + 0.1 per independent file, capped at 0.7; diagram-only claims without a second-pass check are capped at 0.5 |
| PROPOSED | every claim is a plan, target state, dashed arrow, or comes from a "Proposed" ADR |
| SUPERSEDED / CONTRADICTED / REJECTED | set by schema replay (dropped or renamed columns) or by the judge (a newer decision wins) |

**Authority tiers:** 1 = runtime truth, 2 = contract, 3 = intent and history, 4 = archive. Archive material is namespaced (`archive/<project>::…`) and can never merge with live entities.

**Avoiding the "bug-as-truth" fallacy.** Code is not automatically right. When code contradicts a MUST in a contract or a governing policy, the judge and the code reviewer emit a `POLICY_VIOLATION`, where the code is wrong. That is different from `STALE_DOC`, where the doc is wrong. Examples: the idempotency contract, the last-unit rule, the logging standard.

## 4. Extraction: why hybrid

| Source | Extractor | Why |
|---|---|---|
| TS/JS/Python/Java, SQL, compose, .env, CODEOWNERS, package.json, OpenAPI, GraphQL | deterministic parsers (`parsers/`) | exact line-level evidence, zero cost, reproducible; LLMs miss indirect calls and hallucinate dependencies in code |
| Security patterns | 8 generic rules (CORS `*`, header identity with fail-open default, PII in logs, idempotency key never looked up, by-id read without ownership scope, unauthenticated mutation, GraphQL defaults, error-message leak) | high precision, explainable |
| Wiki / ADR / meeting / issue / incident / release / spec prose | LLM → claims, aliases, policies (structured output) | prose needs language understanding (modes, nicknames, supersession) |
| PNG diagrams | VLM extraction plus an **independent second VLM pass** that re-checks every arrow (present? direction? solid or dashed?) | VLMs misread arrows; dashed arrows map to `PROPOSED`; "process" diagrams produce no `CALLS` edges |
| Code vs. contract semantics | LLM code review per service, given the contract and the governing policies extracted from docs | catches off-by-one errors and contract violations that regexes cannot |

**Example.** The wiki's "Stock" and the spec's "Charge Ledger" are resolved to `service:inventory-service` and `service:payment-service`. The aliases come from LLM extraction, and each one is grounded in a quoted sentence.

## 5. Validation (built-in and measured)

**A. In-pipeline gates. These run on every ETL run.**
1. **Schema gate.** The response must be valid Pydantic structured output. The pipeline allows one repair round-trip, then drops the response.
2. **Evidence grounding.** The quote must exist in the cited file. Line numbers are recomputed from the file itself; the LLM's line numbers are never trusted. Ungrounded claims are rejected and listed in the audit report.
3. **Entity grounding.** Only Tier-1 observation can make something `VERIFIED`.
4. **Scope guard.** No alias or merge from archive material to live entities. Refused merges are logged as decoy candidates.
5. **Judge cross-check.** A risk must cite claims from at least 2 different files. Otherwise it is downgraded to `UNVERIFIED_ASSUMPTION`.
6. **Post-load integrity checks (Cypher).** No dangling claims. No risk without evidence. No `VERIFIED` edge without a Tier-1 claim.

**B. Golden set (`eval/golden.yaml`), hand-mapped from the planted problems.**
- G1–G10: risk cases. Each must cite the stale/claim side and the truth side.
- D1–D8: decoys that must not link (archive PaymentService/Inventory, the proposed SSO, rejected email matching, the HR→Finance manual CSV, Mongo/Postgres "analogues", the provider-retry theory, the independent blog).
- P1–P3: positive controls. Real hr→user and order→* calls must be `VERIFIED`; accurate docs must not be flagged.
- Q1–Q7: MCP/retrieval probes (owner, double charge, blast radius, console exception, security guidance, plan validation).

**C. Metrics and ablation.** `python -m eval.run_eval --ablation` writes `out/eval_report.md`. It rebuilds the graph three ways (deterministic only, LLM extraction without the judge, the full pipeline) and reports recall, decoy false links, positive controls and probe hit rate. This shows what each layer adds. The LLM cache makes repeat runs free.

> Overfitting caveat: the golden set was hand-built from this corpus. To mitigate this, **no file names or corpus-specific rules live in the pipeline**. Only `eval/` references them. Every rule is a generic pattern.

## 6. Context delivery

**MCP tools.** All are read-only (`read_only_hint`):

| Tool | What it returns |
|---|---|
| `query_engineering_context` | hybrid GraphRAG context |
| `get_entity` | every perspective on the entity, plus a dated timeline |
| `analyze_change_impact` | reverse traversal over impact edges, with evidence for each hop, owners and referencing docs |
| `get_contradictions` | contradictions for an entity |
| `get_security_guidance` | findings for a service or file path, plus **codegen rules**, including risks inherited from shared libraries |
| `validate_implementation_plan` | OK / WARN / REVIEW / REJECT / UNVERIFIED for each API, table, column, datastore or library the agent plans to touch. Examples: `orders.total` is rejected because it was dropped and `total_minor` should be used; `redis` is rejected because ADR-003 removed it |
| `find_dependency_path` | the dependency path between two components |
| `who_owns` | the owner, with CODEOWNERS listed first and older claims flagged |
| `list_risks` | all risks |

**MCP resources:** `context://audit/report` and `context://ontology`.

**Serialization is relations-first.** Each line looks like `[a] -CALLS (VERIFIED, T1, conf 0.94)-> [b] | evidence: file:line "…"`. Risk warnings come after the relations, then source passages.

**Prompt-injection mitigation.** Tier-3 text is sanitised (instruction-like patterns removed, angle brackets escaped). It is wrapped in `<untrusted_source path= tier= date=>`, and the server instructions tell agents to treat it as data. All tools are read-only.

**Hub safety.** Personalized PageRank, weighted by status and confidence, caps the context at 30 edges.

## 7. Trade-offs and limitations (devil's advocate)

| Decision | Benefit | Cost / risk | Mitigation / next step |
|---|---|---|---|
| Regex/AST parsers instead of full tree-sitter / CodeQL | fast, transparent, exact lines | brittle on unusual code styles; the Java split-path router is handled heuristically | swap in tree-sitter queries behind the same `Claim` contract; add Semgrep rule packs |
| LLM judge per entity cluster | catches cross-document supersession (ADR > wiki) | cost grows with the number of clusters; the judge can still be wrong | must cite ≥ 2 grounded files; Tier-1 claims cannot be overruled; deterministic candidates survive judge failure; humans review via the audit report |
| Flash-class judge (free-tier key: Pro models have zero quota) | runs at all on the available key | weaker reasoning than a Pro model on subtle conflicts | `CS_JUDGE_MODEL` is configurable; the ablation quantifies the difference |
| Single Neo4j store (graph + vectors + full-text) | no dual-index sync; one Cypher query covers seed and expansion | Community edition has no GDS, so PPR runs in Python | Enterprise or GDS for in-database PPR; Neo4j clustering for scale |
| Fixed ontology + CONCEPT fallback | deterministic tools such as blast radius | new concepts land in CONCEPT | periodic human-in-the-loop promotion of frequent CONCEPT clusters into types |
| Lexical/fuzzy entity resolution (no embedding clustering yet) | predictable; scope guard is easy to reason about | misses paraphrased names that have no alias sentence | add embedding similarity with an LLM confirmation step for merges |
| Diagram VLM + verification pass | unlocks diagram-only knowledge | two vision calls per image; raster diagrams have no text to quote | prefer SVG/diagram-as-code sources; OCR + layout model for label grounding |
| MCP delivery | works with any agent | amplifies prompt-injection risk | read-only tools, provenance tags, sanitisation, untrusted wrapping; next: signed provenance and per-tool capability scoping |

## 8. Forward view

- **Freshness.** A Git webhook or CI job re-runs the ETL. Source sha256 values make reloads incremental: only claims from changed files are replaced, and the LLM cache is keyed by content hash, so unchanged files cost nothing. Derived edges and risks are rebuilt each run so they stay consistent.
- **CI gate.** `POST /context/validate-plan` and `GET /context/security/{path}` can run on pull requests. They would fail a PR that reintroduces a dropped column or a known-insecure pattern, or that changes a component whose docs are flagged stale.
- **Doc-debt loop.** Every `STALE_DOC` or `OWNERSHIP_DRIFT` risk names the stale file, the truth file and the current truth, which is enough to auto-open a documentation PR (this would also close issue DOC-31).
- **Scale.** Shard extraction per repository and keep Neo4j as the system of record. The current `GraphRepository` interface already has two implementations (Neo4j and in-memory), so other backends can be added behind it.
- **Trust.** Track judge accuracy against human resolutions of risks, and grow the golden set from accepted and rejected findings.
