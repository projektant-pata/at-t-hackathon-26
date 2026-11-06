# Presentation script — Efficient AI Context Service

**Target:** ~11:30 spoken, inside the 10–12 min slot. The demo slide is the buffer: shorten it if time runs out.
**Judging weights:** Presentation 35 · Knowledge representation 25 · Extraction 15 · Service usage 15 · Forward view 10.
The brief says it outright: *"the most important judging criterion is … how clearly the team explains the
engineering and AI thinking … design decisions, validation approach, limitations, and how the solution could
be improved."* Every slide should answer **why**, not only **what**.

Recurring phrases. Say them on several slides so the judges remember them:
- **"No edge without a claim, no claim without a grounded quote."**
- **"We don't connect everything. We say how much to trust each connection, and why."**
- **"Code is runtime truth, but not automatically right."**

`[X]` = fill in from `out/eval_report.md` after the final ETL run. Don't present numbers you haven't measured.

| # | Slide | Time | Speaker | Scores on |
|---|---|---|---|---|
| 0 | Title | 0:20 | P4 | — |
| 1 | The problem: sources disagree | 0:50 | P4 | Presentation |
| 2 | Our stance: trust, not just links | 0:40 | P4 | Knowledge repr |
| 3 | Architecture in 3 layers | 0:50 | P2 | Presentation |
| 4 | Extraction: deterministic first, AI where needed | 1:10 | P1 | Extraction |
| 5 | Knowledge representation: the claim graph | 1:30 | P2 | Knowledge repr |
| 6 | Trust rules: who wins a conflict | 0:50 | P2 | Knowledge repr |
| 7 | Validation: how we know it works | 1:10 | P4 | Presentation |
| 8 | Live demo: context in the IDE | 2:00 | P3 | Service usage |
| 9 | Security: findings become guardrails | 0:40 | P4 | Service usage / Knowledge repr |
| 10 | Trade-offs & limitations | 0:50 | P1 | Presentation |
| 11 | Forward view | 0:50 | P3 | Forward view |
| 12 | Close | 0:20 | P4 | — |

---

## Slide 0 — Title

**Key points**
- Team name, one-line pitch.
- Teaser: the one sentence everything else hangs on.

**How we could say it**
> "We built a Context Service that reads a messy engineering monorepo and tells you, and your AI coding
> assistant, how the systems are connected. It also tells you how much each of those connections can be
> trusted, and why."

---

## Slide 1 — The problem: sources disagree

**Key points**
- Knowledge lives in eight source types: code, SQL, config, OpenAPI, wiki, ADRs and meeting notes, issues and incidents, diagrams.
- They contradict each other: a stale wiki schema, a diagram from an older architecture, an ownership change recorded only in an ADR.
- Two kinds of users suffer: engineers ("what breaks if I change this?") and AI assistants, which confidently generate code against the stale version.
- The disagreement is the actual problem to solve, not noise to clean up.

**How we could say it**
> "In this repo the wiki says one thing, the code another, and a diagram a third. Usually nobody is lying.
> They were written at different times. A human gets lost in that, and an AI assistant is worse off: it picks
> one version at random and writes code against it. We decided the disagreement isn't noise to filter out.
> It's the core of the problem. A good context service has to show the conflict, explain it and resolve it
> with evidence."

Concrete example from the dataset (pick one, keep it short): *the wiki still documents the old Accounts v1 API
and a decimal money field, while the migrations moved to minor units.*

---

## Slide 2 — Our stance: trust, not just links

**Key points**
- The brief says: *"do not blindly connect everything."*
- Our principle: **no edge without a claim, no claim without a grounded quote.**
- Every relation carries an epistemic status: VERIFIED, DOCUMENTED, ASSERTED, PROPOSED, SUPERSEDED, CONTRADICTED.
- The output is context you can trust, not the biggest possible graph.

**How we could say it**
> "We could easily have built a graph with a thousand edges. It would look impressive and be dangerous.
> So we set one rule: no edge without a claim, and no claim without a quote that really exists in the cited
> file. Every connection in our graph knows where it came from and how confident we are in it. A verified
> call from code is not the same as an arrow on a three-year-old diagram, and our graph says so."

---

## Slide 3 — Architecture in 3 layers

