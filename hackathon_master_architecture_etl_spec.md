# Efficient AI Context Service: Complete System Design & Claude Code Specification

## 1. End-to-End System Architecture (2026 State-of-the-Art)

```
========================================================================================
LAYER 1: MULTI-MODAL AGENTIC ETL & VALIDATION PIPELINE
========================================================================================
[Scattered Monorepo Sources] (.py, .js, .sql, .yaml, .png, .svg, .md, Dockerfile, .tf)
         │
         ▼
[Modality Router & Authority Classifier]
  ├── Tier 1 (Runtime Truth): Code, SQL, Deployment Configs
  ├── Tier 2 (Contract Truth): OpenAPI / Event Specs
  └── Tier 3 (Intended/Historical): Diagrams, Wikis, Issue Notes
         │
         ▼
[Parallel Multi-Modal LLM/VLM Extraction] ──> Strict Pydantic Structured Outputs
         │
         ▼
[Adversarial Validation & Entity Resolution Engine]
  └── Emits: validated_context_graph.json (Nodes, Verified Edges, Provenance, RiskFlags)

========================================================================================
LAYER 2: HYBRID GRAPHRAG KNOWLEDGE REPRESENTATION & STORAGE
========================================================================================
[Dual-Index Ingestion Engine]
  ├── 1. Property Graph Store (NetworkX / Neo4j)
  │       ├── Typed Ontology Nodes & Multi-Hop Directed Edges
  │       └── Edge-Level Provenance & Conflict/Security Metadata
  └── 2. Semantic Vector Index (ChromaDB / FAISS)
          └── Embeddings of Unstructured Evidence Snippets & Node Descriptions
         │
         ▼
[Hybrid Subgraph Retriever]
  └── Vector Seed Lookup ──> K-Hop Ego-Graph Expansion ──> Relations-First JSON Serialization

========================================================================================
LAYER 3: CONTEXT DELIVERY GATEWAY (MCP SERVER & REST API)
========================================================================================
[Unified Context Query Engine]
  ├── FastMCP Server (stdio / SSE) ──> IDE Coding Assistants (Cursor, Claude Code, Copilot)
  └── FastAPI REST Gateway         ──> Human Engineers, CI/CD Gates, Web Dashboard
```

---

## 2. Layer 1 Specification: Agentic Multi-Modal ETL (`etl_pipeline/`)

### Directory Structure (Full Project)
```
context_service/
├── etl_pipeline/
│   ├── config.py          # Concurrency limits, model names, file extensions
│   ├── schema.py          # Strict Pydantic ontology & provenance schemas
│   ├── router.py          # Repository crawler and modality classifier
│   ├── extractors.py      # Async LLM/VLM extraction workers
│   ├── validator.py       # Entity resolution, conflict & security Judge Agent
│   └── run_etl.py         # Outputs validated_context_graph.json & etl_audit_report.md
├── knowledge_store/
│   ├── graph_db.py        # Property graph builder & K-hop traversal engine
│   ├── vector_db.py       # Semantic embedding store for unstructured passages
│   └── hybrid_retriever.py# Dual-path GraphRAG retrieval & relations-first serializer
└── delivery_gateway/
    ├── mcp_server.py      # FastMCP tools & resources for AI coding agents
    └── api_server.py      # FastAPI REST endpoints for humans & CI/CD
```

### Step 1.1: Ontology & Provenance Schema (`etl_pipeline/schema.py`)
Define strict Pydantic models for LLM Structured Outputs. Every extracted entity and edge MUST include source provenance.

