# Efficient AI Context Service: System Design & Agentic ETL Specification

## 1. High-Level System Architecture (2026 State-of-the-Art)

```
[Layer 1: Scattered Monorepo Sources]
  ├── Code & Configs (.py, .js, Dockerfile, .tf, package.json)
  ├── Database Schemas (.sql, migrations)
  ├── API Specifications (OpenAPI .yaml, .json)
  ├── Visual Diagrams (.png, .svg)
  └── Unstructured Docs (.md wikis, meeting notes, incident tickets)
         │
         ▼
[Layer 2: Modality Router & Authority Classifier]
  ├── Tier 1 (Runtime Truth): Code, SQL, Deployment Configs
  ├── Tier 2 (Contract Truth): OpenAPI / Event Specs
  └── Tier 3 (Intended/Historical): Diagrams, Wikis, Issue Notes
         │
         ▼
[Layer 3: Parallel Multi-Modal LLM/VLM Extraction]
  └── Strict Pydantic Structured Outputs (Nodes, Edges, Provenance, Security Flags)
         │
         ▼
[Layer 4: Adversarial Validation & Entity Resolution Engine]
  ├── Canonical Entity Clustering (Sub-graph alignment)
  ├── Conflict Detection Agent (Tier 1 vs. Tier 3 contradiction mapping)
  └── False-Positive & Security Risk Judge
         │
         ▼
[Layer 5: Hybrid GraphRAG Storage]
  ├── Knowledge Graph (Deterministic multi-hop topology & provenance)
  └── Vector Index (Semantic embeddings of unstructured passages)
         │
         ▼
[Layer 6: Delivery Gateway (MCP & REST API)]
  └── Exposes verified context to IDEs, Humans, and Agentic Coding Assistants
```

*   **Ingestion Layer (Agentic ETL):** Crawls the shared GitHub monorepo and routes diverse files (code, PNG/SVG diagrams, API specs, configs, wikis) to modality-specific LLM/VLM extraction prompts, tagging each with source provenance and an authority tier.
*   **Validation & Reconciliation Layer:** Uses a multi-agent memory and conflict-resolution pattern (`MemGraphRAG`, *arXiv:2606.00610*) so the system does not blindly connect everything. It cross-references extracted claims to flag security risks, stale documentation, and architectural drift.
*   **Knowledge Representation (Hybrid GraphRAG):** Combines a structured graph for explicit multi-hop dependency tracking (`SubgraphRAG`, Li et al., 2025; `LightRAG`, Guo et al., 2025, cited in *arXiv:2512.20626*) with vector embeddings for semantic search over incident notes. Serialization to downstream LLMs uses structured JSON and relations-first formatting, which empirically outperforms natural language descriptions on KG comprehension benchmarks (Markowitz et al., 2025; Dai et al., 2025, cited in Baldwin & Ghanavati, 2026, *arXiv:2604.27713*).
*   **Delivery Interface:** Exposes the validated graph via the Model Context Protocol (MCP) and REST APIs for IDEs and coding agents.

---

## 2. Claude Code Execution Specification (Ingestion & Extraction Layer)

Feed the instructions below directly into Claude Code to build the PoC extraction and validation pipeline.

### Project Directory Structure
Create a modular Python architecture using `asyncio`, `pydantic`, and `litellm` (or native SDKs supporting Structured Outputs):

```text
etl_pipeline/
├── config.py          # Concurrency limits, model names, file extensions
├── schema.py          # Strict Pydantic ontology & provenance schemas
├── router.py          # Repository crawler and modality classifier
├── extractors.py      # Async LLM/VLM extraction workers
├── validator.py       # Entity resolution, conflict & security Judge Agent
└── main.py            # Pipeline orchestrator outputting validated_graph.json
```

### Step 1: Ontology & Provenance Schema (`schema.py`)
Define strict Pydantic models for LLM Structured Outputs. Every extracted entity and edge MUST include source provenance.