**Key points**
- **Layer 1, agentic ETL:** router, then deterministic parsers plus LLM/VLM extractors, then grounding, entity resolution, validator and judge.
- **Layer 2, knowledge store:** Neo4j holds the property graph, full-text and vector indexes in one store, with a hybrid GraphRAG retriever on top.
- **Layer 3, delivery:** one query layer behind both MCP (IDE agents) and REST (humans, CI).
- Everything runs in Docker Compose with a single image.

**How we could say it**
> "Three layers. First, an agentic ETL. It routes every file by type, extracts claims, checks them against
> the source and reconciles them. Second, a knowledge store. We chose Neo4j because one store holds the
> graph, the full-text index and the vectors, so there's no second database to keep in sync. Third,
> delivery: the same query layer exposed over MCP for coding agents and over REST for people and CI
> pipelines. One `docker compose up` starts all of it."

---

## Slide 4 — Extraction: deterministic first, AI where needed

**Key points**
- Files are routed into **authority tiers**: 1 runtime truth (code, SQL, deploy config, CODEOWNERS), 2 contracts (OpenAPI, GraphQL), 3 intent and history (wiki, ADR, notes, diagrams), 4 archive.
- **Deterministic parsers** handle Tiers 1 and 2: Python AST, TS/JS, Java routes, SQL migration *replay*, OpenAPI, compose, .env, package.json, CODEOWNERS. They give exact line numbers, cost nothing and are reproducible.
- **LLM** for prose: claims, aliases ("Stock" = inventory-service), policies, and the *mode* of each statement (observed, proposed, deprecated, rejected).
- **VLM** for PNG diagrams, plus an **independent second VLM pass** that re-checks every arrow. A dashed arrow becomes PROPOSED.
- **LLM code review** per service, given the contract and policies, for the semantic bugs a regex can't catch.
- All LLM output is Pydantic structured output, cached by content hash, and goes through LiteLLM so the provider is swappable.

**How we could say it**
> "We didn't send everything to an LLM. Where a parser can read the truth exactly, in code, SQL migrations,
> OpenAPI or compose files, we use the parser. It's free, it's exact to the line, and it doesn't hallucinate.
> We save the LLM for what really needs language understanding: wiki pages, ADRs, meeting notes, incidents.
> There the hard part isn't spotting entities, it's spotting the *mode*: is this sentence saying what exists,
> what someone proposed, or what was rejected? Diagrams go to a vision model, and since vision models
> misread arrows, a second independent pass re-checks every arrow: does it exist, which way does it point,
> is it dashed or solid. All model output is schema-validated and cached by file hash, so re-runs cost nothing."

---

## Slide 5 — Knowledge representation: the claim graph

*(Biggest technical score. Spend the time here.)*

**Key points**
- **Reified graph**: we don't store "A CALLS B" as a bare fact. We store **claims** that support or dispute it.
- Node layers:
  - **Entity**: Service, Endpoint, Table, Column, Library, Team, Decision, Incident, Diagram, **Policy**, plus CONCEPT as a fallback for out-of-schema ideas.
  - **Claim**: one source's statement. Subject, predicate, object, mode, quoted snippet, file:line, tier, date, extractor.
  - **Source**: path, tier, date, owner, sha256, which drives incremental reload.
  - **Derived edge**: CALLS, EXPOSES, OWNED_BY… with **status + confidence + claim_ids**.
  - **Risk**: category, severity, evidence from both sides, current truth, *guidance for codegen*.
  - **Chunk**: a prose paragraph with an embedding, linked to the entities it talks about.
- The same entity is seen from several perspectives (code, contract, docs, diagram, incident) and each one stays traceable.
- Retrieval: exact alias match + full-text + vector, fused with RRF, then 2-hop expansion, Personalized PageRank weighted by status and confidence, capped at 30 edges, and serialized **relations-first**.

**How we could say it**
> "This is the heart of it. A naive knowledge graph says 'order-service calls payment-service' and that's
> it. Ours says: here are three claims supporting that edge, one from the code at this line, one from the
> OpenAPI contract and one from the wiki. That's why it's VERIFIED with confidence 0.94. We call it a
> claim graph: facts are derived from evidence, never typed in. The same service is visible through its
> code, its contract, its documentation, its diagram and its incidents, and you can trace every view back
> to a file and line.
>
> For retrieval we combine three signals: exact alias, full-text and semantic vectors. From those seeds we
> expand the graph two hops and rank the edges with PageRank weighted by trust, so verified facts float
> up and weak ones sink. We cap it at 30 edges so a hub like the users table doesn't flood the
> assistant's context window. The model receives relations first, then warnings, then source passages."