```python
from enum import Enum
from typing import Literal, Dict, Any, List
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

### Step 1.2: Modality Router (`etl_pipeline/router.py`)
1. Walk the target monorepo directory (ignoring `.git`, `node_modules`, `__pycache__`, `.venv`).
2. Classify files into 5 buckets based on path and extension:
   * `CODE_CONFIG`: `.py`, `.js`, `.ts`, `Dockerfile`, `docker-compose.yml`, `.tf`, `requirements.txt`, `package.json` $\rightarrow$ Assign `authority_tier=1`.
   * `DB_SCHEMA`: `.sql`, migration files $\rightarrow$ Assign `authority_tier=1`.
   * `API_SPEC`: OpenAPI `.yaml`, `.json` $\rightarrow$ Assign `authority_tier=2`.
   * `DIAGRAM`: `.png`, `.svg` $\rightarrow$ Assign `authority_tier=3`.
   * `DOCS_NOTES`: `.md`, `.txt`, issue exports, release notes $\rightarrow$ Assign `authority_tier=3`.

### Step 1.3: Parallel Modality Extractors (`etl_pipeline/extractors.py`)
Implement `asyncio.Semaphore(10)` to extract data concurrently without hitting rate limits. All calls must enforce `response_format=ExtractionOutput`.
* **`extract_from_code_and_sql(file_path, content)`**: Prompt LLM to extract implemented routes, DB table definitions, library dependencies, and flag hardcoded secrets, SQL injection risks, weak crypto, or insecure authentication patterns.
* **`extract_from_docs_and_specs(file_path, content)`**: Prompt LLM to extract intended API contracts, documented DB schemas, service ownership, deprecation notices, and historical outage causes.
* **`extract_from_diagram(file_path, image_bytes)`**: If `.svg`, pass both raw XML text and rendered image to VLM. Prompt VLM to extract visual boxes as `EntityNode` and directional arrows as `RelationshipEdge`.

### Step 1.4: Entity Resolution & Adversarial Judge (`etl_pipeline/validator.py`)
Implement a two-stage Map-Reduce reconciliation pipeline inspired by `MemGraphRAG` (*arXiv:2606.00610*):
1. **Canonical Entity Clustering:** Group extracted `EntityNode` items across all files by lexical normalization and semantic similarity on `id`.
2. **Adversarial Verification Agent:** For each clustered entity and its 1-hop neighborhood, pass all associated multi-modal claims to a reasoning model:
   * **Ground Truth Rule:** `authority_tier=1` (Code/SQL/Config) represents runtime reality. `authority_tier=3` (Wiki/Diagrams) represents intended or historical state.
   * **Contradiction Detection:** If a Wiki or Diagram claims a relationship or schema field exists, but Code/SQL contradicts or omits it, emit a `CONTRADICTS` edge and a `STALE_DOC` or `CONTRADICTION` `RiskFlag` citing both file paths and exact snippets.
   * **False-Positive Filtering:** If two unrelated projects reference a generic endpoint/port without shared deployment config or explicit cross-calls, assign `confidence < 0.3` and emit an `UNVERIFIED_ASSUMPTION` flag.

---

## 3. Layer 2 Specification: Knowledge Representation & Hybrid GraphRAG (`knowledge_store/`)

This layer addresses the highest-weighted technical judging area (**Knowledge Representation: 25 Points**). It combines deterministic graph topology with vector semantics (`Hybrid GraphRAG`, *arXiv:2507.03608*).

### Step 2.1: Property Graph Store (`knowledge_store/graph_db.py`)
For hackathon speed and zero infrastructure overhead, use `NetworkX` (`nx.MultiDiGraph`) persisted to disk (or `Neo4j` if Docker is desired).
1. **Ingest Validated JSON:** Load `validated_context_graph.json`.
2. **Multi-Perspective Node Storage:** Each node stores merged properties across all modalities, plus an array of `provenance_records` and attached `risk_flags`.
3. **Typed Directed Edges:** Store relationships (`CALLS`, `DEPENDS_ON`, `READS_WRITES`, `CONTRADICTS`) with edge attributes: `confidence`, `authority_tier`, `file_path`, and `evidence_snippet`.
4. **Graph Traversal Methods:**
   * `get_blast_radius(entity_id, depth=2)`: Performs reverse breadth-first search (BFS) on `DEPENDS_ON`, `CALLS`, and `READS_WRITES` edges to find all upstream/downstream services impacted by modifying `entity_id`.
   * `get_entity_contradictions(entity_id)`: Returns all incident `CONTRADICTS` edges and `STALE_DOC` / `SECURITY` risk flags within 1 hop.
   * `find_shortest_dependency_path(source_id, target_id)`: Explains how two seemingly unrelated repositories or tables are connected.

### Step 2.2: Semantic Vector Index (`knowledge_store/vector_db.py`)
Use `ChromaDB` (in-memory/persistent local mode) or `FAISS` with dense embeddings.
1. **Index Granularity:** Embed every `EntityNode` summary, every `RiskFlag` description, and chunked passages from unstructured docs (meeting notes, incident reports, ADRs).
2. **Metadata Linking:** Every vector record MUST store `{"entity_id": node.id, "file_path": prov.file_path, "authority_tier": prov.authority_tier}` in its metadata payload so vector hits map directly back to Graph nodes.

### Step 2.3: Hybrid Subgraph Retriever & Serializer (`knowledge_store/hybrid_retriever.py`)
Implement the dual-retrieval pattern (`SubgraphRAG`, Li et al., 2025; `Hybrid GraphRAG`, *arXiv:2507.03608*):
1. **Step A (Semantic Anchor Lookup):** Given a natural language or coding query, query `vector_db` for the top-$K$ ($K=5$) matching entities or incident passages.
2. **Step B (Deterministic K-Hop Expansion):** Map the top-$K$ vector hits to their canonical `entity_id` in `graph_db` and extract the induced 2-hop ego-subgraph, explicitly prioritizing verified edges (`confidence >= 0.7`) while attaching any `CONTRADICTS` or `SECURITY` flags attached to those nodes.
3. **Step C (Relations-First Structured Serialization):** Format the retrieved subgraph for downstream LLMs using **JSON / Relations-First serialization** (`[Source] -> [EDGE_TYPE (Tier 1 Verified)] -> [Target] | Evidence: ...`) followed by explicit warnings for any `RiskFlag` items. Empirical research proves structured JSON and relations-first ordering significantly outperform natural language prose serialization on LLM multi-hop reasoning tasks (Markowitz et al., 2025; Dai et al., 2025, cited in *arXiv:2604.27713*).

---

## 4. Layer 3 Specification: Context Service Delivery Gateway (`delivery_gateway/`)

This layer addresses **Context Service Connection & Usage (15 Points)** by exposing the Hybrid GraphRAG engine simultaneously to Agentic Coding Assistants via MCP and humans via REST.

### Step 3.1: Model Context Protocol Server (`delivery_gateway/mcp_server.py`)
Use the official Python `mcp` SDK (`FastMCP`) to expose deterministic tools and read-only resources to IDEs (Cursor, Windsurf, Claude Code, VS Code).

```python
from mcp.server.fastmcp import FastMCP
from knowledge_store.hybrid_retriever import HybridRetriever