```python
from enum import Enum
from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field

class NodeType(str, Enum):
    SERVICE = "SERVICE"
    API_ENDPOINT = "API_ENDPOINT"
    DATABASE_TABLE = "DATABASE_TABLE"
    CONFIG_KEY = "CONFIG_KEY"
    LIBRARY = "LIBRARY"
    ARCHITECTURAL_DECISION = "ARCHITECTURAL_DECISION"
    INCIDENT = "INCIDENT"

class EdgeType(str, Enum):
    CALLS = "CALLS"
    DEPENDS_ON = "DEPENDS_ON"
    READS_WRITES = "READS_WRITES"
    IMPLEMENTS = "IMPLEMENTS"
    CONTRADICTS = "CONTRADICTS"
    DEPRECATED_BY = "DEPRECATED_BY"
    CAUSED_OUTAGE = "CAUSED_OUTAGE"

class Provenance(BaseModel):
    file_path: str
    modality: Literal["code", "doc", "diagram", "config", "sql", "api_spec"]
    evidence_snippet: str = Field(description="Exact quote, code line, or visual description")
    authority_tier: int = Field(description="1=Code/SQL/Config, 2=API Spec, 3=Diagram/Wiki/Notes")

class EntityNode(BaseModel):
    id: str = Field(description="Normalized lowercase canonical identifier, e.g., 'user-service'")
    type: NodeType
    properties: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance

class RelationshipEdge(BaseModel):
    source_id: str
    target_id: str
    type: EdgeType
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)

class RiskFlag(BaseModel):
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    category: Literal["SECURITY", "CONTRADICTION", "STALE_DOC", "ORPHANED_DEP", "UNVERIFIED_ASSUMPTION"]
    description: str
    conflicting_sources: List[str]
    evidence: str

class ExtractionOutput(BaseModel):
    nodes: List[EntityNode]
    edges: List[RelationshipEdge]
    risks: List[RiskFlag]
```

### Step 2: Modality Router (`router.py`)
1. Walk the target monorepo directory (ignoring `.git`, `node_modules`, `__pycache__`, `.venv`).
2. Classify files into 5 buckets based on path and extension:
   * `CODE_CONFIG`: `.py`, `.js`, `.ts`, `Dockerfile`, `docker-compose.yml`, `.tf`, `requirements.txt`, `package.json` $\rightarrow$ Assign `authority_tier=1`.
   * `DB_SCHEMA`: `.sql`, migration files $\rightarrow$ Assign `authority_tier=1`.
   * `API_SPEC`: OpenAPI `.yaml`, `.json` $\rightarrow$ Assign `authority_tier=2`.
   * `DIAGRAM`: `.png`, `.svg` $\rightarrow$ Assign `authority_tier=3`.
   * `DOCS_NOTES`: `.md`, `.txt`, issue exports, release notes $\rightarrow$ Assign `authority_tier=3`.

### Step 3: Parallel Modality Extractors (`extractors.py`)
Implement `asyncio.Semaphore(10)` to extract data concurrently without hitting rate limits. All calls must enforce `response_format=ExtractionOutput`.
*   **`extract_from_code_and_sql(file_path, content)`**: Prompt the LLM to extract implemented routes, DB table definitions, library dependencies, and explicitly flag hardcoded secrets, SQL injection risks, weak crypto, or insecure authentication patterns.
*   **`extract_from_docs_and_specs(file_path, content)`**: Prompt the LLM to extract intended API contracts, documented DB schemas, service ownership, deprecation notices, and historical outage causes.
*   **`extract_from_diagram(file_path, image_bytes)`**: If `.svg`, pass both raw XML text and rendered image to the VLM. Prompt the VLM to extract visual boxes as `EntityNode` and directional arrows as `RelationshipEdge`.