Visual idea: one edge, `order-service → payment-service`, opened up into its 3 claims, each with a file:line
badge and a tier color.

---

## Slide 6 — Trust rules: who wins a conflict

**Key points**
- Status is **computed from claims, never asserted by an LLM**:
  - VERIFIED needs at least one Tier-1 observed claim.
  - DOCUMENTED means contract only.
  - ASSERTED means prose or diagram only. Confidence is 0.4 + 0.1 per independent file, capped at 0.7. A diagram without the second-pass check is capped at 0.5.
  - PROPOSED means plans, dashed arrows, "Proposed" ADRs.
  - SUPERSEDED / CONTRADICTED come from SQL replay or from the judge (a newer decision wins).
- Archive material is namespaced and **can never merge** with live entities.
- **Avoiding "bug-as-truth":** code is runtime truth, not automatically correct. When code breaks a MUST in a contract or policy, that's a **POLICY_VIOLATION** and the code is wrong. That's different from **STALE_DOC**, where the doc is wrong.

**How we could say it**
> "When sources disagree, who wins? The simple answer is 'code always wins'. That's dangerous, because it
> means a bug in the code automatically becomes the truth. So we split it in two. If the doc describes
> something that no longer exists, that's a stale doc: the doc is wrong. If the code violates a contract,
> say the payment API promises idempotency and the code never checks the key, that's a policy violation:
> the code is wrong. And one more thing: no LLM sets the status. It's computed by rules from the evidence,
> so we can always explain why an edge is verified or only asserted."

---

## Slide 7 — Validation: how we know it works

**Key points**
- **In-pipeline gates, on every run:**
  1. Schema gate: valid Pydantic output, one repair attempt, otherwise dropped.
  2. Grounding: the quote must exist in the cited file (exact, or fuzzy ≥ 0.9). Line numbers are recomputed from the file. **We never trust the LLM's line numbers.**
  3. Only a Tier-1 observation can make something VERIFIED.
  4. Scope guard: no alias or merge from archive to live.
  5. The judge must cite ≥ 2 different files, otherwise the finding is downgraded to UNVERIFIED_ASSUMPTION.
  6. Post-load Cypher integrity checks: no dangling claims, no risk without evidence.
- **Golden set:** 10 planted problems (G), 8 **decoys that must NOT link** (D), positive controls (P), 7 MCP probes (Q).
- **Ablation:** deterministic only → + LLM extraction → + judge. Shows what each layer adds.
- Results: recall `[X/10]`, decoy false links `[X/8]`, positive controls `[X]`, probes `[X/7]`.
- Honest caveat: the golden set was built by hand from this corpus, so the pipeline contains **no file names and no corpus-specific rules**. Only `eval/` refers to them.

**How we could say it**
> "With LLMs in the pipeline you have to prove the output is reliable. We do it on two levels. In the
> pipeline itself, every quote the model returns has to really exist in the file. If it doesn't, the claim
> is rejected and logged in the audit report. The model's line numbers are never used; we compute our own.
>
> Second, we built a golden set. It has ten real problems we expect to find, and, importantly, eight decoys
> we must *not* connect. For example, there's an old PaymentService in the archive with nothing to do with
> the live payment-service, an SSO that was only proposed, and a manual CSV export between HR and Finance
> that isn't a service call. Precision on decoys matters as much as recall. The ablation shows what each
> layer adds: parsers alone find [X], adding the LLM gets [X], and the judge brings it to [X] while keeping
> decoys at [X].
>
> One honest note: we built the golden set ourselves from this dataset, so it's an overfitting risk. To
> counter that, the pipeline has no file names or dataset-specific rules. Every rule is a general pattern."

---

## Slide 8 — Live demo: context in the IDE

*(Biggest risk, biggest payoff. Have a recorded backup video ready to switch to without hesitation.)*