mcp = FastMCP("Enterprise-Engineering-Context-Service")
retriever = HybridRetriever.load_from_disk()

@mcp.tool()
def query_engineering_context(query: str, include_unverified: bool = False) -> str:
    """
    Retrieves evidence-backed architectural context, service dependencies,
    contradictions, and security warnings for a given engineering question or task.
    """
    return retriever.retrieve_serialized_context(query, include_unverified=include_unverified)

@mcp.tool()
def analyze_change_impact(component_id: str, max_hops: int = 2) -> dict:
    """
    Calculates the blast radius of changing a service, API endpoint, or database table.
    Returns dependent repositories, callers, stale docs, and active security risks.
    """
    return retriever.graph_db.get_blast_radius(component_id, depth=max_hops)

@mcp.tool()
def validate_code_snippet_context(service_id: str, proposed_dependencies: list[str]) -> dict:
    """
    Pre-flight check for AI coding assistants: verifies whether proposed API calls,
    DB tables, or libraries match current Tier-1 runtime truth and flags security anti-patterns.
    """
    return retriever.verify_implementation_plan(service_id, proposed_dependencies)

@mcp.resource("context://audit/contradictions-and-risks")
def get_global_risk_report() -> str:
    """Returns the full markdown report of repository contradictions, stale wikis, and security flags."""
    with open("etl_audit_report.md", "r") as f:
        return f.read()

