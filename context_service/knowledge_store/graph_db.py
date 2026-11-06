"""GraphRepository: the storage abstraction behind the retriever.

Two interchangeable implementations with identical return shapes:
  * Neo4jGraph   (knowledge_store/neo4j_client.py) - production path: Cypher + native vector & full-text indexes
  * MemoryGraph  (this file)                       - zero-infra path for tests/offline, networkx over the ETL JSON
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from difflib import SequenceMatcher
from typing import Iterable, Protocol

import networkx as nx

import config
from etl_pipeline.schema import IMPACT_EDGES, ContextGraph
from knowledge_store import vector_db as V

IMPACT = {e.value for e in IMPACT_EDGES}
TRUSTED = {"VERIFIED", "DOCUMENTED"}
PATH_EDGES = {"CALLS", "DEPENDS_ON", "READS_WRITES", "EXPOSES", "HAS_COLUMN", "DEPLOYED_AS", "AFFECTS", "DECIDES"}
RECORD_RE = re.compile(r"\b(ADR-\d{3}|INC-20\d\d-\d\d-\d\d|[A-Z]{2,7}-\d{1,4})\b")


def name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", (text or "").lower()))


def claim_view(c: dict) -> dict:
    p = c["provenance"]
    return {"id": c["id"], "subject": c["subject"], "predicate": c["predicate"], "object": c["object"],
            "mode": c["mode"], "status": c.get("status"), "superseded_by": c.get("superseded_by"),
            "file_path": p["file_path"], "line_start": p.get("line_start"), "tier": p["tier"],
            "date": p.get("effective_date"), "kind": p.get("doc_kind"), "snippet": p["snippet"],
            "extractor": p.get("extractor")}


class GraphRepository(Protocol):
    def entity_index(self) -> dict[str, dict]: ...
    def get_entity(self, eid: str) -> dict | None: ...
    def edges_of(self, ids: Iterable[str], direction: str = "both", with_evidence: bool = True) -> list[dict]: ...
    def reverse_impact(self, eid: str, depth: int) -> list[dict]: ...
    def shortest_path(self, a: str, b: str) -> list[dict]: ...
    def risks(self, entity_ids: Iterable[str] | None = None, categories: Iterable[str] | None = None) -> list[dict]: ...
    def claims_for(self, eid: str) -> list[dict]: ...
    def search(self, query: str, k: int = 8) -> list[tuple[str, float]]: ...
    def chunk(self, cid: str) -> dict | None: ...
    def export(self) -> dict: ...


def resolve_name(text: str, index: dict[str, dict], prefer_live: bool = True) -> str | None:
    """Map a user/agent-supplied component name to a canonical entity id."""
    if not text:
        return None
    text = text.strip()
    if text in index:
        return text
    m = RECORD_RE.search(text)
    if m:
        for p in ("decision:", "issue:", "incident:"):
            if f"{p}{m.group(1)}" in index:
                return f"{p}{m.group(1)}"
    k = name_key(text.split(":", 1)[1] if ":" in text and text.split(":", 1)[0].isalpha() else text)
    best, best_id = 0.0, None
    type_rank = {"SERVICE": 0, "FRONTEND_APP": 1, "TABLE": 2, "COLUMN": 3, "TEAM": 4, "DATASTORE": 5, "LIBRARY": 6}
    for eid, e in index.items():
        if prefer_live and e.get("scope") != "live":
            continue
        names = [e["name"], eid.split(":", 1)[-1], *e.get("aliases", [])]
        if e["type"] in ("SERVICE", "FRONTEND_APP"):
            base = eid.split(":", 1)[1]
            names += [base.replace("-service", "")]
        for n in names:
            nk = name_key(n)
            if not nk:
                continue
            if nk == k:
                score = 2.0 - type_rank.get(e["type"], 9) * 0.01
            else:
                score = SequenceMatcher(None, k, nk).ratio()
                if len(k) >= 4 and (nk.endswith(k) or k in nk):
                    score = max(score, 0.9 - type_rank.get(e["type"], 9) * 0.01)
            if score > best:
                best, best_id = score, eid
    if best >= 0.85:
        return best_id
    # "users table" / "the inventory service" -> strip generic words and retry once
    stripped = re.sub(r"(?i)\b(the|table|column|service|svc|endpoint|api|db|database|component|repo)\b", " ", text).strip()
    if stripped and stripped != text and len(stripped) >= 3:
        hint = re.search(r"(?i)\b(table|column)\b", text)
        if hint:
            want = "TABLE" if hint.group(1).lower() == "table" else "COLUMN"
            k2 = name_key(stripped)
            for eid, e in index.items():
                if e["type"] == want and e.get("scope") == "live" and name_key(eid.split(".")[-1]) == k2:
                    return eid
        return resolve_name(stripped, index, prefer_live)
    return None


class MemoryGraph:
    def __init__(self, graph_json=None):
        data = json.loads((graph_json or config.GRAPH_JSON).read_text())
        self.g = ContextGraph.model_validate(data)
        self.raw = data
        self.entities = {e["id"]: e for e in data["entities"]}
        self.claims = {c["id"]: c for c in data["claims"]}
        self.edges = data["edges"]
        self.out = defaultdict(list)
        self.inn = defaultdict(list)
        for e in self.edges:
            self.out[e["source"]].append(e)
            self.inn[e["target"]].append(e)
        self._risks = data["risks"]
        self.chunks = {c["id"]: c for c in V.load_chunks()}
        self.emb = V.load_embeddings()
        docs = {f"entity::{eid}": f"{eid} {e['name']} {' '.join(e.get('aliases', []))} {e.get('summary', '')}"
                for eid, e in self.entities.items()}
        docs |= {f"risk::{r['id']}": f"{r['title']} {r['description']} {' '.join(r['entities'])}" for r in self._risks}
        docs |= {f"chunk::{cid}": c["text"] for cid, c in self.chunks.items()}
        self.bm25 = V.BM25(docs)
        self.nx = nx.MultiDiGraph()
        for e in self.edges:
            self.nx.add_edge(e["source"], e["target"], key=e["type"], **{k: v for k, v in e.items() if k != "claim_ids"})

    # ------------------------------------------------------------------ basics
    def entity_index(self) -> dict[str, dict]:
        return {k: {"id": k, "name": v["name"], "type": v["type"], "aliases": v.get("aliases", []),
                    "scope": v.get("scope", "live")} for k, v in self.entities.items()}

    def get_entity(self, eid: str) -> dict | None:
        return self.entities.get(eid)

    def _edge_view(self, e: dict, with_evidence: bool) -> dict:
        v = {k: e[k] for k in ("source", "type", "target", "status", "confidence", "best_tier", "latest_date")}
        if with_evidence:
            v["evidence"] = sorted((claim_view(self.claims[i]) for i in e["claim_ids"] if i in self.claims),
                                   key=lambda c: (c["tier"], c["date"] or ""))
        return v

    def edges_of(self, ids, direction="both", with_evidence=True) -> list[dict]:
        out, seen = [], set()
        for i in ids:
            pool = (self.out[i] if direction in ("out", "both") else []) + (self.inn[i] if direction in ("in", "both") else [])
            for e in pool:
                key = (e["source"], e["type"], e["target"])
                if key not in seen:
                    seen.add(key)
                    out.append(self._edge_view(e, with_evidence))
        return out

    def reverse_impact(self, eid: str, depth: int) -> list[dict]:
        """Who is affected if `eid` changes: walk impact edges backwards (callers, owners of tables, ...)."""
        seen = {eid: 0}
        result = []
        q = deque([(eid, 0)])
        while q:
            node, d = q.popleft()
            if d >= depth:
                continue
            for e in self.inn[node]:
                if e["type"] not in IMPACT or e["status"] in ("SUPERSEDED", "REJECTED"):
                    continue
                src = e["source"]
                if src not in seen:
                    seen[src] = d + 1
                    result.append({"entity": src, "hops": d + 1, "via": self._edge_view(e, True)})
                    q.append((src, d + 1))
        return result

    def shortest_path(self, a: str, b: str) -> list[dict]:
        und = nx.Graph()
        for e in self.edges:
            if e["status"] in TRUSTED | {"ASSERTED"} and e["type"] in PATH_EDGES:
                w = 1.0 if e["status"] in TRUSTED else 3.0
                if not und.has_edge(e["source"], e["target"]) or und[e["source"]][e["target"]]["weight"] > w:
                    und.add_edge(e["source"], e["target"], weight=w, edge=e)
        try:
            nodes = nx.shortest_path(und, a, b, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
        return [self._edge_view(und[x][y]["edge"], True) for x, y in zip(nodes, nodes[1:])]

    def risks(self, entity_ids=None, categories=None) -> list[dict]:
        ids = set(entity_ids) if entity_ids is not None else None
        cats = set(categories) if categories else None
        return [r for r in self._risks if (ids is None or ids & set(r["entities"])) and
                (cats is None or r["category"] in cats)]

    def claims_for(self, eid: str) -> list[dict]:
        return sorted((claim_view(c) for c in self.claims.values() if c["subject"] == eid or c["object"] == eid),
                      key=lambda c: (c["date"] or "0000", c["tier"]))

    def chunk(self, cid: str) -> dict | None:
        return self.chunks.get(cid)

    def search(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        lex = self.bm25.search(query, k=25)
        vec: list[tuple[str, float]] = []
        if self.emb:
            qv = V.embed_query(query)
            if qv:
                vec = sorted(((key, V.cosine(qv, v)) for key, v in self.emb.items()), key=lambda x: -x[1])[:25]
        exact = []
        for m in RECORD_RE.finditer(query):
            for p in ("decision:", "issue:", "incident:"):
                if f"{p}{m.group(1)}" in self.entities:
                    exact.append((f"entity::{p}{m.group(1)}", 1.0))
        hit = resolve_name(query, self.entity_index())
        for tok in re.findall(r"[A-Za-z][\w.-]{2,}", query):
            h = resolve_name(tok, self.entity_index())
            if h and name_key(tok) in {name_key(n) for n in [self.entities[h]["name"], h.split(':', 1)[-1],
                                                               *self.entities[h].get("aliases", [])]}:
                exact.append((f"entity::{h}", 1.0))
        if hit:
            exact.append((f"entity::{hit}", 1.0))
        return V.rrf(exact, lex, vec, weights=[2.0, 1.0, 1.2])[:k]

    def export(self) -> dict:
        return {"entities": list(self.entities.values()),
                "edges": [{k: v for k, v in e.items()} for e in self.edges], "risks": self._risks}


_REPO: GraphRepository | None = None


def get_repository() -> GraphRepository:
    global _REPO
    if _REPO is None:
        if config.GRAPH_BACKEND == "neo4j":
            from knowledge_store.neo4j_client import Neo4jGraph
            _REPO = Neo4jGraph()
        else:
            _REPO = MemoryGraph()
    return _REPO