**Key points / demo flow** (pick 3–4, in this order)
1. **Claude Code connected via MCP** (`claude mcp add --transport http context-service http://127.0.0.1:8765/mcp`). Show the 9 read-only tools.
2. **"Why were customers charged twice?"** → links the incident to the payment code; the idempotency key is never looked up. That's a POLICY_VIOLATION, with evidence from the contract and the code.
3. **Plan validation**: the agent plans to use `orders.total` and `redis` →
   - `orders.total` REJECT: the column was dropped, use `total_minor`.
   - `redis` REJECT: ADR-003 removed the read cache.
4. **"Who owns inventory-service?"** → Supply Operations from CODEOWNERS; the wiki's older owner is flagged as ownership drift.
5. **Blast radius of the `users` table** → dependent services, each hop with evidence, owners and docs to update.
6. (Optional, for humans) the same thing over REST at `/docs`, or the graph in Neo4j Browser colored by status.

**How we could say it**
> "Now from the developer's side. This is Claude Code with our service connected over MCP. No plugin,
> any MCP-compatible agent works the same way.
>
> First question: why were customers charged twice? The service links the incident to the payment code
> and finds that the payment API promises idempotency, but the code never looks up the idempotency key.
> And you see the evidence: contract here, code here, line by line.
>
> Second: the agent wants to write a new feature and plans to read `orders.total` and add a Redis cache.
> Before it writes a single line, it asks our service. The answer is no, twice. That column was dropped in
> a migration, use `total_minor`. And Redis was removed by a decision in ADR-003. Without us, the
> assistant would have written code against a schema that no longer exists, and nobody would have
> noticed until runtime.
>
> And for people, the same thing is available over REST, plus a graph where green is verified, orange is
> stale and red is a security risk."

**Cut list if running long:** drop step 4, then step 5. Never drop step 3; it's the clearest value for AI assistants.

---

## Slide 9 — Security: findings become guardrails

**Key points**
- 8 generic security rules: wildcard CORS, spoofable identity header with a fail-open default, PII in logs, idempotency key never looked up, by-id read without an ownership check (IDOR), unauthenticated mutation, GraphQL defaults, error-message leak.
- Plus the LLM code review for semantic issues, e.g. the last-unit off-by-one.
- Each finding is a `Risk` node with **guidance_for_codegen**: `get_security_guidance` returns "don't repeat this pattern" rules, including risks inherited through shared libraries.
- **The service is an attack surface too:** MCP can amplify prompt injection. Our mitigations: all tools read-only, Tier-3 text sanitised and wrapped in `<untrusted_source>`, server instructions tell the agent to treat it as data.

**How we could say it**
> "Finding a vulnerability isn't the end of the job. The point is that the AI assistant doesn't copy it
> into new code. So every finding comes with a rule for code generation, for example 'reading an order by
> ID must check the owner'. The agent gets that before it starts writing. And we treated our own service
> as an attack surface: wikis and issues are text anyone can write into, including a hidden instruction
> for the AI. So all tools are read-only, and text from docs is cleaned and labeled as untrusted data."

---

## Slide 10 — Trade-offs & limitations

*(Present these yourselves. The judges explicitly asked for limitations.)*

**Key points** (choose 3–4)
| Decision | Why | Cost | Next step |
|---|---|---|---|
| Regex/AST parsers instead of tree-sitter/CodeQL | fast, exact lines | brittle on unusual code styles | tree-sitter behind the same `Claim` contract, Semgrep packs |
| LLM judge per entity cluster | catches cross-document supersession | costs more as the repo grows; can be wrong | must cite ≥ 2 files; can't overrule Tier 1; human review via the audit report |
| Flash-class model (free tier) | it runs at all on the access we had | weaker on subtle conflicts | model is configurable; the ablation measures the difference |
| Fixed ontology + CONCEPT fallback | deterministic tools (blast radius) | new concepts land in CONCEPT | human-in-the-loop promotion into new types |
| Lexical/fuzzy entity resolution | predictable, explainable | misses paraphrases without an alias sentence | embedding similarity + LLM merge confirmation |
| Raster diagrams | unlocks diagram-only knowledge | no text to quote, 2 vision calls | prefer SVG / diagrams-as-code, OCR for labels |

