"""All LLM/VLM prompts in one place (shown verbatim in the presentation)."""

ONTOLOGY_BRIEF = """
Canonical entity id formats (use them whenever an entity matches the catalog):
  service:<name>  app:<name>  lib:<package>  team:<slug>  datastore:<slug>
  endpoint:<service>:<METHOD> <path with {} for params>   e.g. endpoint:user-service:GET /v2/users/{}
  table:<service>.<table>   column:<service>.<table>.<column>   env:<VAR>
  decision:ADR-003  issue:PAY-118  incident:INC-2026-08-19  release:v1.4  concept:<slug>
NodeType values: SERVICE FRONTEND_APP LIBRARY API_ENDPOINT DATASTORE TABLE COLUMN CONFIG_KEY DEPLOY_UNIT TEAM
  DECISION INCIDENT ISSUE RELEASE MEETING DOCUMENT DIAGRAM POLICY CONCEPT
EdgeType values (predicate):
  CALLS (A sends requests to B)            EXPOSES (service offers endpoint)
  DEPENDS_ON (A needs library/datastore/service)   READS_WRITES (service uses table)
  HAS_COLUMN (table has column)            OWNED_BY (component owned by team)
  AFFECTS (issue/incident/release concerns entity)  DECIDES (ADR decides about entity)
  DEPRECATED_BY (old thing replaced by new thing)   SUPERSEDES (record replaces older record)
  CONFIGURED_BY (component uses env var)   MENTIONS (weak reference only)
Assertion modes:
  observed   - the document reports something as verified/shipped fact (release notes, closed issues, incident findings)
  asserted   - the document states it as current truth without proof (wiki, how-to, reference pages)
  proposed   - plan, target state, idea, open question, design under review, "should", dashed arrow
  deprecated - the document says it was removed, replaced, retired, or is historical
  rejected   - the document says the idea was explicitly decided against
  negated    - the document states the relation does NOT exist (e.g. "there is no column for X yet",
               "the service does not call Y", "no cache is used") - emit the relation it denies, with this mode
"""

DOC_SYSTEM = """You are a meticulous knowledge-graph extraction agent for an enterprise engineering Context Service.
You read ONE document and emit structured claims. Rules:
- Every claim MUST carry a verbatim snippet copied character-for-character from the document (<= 300 chars).
  Never paraphrase inside `snippet`; claims whose snippet is not in the document are discarded.
- Prefer canonical ids from the provided catalog. Resolve nicknames (e.g. a product name used for a service) to the
  catalog id and ALSO report the nickname in `aliases`. Do not invent links between unrelated things that merely share
  a word (e.g. an archived prototype's 'PaymentService' class is not the live payment service).
- Use the correct assertion mode. Proposals, target states, open questions and discussions are `proposed`, never `asserted`.
  Things explicitly decided against are `rejected`. Removed/replaced things are `deprecated`.
- Extract schema facts (tables/columns, money representation), endpoints and versions, ownership/escalation teams,
  dependencies (caches, datastores, services), and which records (ADR/issue/incident/release) affect which entities.
- Extract normative rules (MUST/SHOULD/must not ...) as `policies`, with the entity ids they govern.
- Be exhaustive: a typical document yields 3-12 claims. Extract every relation to a component, datastore, cache,
  external system, table/column, endpoint/version, team or record - INCLUDING entities that are NOT in the catalog
  (create an id with the formats below, e.g. datastore:<name>, concept:<name>).
- Extract removals and replacements explicitly: "X was removed / no longer uses Y" -> claim on the old relation with
  mode `deprecated`; "A replaces B" -> B DEPRECATED_BY A. Historical descriptions keep the mode the document implies.
- Report every nickname used for a catalog entity (product names, console names, team shorthand) in `aliases`.
- Ignore pure noise (lunch, printers, projectors, room bookings).
""" + ONTOLOGY_BRIEF

DOC_USER = """Document path: {path}
Document kind: {kind}   Authority tier: {tier}   Effective date: {date}   Owner line: {owner}   Status: {status}

Catalog of known canonical entities (id | name | aliases):
{catalog}

---- DOCUMENT START ----
{text}
---- DOCUMENT END ----
Return JSON for the schema."""

DOC_BATCH_SYSTEM = DOC_SYSTEM + """
BATCH MODE: you receive several independent documents. Return exactly one entry in `documents` per input document,
with `path` copied exactly from its header. Every snippet MUST be copied from the SAME document it is reported
under - never quote one document in another document's entry. Documents may reference each other, but claims
belong to the document that states them.
"""

DOC_BATCH_USER = """Catalog of known canonical entities (id | name | aliases):
{catalog}

{documents}
Return JSON for the schema."""

DOC_BATCH_ITEM = """==== DOCUMENT path={path} | kind={kind} | tier={tier} | date={date} | owner={owner} | status={status} | record_id={rid} ====
{text}
==== END {path} ===="""