### Step 4: Entity Resolution & Adversarial Judge (`validator.py`)
Do not pass the entire repository JSON into a single prompt. Implement a two-stage Map-Reduce reconciliation pipeline inspired by `MemGraphRAG` (*arXiv:2606.00610*):
1.  **Canonical Entity Clustering:** Group extracted `EntityNode` items across all files by lexical normalization and semantic similarity on `id` (e.g., merging `user-service`, `UserService`, and `users_api` into a shared local neighborhood).
2.  **Adversarial Verification Agent:** For each clustered entity and its 1-hop neighborhood, pass all associated multi-modal claims to a reasoning model:
    *   **Ground Truth Rule:** `authority_tier=1` (Code/SQL/Config) represents runtime reality. `authority_tier=3` (Wiki/Diagrams) represents intended or historical state.
    *   **Contradiction Detection:** If a Wiki or Diagram claims a relationship or schema field exists, but Code/SQL contradicts or omits it, emit a `CONTRADICTS` edge and a `STALE_DOC` or `CONTRADICTION` `RiskFlag` citing both file paths and exact snippets.
    *   **Diagram Conflict Detection:** If two diagrams show conflicting architectures for the same service, flag as `CONTRADICTION` and check release/meeting notes to determine which diagram represents legacy vs. target state.
    *   **False-Positive Connection Filtering:** If two unrelated projects reference a generic endpoint or port without shared deployment config or explicit cross-calls, assign `confidence < 0.3` and emit an `UNVERIFIED_ASSUMPTION` flag.

### Step 5: Pipeline Output (`main.py`)
*   Aggregate reconciled nodes, verified edges, and risk flags into `validated_context_graph.json`.
*   Generate an executive markdown summary `etl_audit_report.md` listing all discovered contradictions, broken diagram assumptions, and security risks for human and judge review.

---

## 3. Devil's Advocate: Architectural Trade-Offs & Limitations (35% Presentation Criteria)

Use this objective analysis during your presentation to demonstrate engineering maturity, explain why specific shortcuts were taken for the hackathon PoC, and prove how the system scales in production using peer-reviewed research.

### 1. 100% LLM/VLM Extraction vs. Deterministic AST Parsing
*   **PoC Decision (Pro):** Relying exclusively on LLMs/VLMs with Structured Outputs enables rapid development across Python, JS, SQL, YAML, and Markdown using a single unified pipeline without writing language-specific parsers.
*   **Devil's Advocate Critique (Con):** LLMs are probabilistic token predictors, not deterministic compilers. Using LLMs to parse source code incurs $O(N)$ token costs on every commit, struggles with large files, and frequently hallucinates or misses indirect structural call graphs.
*   **Peer-Reviewed Validation & Production Mitigation:** Zhu et al. (2024, cited in Baldwin & Ghanavati, 2026, *arXiv:2604.27713*) evaluated LLMs across eight KG construction tasks (`AutoKG`) and demonstrated that LLMs perform significantly better as reasoning/inference assistants than as zero-shot extractors. Furthermore, repository-level software engineering research (*arXiv:2503.21710*) proves that combining deterministic AST/structural graph analysis with LLM textual understanding significantly outperforms pure LLM approaches in identifying code dependencies and bug locations. **Forward View:** Replace LLM code extraction with Tree-sitter AST parsers and static analysis tools (e.g., Semgrep for security), reserving LLMs strictly for unstructured docs and conflict resolution.

### 2. Chunk/Clustered Extraction vs. Global Graph Consistency
*   **PoC Decision (Pro):** Extracting files in parallel and clustering entities locally by name prevents blowing up the LLM context window and keeps ingestion latency under hackathon time limits.
*   **Devil's Advocate Critique (Con):** Isolated file-level extraction lacks global repository memory. When independent LLM calls extract entities from scattered files, they create fragmented nodes, redundant relationships, and miss transitive contradictions (e.g., Service A calls Service B, which violates a security policy defined in Document C).
*   **Peer-Reviewed Validation & Production Mitigation:** Research on `MemGraphRAG` (*arXiv:2606.00610*) proves that isolated local extraction in standard GraphRAG pipelines causes three severe deficiencies: *thematic irrelevance, logical inconsistency, and structural fragmentation*—noting that up to 40% of unfiltered locally extracted triples act as noise that degrades downstream QA accuracy. Similarly, `SKG-Eval` (Li et al., 2026, *arXiv:2605.16650*) shows that LLM judges prompted on local contexts fail to surface cross-turn or cross-document logical contradictions without symbolic graph cascades. **Forward View:** Transition from batch map-reduce clustering to a stateful three-tier Global Memory architecture (`MemGraphRAG`: Ontology Layer, Fact Layer, Passage Layer) with incremental graph-write agents (`RAGA`, *arXiv:2605.17072*).