**How we could say it**
> "Here's where it's weak. The parsers are fast and exact, but they'd break on unusual code; in production
> we'd put tree-sitter behind the same interface. The judge is an LLM, so it can be wrong. That's why it
> has to cite at least two files and can never overrule a fact from code. We ran on a free Flash-tier
> model, which is weaker on subtle conflicts. The model is one config value, though, and the ablation
> lets us measure what a stronger one would add. We'd rather show you where the limits are than have you
> discover them yourselves."

---

## Slide 11 — Forward view

**Key points**
- **Freshness:** a Git webhook or CI job re-runs the ETL. Source sha256 makes it **incremental**: only claims from changed files are replaced. The LLM cache is keyed by content hash, so unchanged files cost nothing.
- **CI gate:** `validate-plan` and `security/{path}` run on the PR. They fail a PR that brings back a dropped column or a known insecure pattern, or that touches a component whose docs are stale.
- **Doc-debt loop:** each STALE_DOC risk names the stale file, the truth file and the current truth, which is enough to **auto-open a documentation PR**.
- **Scale:** extraction sharded per repo, Neo4j as the system of record (clustering / GDS for in-DB PageRank); the `GraphRepository` interface already has 2 backends.
- **Trust over time:** measure judge accuracy against human resolutions; grow the golden set from accepted and rejected findings.

**How we could say it**
> "How would this live in a real company? It wouldn't be a one-off run. It would be a step in CI. Every
> commit re-ingests only the changed files, because the hash tells us what changed, and unchanged files
> don't cost a single LLM call. The same service then works as a gate on pull requests: if you bring back
> a dropped column or a known vulnerability, the PR fails with evidence. And every stale doc we find
> already contains what's wrong and what's true, so the service can open a PR to fix the doc itself.
> Documentation stops drifting from reality. Over time we'd measure the judge against human decisions
> and grow the golden set from real findings, so trust in the system is measured, not assumed."

---

## Slide 12 — Close

**Key points**
- Recap in 3 beats: hybrid extraction → claim graph with computed trust → context in the IDE via MCP.
- Back to the core sentence.

**How we could say it**
> "To sum up: deterministic parsers where truth can be read exactly, AI where language has to be
> understood, and everything checked against the source. A graph that doesn't just say what's connected,
> but how much to trust it and why. And all of it delivered straight to the developer's assistant before
> it writes the wrong code. No edge without a claim, no claim without a grounded quote. Thank you, we're
> happy to take questions."

---

## Appendix A — Likely Q&A

| Question | Short answer |
|---|---|
| Why not just vector RAG? | Vector search finds similar text but can't answer "what breaks if I change X". That's graph traversal. And it can't tell you which of two similar passages is current. We use vectors only to *seed* the search, then the graph does the rest. |
| How do you handle LLM hallucination? | Quote grounding against the file, recomputed line numbers, schema validation, status computed by rules, judge must cite ≥ 2 files, Tier 1 can't be overruled. Ungrounded claims go to the audit report. |
| What if the code is the bug? | POLICY_VIOLATION vs STALE_DOC (slide 6). |
| Why Neo4j and not NetworkX? | One store for graph + full-text + vectors means no sync problem, plus Cypher integrity checks. An in-memory backend exists behind the same interface for tests. |
| Cost at enterprise scale? | Parsers cost nothing; the LLM only runs on prose and diagrams; content-hash cache plus incremental ingest means you pay for the diff, not the repo. |
| How do you tell two unrelated projects using the same API from a typo? | Scope guard + an evidence requirement. Without shared deployment config or an explicit call, the link stays low-confidence / UNVERIFIED_ASSUMPTION (decoys D1, D2, D8). |
| Isn't your golden set overfit? | Yes, that risk is real (slide 7). Mitigation: no corpus-specific rules in the pipeline; the next step is a held-out repo. |
| Access control? | Not in the PoC. Next: per-source ACLs propagated onto claims, so an agent only sees context its user may see. |

## Appendix B — Delivery tips
- Show one concrete dataset example per slide; abstract claims don't land.
- Slides 5–7 carry the 25-point category. Don't rush them to make time for the demo.
- Rehearse with a timer. If you're past 6:00 at the start of slide 8, use the cut list.
- Fill in `[X]` only from `out/eval_report.md`. If the ablation hasn't run, say "measured on the golden set" and show the audit stats instead.