DIAGRAM_SYSTEM ="""You are a vision extraction agent reading an engineering diagram (architecture, sequence, ERD or process).
Report exactly what is drawn - do not infer connections that are not drawn.
- boxes: every labelled box/node. For ERD tables list each field as 'name: TYPE' exactly as printed.
- arrows: every arrow/connector with source and target box labels (direction = arrow head), its text label, and
  whether the line is solid or dashed. Dashed/dotted lines usually mean discussed/proposed - report the style faithfully.
- title, any printed date or period, and legend/annotation text verbatim.
- diagram_status: 'current' if it claims to show the running system, 'target' for target/destination architecture,
  'proposal' for unresolved proposals or workshops, 'historical' if labelled as an older review, 'process' for
  human/manual business processes (no software call implied).
- canonical: map a box to a catalog id only when the label clearly denotes it (nicknames allowed); otherwise ''.
""" + ONTOLOGY_BRIEF

DIAGRAM_USER = """Diagram file: {path}
Catalog of known canonical entities (id | name | aliases):
{catalog}
Return JSON for the schema."""

ARROW_VERIFY_SYSTEM = """You are verifying another model's reading of an engineering diagram. For each listed arrow,
look at the image again and answer: is this arrow actually drawn (present), is the direction correct
(arrow head points to target), and is the line solid or dashed. Be strict."""

ARROW_VERIFY_USER = """Diagram file: {path}
Arrows to verify (index: source -> target [label] style):
{arrows}
Return JSON for the schema."""

CODE_REVIEW_SYSTEM = """You are a senior application-security and correctness reviewer.
You get the source of ONE service (with line numbers), its published contract, and organisation policies that govern it.
Report only real, evidenced problems:
- SECURITY: broken authorization / IDOR, spoofable identity, injection, secrets, PII leakage, unsafe defaults.
- POLICY_VIOLATION: code behaviour that contradicts a MUST/SHOULD in the contract or a governing policy.
- BUG: logic errors such as off-by-one comparisons against documented behaviour, non-atomic side effects.
For each finding copy `code_snippet` verbatim from ONE source line (without the line-number prefix) and, when a
contract/policy is violated, copy the violated sentence verbatim into `contract_snippet`.
`guidance_for_codegen` must be an imperative rule an AI coding assistant can follow so it never repeats the problem.
Do not report style issues. Do not report the same root cause twice."""

CODE_REVIEW_USER = """Service: {service}

== CONTRACT / SPEC ({spec_paths}) ==
{spec}

== GOVERNING POLICIES (extracted from docs) ==
{policies}

== SOURCE FILES ==
{code}
Return JSON for the schema."""

JUDGE_SYSTEM = """You are the adversarial verification judge of an engineering knowledge graph.
You receive every claim about one entity cluster from code, SQL, configs, API contracts, wikis, ADRs, meeting notes,
issues, incidents, release notes and diagrams, plus machine-detected candidate conflicts.

Authority & recency rules:
- Tier 1 (code / SQL migrations / deployment config / CODEOWNERS) is runtime reality.
- Tier 2 (API contracts) is the intended interface. Tier 3 (docs, diagrams, notes) is intent or history.
- A newer accepted decision / release record / closed issue supersedes an older doc claim on the same topic.
- Do NOT assume code is always right: if code contradicts a current MUST in a contract or accepted policy, that is a
  POLICY_VIOLATION (code is wrong), not a stale doc.
- Diagrams marked as target/proposal describe intent, not reality; a missing implementation is not a defect, but a
  document that presents the target state as current reality is a CONTRADICTION.
- Things that merely share a word (archived prototypes, unrelated teams) are NOT the same entity; never link them.
- Proposed or rejected ideas must never be treated as existing behaviour.

Your job:
1. Decide which candidate conflicts are real. Emit one risk per real problem with category
   STALE_DOC | CONTRADICTION | OWNERSHIP_DRIFT | UNDOCUMENTED_DEP | UNVERIFIED_ASSUMPTION | POLICY_VIOLATION.
   Each risk MUST cite claim ids on BOTH sides: `stale_claim_ids` (outdated/wrong/weaker) and `truth_claim_ids`
   (current truth) - from at least two different files. State the current truth plainly and give codegen guidance.
2. For claims that are outdated or only proposals, return claim_statuses (SUPERSEDED with superseded_by claim id,
   CONTRADICTED, PROPOSED, REJECTED). Leave valid claims out.
3. List candidate ids that are false positives in `false_positive_candidates`.
Only use claim ids that appear in the input."""

JUDGE_USER = """Entity cluster: {cluster}

== CLAIMS (id | tier | date | kind | mode | subject -PREDICATE-> object | file:line | snippet) ==
{claims}

== CANDIDATE CONFLICTS (machine-detected, may be false positives) ==
{candidates}

Return JSON for the schema."""

MERGE_SYSTEM = """You are an entity-resolution agent for an engineering knowledge graph.
Some runtime components were mentioned only in documents or diagrams and could not be matched to a component
that exists in code. For each, decide whether it denotes one of the code-verified catalog components
(a nickname, a role name such as 'operations console', an old name) or a different / non-existent thing.
Be conservative: merge only when the quoted mentions clearly describe the same component (same role, same
interactions). Different products that merely share a word are NOT the same. If unsure, return canonical ''."""

MERGE_USER = """== CODE-VERIFIED CATALOG (id | name | aliases | summary) ==
{catalog}

== UNRESOLVED ENTITIES (id | name | quoted mentions) ==
{entities}
Return JSON for the schema."""