if __name__ == "__main__":
    mcp.run()
```

### Step 3.2: REST API & Interactive Graph Viewer (`delivery_gateway/api_server.py`)
Build a lightweight `FastAPI` wrapper over `HybridRetriever` so judges can test the service in a browser:
* `GET /graph/topology`: Returns the full graph in Cytoscape.js / D3.js JSON format with color-coded nodes (Green = Verified, Orange = Stale Doc Conflict, Red = Security Risk).
* `POST /context/query`: Accepts a natural language question and returns the structured subgraph, source citations, and LLM-synthesized answer.
* `GET /context/blast-radius/{entity_id}`: Returns upstream/downstream impact analysis.

---

## 5. Comprehensive Devil's Advocate: Architectural Trade-Offs (All 3 Layers)

This section directly targets **Presentation and Explanation (35 Points)** and **Forward View (10 Points)**. Presenting these critiques proactively proves deep architectural mastery.

### Layer 1 Trade-Offs: Ingestion & Extraction

| Design Decision | PoC Advantage (Why we built it this way) | Devil's Advocate Critique (Why it breaks at enterprise scale) | Peer-Reviewed Validation & Production Mitigation |
| :--- | :--- | :--- | :--- |
| **100% LLM/VLM Extraction** vs. Deterministic AST Parsing | **Zero-Parser Velocity:** Handles Python, JS, SQL, YAML, and Markdown via a single unified prompt pipeline. | **Hallucination & $O(N)$ Token Cost:** LLMs are probabilistic and miss indirect call graphs or hallucinate dependencies in large files. | Zhu et al. (2024, cited in *arXiv:2604.27713*) showed LLMs excel at reasoning over graphs but underperform as raw zero-shot extractors (`AutoKG`). Software repair research (*arXiv:2503.21710*) proves AST + LLM hybrids beat pure LLMs. **Mitigation:** Use Tree-sitter ASTs and Semgrep for Tier-1 code, reserving LLMs for unstructured docs. |
| **Local Entity Clustering** vs. Global Stateful Memory | **Context Window Safety:** Map-reduce clustering prevents context overflow and allows parallel extraction. | **Structural Fragmentation:** Independent file extraction creates duplicate nodes and misses cross-document transitive conflicts. | `MemGraphRAG` (*arXiv:2606.00610*) demonstrates that isolated local extraction causes up to 40% triple noise and logical inconsistency. `SKG-Eval` (Li et al., 2026, *arXiv:2605.16650*) shows local LLM judges miss multi-hop contradictions. **Mitigation:** Adopt stateful incremental graph-building agents (`RAGA`, *arXiv:2605.17072*). |
| **Heuristic Authority (`Code > Spec > Wiki`)** vs. Bidirectional Policy Alignment | **Deterministic Conflict Rule:** Gives the Judge Agent a clear heuristic to flag outdated documentation. | **The "Bug-as-Truth" Fallacy:** If a developer commits a bug or security regression in code, assuming `Code = Truth` invalidates the intended architecture spec. | `GraphCompliance` (Chung et al., 2025; Baldwin & Ghanavati, 2026, *arXiv:2604.27713*) models normative docs as a formal *Policy Graph* evaluated against the *Context Graph*. **Mitigation:** Distinguish `STALE_DOC` (verified via git blame/timestamps) from `POLICY_VIOLATION` (code violating an active ADR). |
| **Zero-Shot VLM Diagram Parsing** | **Unlocks Dark Data:** Extracts architectural relationships from `.png`/`.svg` files ignored by text RAG. | **Spatial Blindness:** VLMs misread arrow directions and edge labels in dense technical schematics. | The `Enginuity` benchmark (Seefried et al., 2026, *arXiv:2606.03410*) proves frontier VLMs score `<60%` on dense engineering diagrams. **Mitigation:** Parse `.svg` XML DOM directly and use multi-vector patch retrieval (`ColPaLi`, Faysse et al., 2025, cited in *arXiv:2512.20626*). |

### Layer 2 Trade-Offs: Knowledge Representation & Storage

| Design Decision | PoC Advantage (Why we built it this way) | Devil's Advocate Critique (Why it breaks at enterprise scale) | Peer-Reviewed Validation & Production Mitigation |
| :--- | :--- | :--- | :--- |
| **Hybrid GraphRAG (Graph + Vector)** vs. Pure Vector RAG | **Multi-Hop Precision + Semantic Recall:** Captures explicit repository dependencies while retaining semantic search over incident notes. | **Dual-Index Synchronization & Latency Overhead:** Maintaining consistency across both a graph DB and vector index doubles storage complexity and increases query latency. | Benchmarking of Vector, Graph, and Hybrid RAG (*arXiv:2507.03608*) proves Hybrid GraphRAG improves factual correctness by **8%** and GraphRAG improves context relevance by **7–11%** over vector RAG on complex system dependency tasks. **Mitigation:** Use unified multi-model storage or event-driven CDC (Change Data Capture) via Git webhooks to update only modified subgraphs. |
| **In-Memory Property Graph (`NetworkX`)** vs. Distributed Native Graph DB (`Neo4j` / `Memgraph`) | **Zero Deployment Friction:** Runs locally in Python without container orchestration or database tuning during the hackathon. | **Memory Ceiling & Concurrency Bottlenecks:** In-memory Python graphs cannot scale to 10,000+ enterprise microservices or handle concurrent transactional writes. | Enterprise GraphRAG deployments (*arXiv:2507.03608*) rely on native Cypher-backed graph stores (`Neo4j`) for indexed subgraph pattern matching. **Mitigation:** Abstract `graph_db.py` behind a repository interface so `NetworkX` can be swapped for `Neo4j` in production. |
| **Strict Predefined Ontology** (`NodeType` / `EdgeType` Enums) vs. Schema-Free OpenIE Triples | **High Query Determinism:** Constrains LLM extraction so downstream tools can reliably run `get_blast_radius()` on known edge types. | **Ontology Brittleness (Out-of-Schema Loss):** Novel engineering concepts (e.g., a Kafka dead-letter queue or feature flag) get forced into awkward node types or dropped entirely. | Baldwin & Ghanavati (2026, *arXiv:2604.27713*) highlight that while rigid schemas prevent extraction drift, they struggle with unseen domain abstractions unless paired with semi-automated ontology evolution (`MemGraphRAG` Ontology Layer, *arXiv:2606.00610*). **Mitigation:** Allow a fallback `CUSTOM_CONCEPT` node type that triggers periodic human-in-the-loop ontology promotion. |

### Layer 3 Trade-Offs: Context Service Connection & MCP Delivery

| Design Decision | PoC Advantage (Why we built it this way) | Devil's Advocate Critique (Why it breaks at enterprise scale) | Peer-Reviewed Validation & Production Mitigation |
| :--- | :--- | :--- | :--- |
| **Model Context Protocol (MCP) Integration** vs. Custom IDE Plugins | **Universal Agent Interoperability:** Any MCP-compliant coding assistant (Claude Code, Cursor, Copilot) can immediately invoke our Context Service without custom glue code. | **Protocol-Level Security & Prompt Injection Amplification:** If an attacker or developer places a malicious prompt inside a GitHub issue, wiki, or commit message, the MCP server will feed that poisoned context directly into the developer's IDE agent with full tool privileges. | A landmark security analysis of the MCP v1.0 specification (*arXiv:2601.17549*) proved across 847 attack scenarios that MCP's architecture (lack of capability attestation, conflated context provenance, and implicit cross-server trust) **amplifies prompt injection and cross-server attack success rates by 23–41%** (reaching 52.8% attack success) compared to non-MCP APIs (see also Li & Gao, *DSN 2026 / arXiv:2510.16558*). **Mitigation:** Implement `AMCP`-style message provenance tagging (*arXiv:2601.17549*), sanitize untrusted Tier-3 text (issues/wikis) before MCP tool return, and enforce strict read-only capability scoping. |
| **Subgraph Serialization into IDE Context** vs. Autonomous Agentic Traversal | **Predictable Latency (Single-Roundtrip):** Returning a pre-serialized 2-hop JSON subgraph in one MCP tool call gives the coding assistant immediate context in `<200ms`. | **Context Bloat on Hub Nodes:** If a developer queries a core shared module (e.g., `auth-library` connected to 200 endpoints), a 2-hop subgraph explodes the IDE's token window and degrades code generation quality. | Markowitz et al. (2025) and Dai et al. (2025, cited in *arXiv:2604.27713*) show that while JSON/relations-first serialization maximizes accuracy on compact subgraphs, unpruned hub expansion degrades LLM reasoning. **Mitigation:** Implement degree-aware Personalized PageRank (PPR) pruning (`SubgraphRAG`, Li et al., 2025) to cap returned subgraphs at the top-30 most relevant edges. |

---

## 6. Peer-Reviewed References (For Presentation Slides)

1. **Baldwin, W., & Ghanavati, S. (2026).** *Knowledge Graph Representations for LLM-Based Policy Compliance Reasoning.* arXiv:2604.27713. (Citing Chung et al., 2025; Markowitz et al., 2025; Dai et al., 2025; Zhu et al., 2024).
2. **MemGraphRAG Authors. (2026).** *MemGraphRAG: Memory-based Multi-Agent System for GraphRAG.* arXiv:2606.00610.
3. **Benchmarking Hybrid GraphRAG Authors. (2025).** *Benchmarking Vector, Graph and Hybrid Retrieval Augmented Generation (RAG) Architectures.* arXiv:2507.03608.
4. **Breaking the Protocol Authors. (2026).** *Breaking the Protocol: Security Analysis of the Model Context Protocol Specification and Prompt Injection Vulnerabilities in Tool-Integrated LLM Agents.* arXiv:2601.17549.
5. **Li, X., & Gao, X. (2026).** *A First Look at the Security Issues in the Model Context Protocol Ecosystem.* Accepted to IEEE/IFIP DSN 2026, arXiv:2510.16558.
6. **Li, et al. (2026).** *SKG-Eval: Stateful Evaluation of Multi-Turn Dialogue via Incremental Symbolic Knowledge Graphs.* arXiv:2605.16650.
7. **RAGA Authors. (2026).** *RAGA: Reading-And-Graph-building-Agent for Autonomous Knowledge Graph Construction.* arXiv:2605.17072.
8. **Multimodal KG-RAG Authors. (2026).** *Multimodal Knowledge Graph-Based Retrieval Augmented Generation.* arXiv:2512.20626v2. (Citing Li et al., 2025; Faysse et al., 2025).
9. **Seefried, et al. (2026).** *Enginuity: A Dataset and Benchmark for Vision-Language Understanding of Engineering Diagrams.* arXiv:2606.03410.
10. **Repository-Aware Software Repair Authors. (2025).** *Enhancing Repository-Level Software Repair via Repository-Aware Knowledge Graphs.* arXiv:2503.21710.