### 3. Heuristic Authority Hierarchy (`Code > Spec > Wiki`) vs. Policy Compliance
*   **PoC Decision (Pro):** Enforcing a strict rule that Tier 1 (Code/SQL) overrides Tier 3 (Wikis/Diagrams) gives the Judge Agent a deterministic rubric to automatically flag stale documentation.
*   **Devil's Advocate Critique (Con):** **The "Bug-as-Truth" Fallacy.** If a developer introduces an unauthorized breaking change, a regression, or an insecure pattern into the source code, treating `Code = Ground Truth` causes the Context Service to invalidate the intended architectural specification or security policy rather than flagging the code as non-compliant.
*   **Peer-Reviewed Validation & Production Mitigation:** In regulatory and architectural compliance research (`GraphCompliance`, Chung et al., 2025; Baldwin & Ghanavati, 2026, *arXiv:2604.27713*), normative documents (policies, ADRs, security specs) must be modeled as a formal *Policy Graph* (`subject-constraint-context-condition` tuples) and evaluated bidirectionally against the runtime *Implementation Graph*. **Forward View:** Separate `STALE_DOCUMENTATION` (where git commit timestamps and migration files prove the code intentionally evolved past an old wiki) from `ARCHITECTURAL_VIOLATION` (where newer code violates an active Architecture Decision Record or security policy).

### 4. Zero-Shot VLM Diagram Parsing vs. Spatial Grounding Limitations
*   **PoC Decision (Pro):** Passing `.png` and `.svg` architecture diagrams directly to a frontier VLM captures "dark data" and high-level system intent that text-only RAG pipelines completely ignore.
*   **Devil's Advocate Critique (Con):** Vision-Language Models exhibit high hallucination rates on dense technical schematics, frequently misattributing arrow directions, dropping edge labels, or failing to link visual callouts to tabular schemas.
*   **Peer-Reviewed Validation & Production Mitigation:** Recent benchmarking on complex engineering diagrams (`Enginuity`, Seefried et al., 2026, *arXiv:2606.03410*; Xiang et al., 2026) demonstrates that frontier VLMs fall below 60% accuracy on structured technical and scientific diagrams due to fine-grained spatial reasoning failures and domain-specific nomenclature. **Forward View:** Parse `.svg` files deterministically via XML DOM traversal before visual rendering, and use multi-vector visual patch embeddings (`ColPaLi`, Faysse et al., 2025; `GME`, Zhang et al., 2025, cited in *arXiv:2512.20626*) paired with human-in-the-loop confidence thresholds for rasterized `.png` diagrams.

---

## 4. Peer-Reviewed References
1. **Baldwin, W., & Ghanavati, S. (2026).** *Knowledge Graph Representations for LLM-Based Policy Compliance Reasoning.* arXiv preprint arXiv:2604.27713. (Citing Chung et al., 2025; Markowitz et al., 2025; Dai et al., 2025; Zhu et al., 2024).
2. **MemGraphRAG Authors. (2026).** *MemGraphRAG: Memory-based Multi-Agent System for GraphRAG.* arXiv preprint arXiv:2606.00610.
3. **Li, et al. (2026).** *SKG-Eval: Stateful Evaluation of Multi-Turn Dialogue via Incremental Symbolic Knowledge Graphs.* arXiv preprint arXiv:2605.16650.
4. **RAGA Authors. (2026).** *RAGA: Reading-And-Graph-building-Agent for Autonomous Knowledge Graph Construction.* arXiv preprint arXiv:2605.17072. (Citing Edge et al., 2024; Guo et al., 2025).
5. **Multimodal KG-RAG Authors. (2026).** *Multimodal Knowledge Graph-Based Retrieval Augmented Generation.* arXiv preprint arXiv:2512.20626v2. (Citing Li et al., 2025; Faysse et al., 2025; Zhang et al., 2025).
6. **Seefried, et al. (2026).** *Enginuity: A Dataset and Benchmark for Vision-Language Understanding of Engineering Diagrams.* arXiv preprint arXiv:2606.03410. (Citing Xiang et al., 2026).
7. **Repository-Aware Software Repair Authors. (2025).** *Enhancing Repository-Level Software Repair via Repository-Aware Knowledge Graphs.* arXiv preprint arXiv:2503.21710.