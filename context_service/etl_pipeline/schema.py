"""Ontology + provenance schema.

Design: a *claim-based* (reified) knowledge graph.
  * Entity   – a thing in the engineering landscape (service, table, team, ADR, ...).
  * Claim    – one source's statement about entities ("wiki X says service A owned_by team B, Feb 2026").
  * Edge     – aggregate of all claims for (src, type, dst) with a computed epistemic status.
  * Risk     – a finding (security / contradiction / stale doc ...) backed by evidence from >= 1 claim.
  * Source   – the file a claim came from, with authority tier, kind, date and owner.

Edges never exist without claims, claims never exist without a grounded snippet.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    # runtime / systems
    SERVICE = "SERVICE"
    FRONTEND_APP = "FRONTEND_APP"
    LIBRARY = "LIBRARY"
    API_ENDPOINT = "API_ENDPOINT"
    DATASTORE = "DATASTORE"
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    CONFIG_KEY = "CONFIG_KEY"
    DEPLOY_UNIT = "DEPLOY_UNIT"
    # organisation / knowledge records
    TEAM = "TEAM"
    DECISION = "DECISION"
    INCIDENT = "INCIDENT"
    ISSUE = "ISSUE"
    RELEASE = "RELEASE"
    MEETING = "MEETING"
    DOCUMENT = "DOCUMENT"
    DIAGRAM = "DIAGRAM"
    POLICY = "POLICY"
    # out-of-schema fallback (promoted by humans later)
    CONCEPT = "CONCEPT"


class EdgeType(str, Enum):
    CALLS = "CALLS"
    EXPOSES = "EXPOSES"
    DEPENDS_ON = "DEPENDS_ON"
    READS_WRITES = "READS_WRITES"
    HAS_COLUMN = "HAS_COLUMN"
    CONFIGURED_BY = "CONFIGURED_BY"
    DEPLOYED_AS = "DEPLOYED_AS"
    OWNED_BY = "OWNED_BY"
    DOCUMENTS = "DOCUMENTS"
    DECIDES = "DECIDES"
    AFFECTS = "AFFECTS"
    GOVERNS = "GOVERNS"
    SUPERSEDES = "SUPERSEDES"
    DEPRECATED_BY = "DEPRECATED_BY"
    CONTRADICTS = "CONTRADICTS"
    ALIAS_OF = "ALIAS_OF"
    MENTIONS = "MENTIONS"


# Edges that carry runtime impact (used for blast radius / change impact).
IMPACT_EDGES = {EdgeType.CALLS, EdgeType.DEPENDS_ON, EdgeType.READS_WRITES, EdgeType.EXPOSES,
                EdgeType.HAS_COLUMN, EdgeType.CONFIGURED_BY}

Modality = Literal["code", "sql", "config", "api_spec", "doc", "diagram"]
AssertionMode = Literal["observed", "asserted", "proposed", "deprecated", "rejected"]
EdgeStatus = Literal["VERIFIED", "DOCUMENTED", "ASSERTED", "PROPOSED", "SUPERSEDED", "CONTRADICTED", "REJECTED"]
RiskCategory = Literal["SECURITY", "POLICY_VIOLATION", "CONTRADICTION", "STALE_DOC", "OWNERSHIP_DRIFT",
                       "UNDOCUMENTED_DEP", "UNVERIFIED_ASSUMPTION", "BUG"]
Severity = Literal["LOW", "MEDIUM", "HIGH"]


class SourceDoc(BaseModel):
    path: str                       # repo-relative
    kind: str                       # code | sql_migration | config | codeowners | openapi | api_spec_md | graphql |
                                    # wiki | adr | design | architecture | reference | howto | runbook | meeting |
                                    # issue | incident | release | diagram | readme | archive | api_history
    modality: Modality
    tier: int                       # 1 runtime truth, 2 contract, 3 intent/history, 4 retired archive
    scope: Literal["live", "archive"] = "live"
    title: str = ""
    date: Optional[str] = None      # ISO yyyy-mm-dd (or yyyy-mm)
    owner: Optional[str] = None     # team named in the metadata line
    status: Optional[str] = None    # Accepted / Proposed / Investigating / Closed / Deferred ...
    record_id: Optional[str] = None # ADR-003, PAY-118, ...
    sha256: str = ""
    size: int = 0


class Provenance(BaseModel):
    file_path: str
    modality: Modality
    tier: int
    snippet: str = Field(description="Exact quote / code line / visual description")
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    effective_date: Optional[str] = None
    doc_kind: str = ""
    extractor: str = ""             # parser name or model id


class Entity(BaseModel):
    id: str                         # canonical, e.g. service:order-service
    type: NodeType
    name: str
    aliases: list[str] = Field(default_factory=list)
    scope: Literal["live", "archive"] = "live"
    summary: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    grounded_tier: int = 9          # best (lowest) tier that observed this entity


class Claim(BaseModel):
    id: str = ""
    subject: str                    # entity id
    predicate: EdgeType
    object: str                     # entity id
    mode: AssertionMode = "asserted"
    properties: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    status: Optional[str] = None    # set by validator: SUPERSEDED / CONTRADICTED / ...
    superseded_by: Optional[str] = None

    def compute_id(self) -> str:
        raw = f"{self.subject}|{self.predicate.value}|{self.object}|{self.provenance.file_path}|{self.provenance.line_start}|{self.mode}"
        self.id = "c_" + hashlib.sha1(raw.encode()).hexdigest()[:14]
        return self.id


class Edge(BaseModel):
    source: str
    type: EdgeType
    target: str
    status: EdgeStatus
    confidence: float = Field(ge=0.0, le=1.0)
    claim_ids: list[str]
    best_tier: int
    latest_date: Optional[str] = None


class EvidenceRef(BaseModel):
    file_path: str
    snippet: str
    line_start: Optional[int] = None
    tier: int = 3
    date: Optional[str] = None
    side: Literal["claim", "truth", "context"] = "context"
    claim_id: Optional[str] = None


class Risk(BaseModel):
    id: str = ""
    category: RiskCategory
    severity: Severity
    title: str
    description: str
    entities: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    current_truth: str = ""
    guidance_for_codegen: str = ""
    detected_by: str = ""           # rule / parser / model id
    confidence: float = 0.7

    def compute_id(self) -> str:
        raw = f"{self.category}|{'/'.join(sorted(self.entities))}|{self.title.lower()}"
        self.id = "r_" + hashlib.sha1(raw.encode()).hexdigest()[:12]
        return self.id


class ContextGraph(BaseModel):
    generated_at: str
    repo_root: str
    sources: list[SourceDoc]
    entities: list[Entity]
    claims: list[Claim]
    edges: list[Edge]
    risks: list[Risk]
    audit: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# LLM structured-output schemas (kept flat & simple: Gemini structured output
# works best with shallow schemas and string enums).
# ---------------------------------------------------------------------------
class LLMClaim(BaseModel):
    subject: str = Field(description="Entity name exactly as best identified (prefer canonical ids from the catalog)")
    subject_type: str = Field(description="One of the NodeType values")
    predicate: str = Field(description="One of the EdgeType values")
    object: str
    object_type: str
    mode: str = Field(description="observed | asserted | proposed | deprecated | rejected")
    snippet: str = Field(description="Verbatim quote (<= 300 chars) from the document supporting this claim")


class LLMAlias(BaseModel):
    alias: str
    canonical: str = Field(description="canonical entity id from the catalog, e.g. service:payment-service")
    snippet: str


class LLMPolicy(BaseModel):
    statement: str = Field(description="Normative rule, e.g. 'Repeating the same Idempotency-Key MUST return the original charge'")
    strength: str = Field(description="MUST | SHOULD | MAY")
    governs: list[str] = Field(description="canonical entity ids the rule applies to")
    snippet: str


class LLMDocExtraction(BaseModel):
    summary: str = Field(description="1-2 sentence summary of what this document asserts")
    claims: list[LLMClaim] = Field(default_factory=list)
    aliases: list[LLMAlias] = Field(default_factory=list)
    policies: list[LLMPolicy] = Field(default_factory=list)
    mentioned_records: list[str] = Field(default_factory=list, description="IDs like ADR-003, PAY-118, INC-2026-08-19")


class LLMDocBatchItem(LLMDocExtraction):
    path: str = Field(description="exact document path as given in the DOCUMENT header")


class LLMDocBatch(BaseModel):
    documents: list[LLMDocBatchItem] = Field(description="one entry per input document, in input order")


class LLMDiagramBox(BaseModel):
    label: str
    kind: str = Field(description="service | frontend | datastore | table | team | external | process | note")
    canonical: str = Field(default="", description="canonical entity id from catalog if confidently matched, else ''")
    fields: list[str] = Field(default_factory=list, description="for ERD tables: 'column: TYPE' strings")


class LLMDiagramArrow(BaseModel):
    source: str = Field(description="label of the source box")
    target: str = Field(description="label of the target box")
    label: str = ""
    style: str = Field(description="solid | dashed")


class LLMDiagramExtraction(BaseModel):
    title: str
    date_text: str = Field(description="date or period printed on the image, verbatim")
    diagram_status: str = Field(description="current | target | proposal | historical | process")
    boxes: list[LLMDiagramBox]
    arrows: list[LLMDiagramArrow]
    annotations: list[str] = Field(default_factory=list, description="legend / notes text, verbatim")


class LLMArrowCheck(BaseModel):
    index: int
    present: bool
    direction_correct: bool
    style: str


class LLMArrowVerification(BaseModel):
    checks: list[LLMArrowCheck]


class LLMCodeFinding(BaseModel):
    title: str
    category: str = Field(description="SECURITY | POLICY_VIOLATION | BUG")
    severity: str = Field(description="LOW | MEDIUM | HIGH")
    code_snippet: str = Field(description="verbatim line(s) from the code file")
    contract_snippet: str = Field(default="", description="verbatim sentence from the contract/spec that is violated, '' if none")
    explanation: str
    guidance_for_codegen: str = Field(description="imperative rule an AI coding assistant must follow to avoid repeating this")


class LLMCodeReview(BaseModel):
    findings: list[LLMCodeFinding]


class LLMJudgeRisk(BaseModel):
    category: str = Field(description="STALE_DOC | CONTRADICTION | OWNERSHIP_DRIFT | UNDOCUMENTED_DEP | UNVERIFIED_ASSUMPTION | POLICY_VIOLATION")
    severity: str
    title: str
    description: str
    stale_claim_ids: list[str] = Field(description="claim ids that are outdated / wrong / weaker")
    truth_claim_ids: list[str] = Field(description="claim ids that represent current truth")
    current_truth: str
    guidance_for_codegen: str = ""


class LLMClaimStatus(BaseModel):
    claim_id: str
    status: str = Field(description="SUPERSEDED | CONTRADICTED | PROPOSED | REJECTED | VALID")
    superseded_by: str = ""
    reason: str = ""


class LLMJudgeVerdict(BaseModel):
    risks: list[LLMJudgeRisk]
    claim_statuses: list[LLMClaimStatus] = Field(default_factory=list)
    false_positive_candidates: list[str] = Field(default_factory=list, description="candidate ids judged NOT to be real problems")
