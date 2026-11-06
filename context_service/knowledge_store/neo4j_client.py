"""Neo4j storage: schema bootstrap, incremental loader and the Cypher-backed GraphRepository.

Graph model
  (:Entity:<NodeType> {id, name, type, scope, aliases, summary, props, grounded_tier, embedding})
  (:Source {path, kind, tier, modality, date, owner, status, record_id, sha256})
  (:Claim {id, predicate, mode, status, snippet, file_path, line_start, tier, date, extractor})
      -[:SUBJECT]->(:Entity)  -[:OBJECT]->(:Entity)  -[:FROM]->(:Source)
  (:Entity)-[:CALLS|EXPOSES|...{status, confidence, claim_ids, best_tier, latest_date}]->(:Entity)   (aggregated)
  (:Risk {id, category, severity, ...})-[:CONCERNS]->(:Entity), -[:EVIDENCED_BY]->(:Claim)
  (:Chunk {id, text, file_path, tier, date, embedding})-[:PART_OF]->(:Source), -[:ABOUT]->(:Entity)
Indexes: uniqueness constraints, Lucene full-text (entity/chunk/risk) and native vector indexes (cosine).
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from typing import Iterable

from neo4j import GraphDatabase

import config
from etl_pipeline.schema import IMPACT_EDGES, ContextGraph, EdgeType, NodeType
from knowledge_store import vector_db as V
from knowledge_store.graph_db import PATH_EDGES, RECORD_RE, name_key, resolve_name

log = logging.getLogger("neo4j")
REL_TYPES = [e.value for e in EdgeType]
_driver = None


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
    return _driver


def run(query: str, **params) -> list[dict]:
    with driver().session(database=config.NEO4J_DATABASE) as s:
        return [r.data() for r in s.run(query, **params)]


def wait_for_neo4j(timeout_s: int = 90) -> None:
    t0 = time.time()
    while True:
        try:
            driver().verify_connectivity()
            return
        except Exception as e:  # noqa: BLE001
            if time.time() - t0 > timeout_s:
                raise RuntimeError(f"Neo4j not reachable at {config.NEO4J_URI}: {e}") from e
            time.sleep(2)


def bootstrap_schema() -> None:
    dim = config.EMBED_DIM
    stmts = [
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT source_path IF NOT EXISTS FOR (n:Source) REQUIRE n.path IS UNIQUE",
        "CREATE CONSTRAINT risk_id IF NOT EXISTS FOR (n:Risk) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
        "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.aliases_text, n.summary, n.id]",
        "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (n:Chunk) ON EACH [n.text]",
        "CREATE FULLTEXT INDEX risk_text IF NOT EXISTS FOR (n:Risk) ON EACH [n.title, n.description, n.entities_text]",
    ]
    for label, name in (("Entity", "entity_emb"), ("Chunk", "chunk_emb"), ("Risk", "risk_emb")):
        stmts.append(f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) OPTIONS "
                     f"{{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}")
    for s in stmts:
        run(s)


def _batches(rows: list, n: int = 500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def load_graph(g: ContextGraph) -> dict:
    """Incremental load: claims/chunks of changed or removed sources are replaced; aggregates are rebuilt."""
    wait_for_neo4j()
    bootstrap_schema()
    sem = {"chunks_file": str(V.CHUNKS_JSON), "embeddings_file": str(V.EMB_JSON)}
    emb = V.load_embeddings()
    chunks = V.load_chunks()

    previous = {r["path"]: r["sha"] for r in run("MATCH (s:Source) RETURN s.path AS path, s.sha256 AS sha")}
    current = {s.path: s.sha256 for s in g.sources}
    changed = [p for p, sha in current.items() if previous.get(p) != sha]
    removed = [p for p in previous if p not in current]
    run("MATCH (c:Claim)-[:FROM]->(s:Source) WHERE s.path IN $paths DETACH DELETE c", paths=changed + removed)
    run("MATCH (c:Chunk)-[:PART_OF]->(s:Source) WHERE s.path IN $paths DETACH DELETE c", paths=changed + removed)
    run("MATCH (s:Source) WHERE s.path IN $paths DETACH DELETE s", paths=removed)
    # extraction can change without the file changing (new prompt / model / judge verdict):
    # drop every claim, chunk and entity that is not part of this build
    stale = run("""MATCH (c:Claim) WHERE NOT c.id IN $ids WITH c, c.id AS id DETACH DELETE c RETURN count(id) AS n""",
                ids=[c.id for c in g.claims])[0]["n"]
    run("MATCH (c:Chunk) WHERE NOT c.id IN $ids DETACH DELETE c", ids=[c["id"] for c in chunks])
    run("MATCH (e:Entity) WHERE NOT e.id IN $ids DETACH DELETE e", ids=[e.id for e in g.entities])
    # aggregates & risks are derived data -> rebuilt every load (cheap, keeps them consistent with claims)
    run("MATCH (:Entity)-[r]->(:Entity) DELETE r")
    run("MATCH (r:Risk) DETACH DELETE r")

    run("""UNWIND $rows AS r MERGE (s:Source {path: r.path}) SET s += r""",
        rows=[s.model_dump() for s in g.sources])

    by_type = defaultdict(list)
    for e in g.entities:
        by_type[e.type.value].append({
            "id": e.id, "name": e.name, "type": e.type.value, "scope": e.scope, "aliases": e.aliases,
            "aliases_text": " ".join(e.aliases), "summary": e.summary, "grounded_tier": e.grounded_tier,
            "props": json.dumps(e.properties, default=str), "status": str(e.properties.get("status") or ""),
            "path": e.properties.get("path"), "date": e.properties.get("date"),
            "embedding": emb.get(f"entity::{e.id}")})
    for t, rows in by_type.items():
        assert t in NodeType.__members__
        for b in _batches(rows):
            run(f"""UNWIND $rows AS r MERGE (n:Entity {{id: r.id}})
                    SET n:{t}, n.name=r.name, n.type=r.type, n.scope=r.scope, n.aliases=r.aliases,
                        n.aliases_text=r.aliases_text, n.summary=r.summary, n.grounded_tier=r.grounded_tier,
                        n.props=r.props, n.status=r.status, n.path=r.path, n.date=r.date
                    WITH n, r WHERE r.embedding IS NOT NULL SET n.embedding = r.embedding""", rows=b)

    claim_rows = [{"id": c.id, "predicate": c.predicate.value, "mode": c.mode, "status": c.status,
                   "superseded_by": c.superseded_by, "snippet": c.provenance.snippet,
                   "file_path": c.provenance.file_path, "line_start": c.provenance.line_start,
                   "line_end": c.provenance.line_end, "tier": c.provenance.tier,
                   "date": c.provenance.effective_date, "kind": c.provenance.doc_kind,
                   "extractor": c.provenance.extractor, "modality": c.provenance.modality,
                   "props": json.dumps(c.properties, default=str), "subject": c.subject, "object": c.object}
                  for c in g.claims]
    for b in _batches(claim_rows):
        run("""UNWIND $rows AS r
               MERGE (c:Claim {id: r.id})
               SET c.predicate=r.predicate, c.mode=r.mode, c.status=r.status, c.superseded_by=r.superseded_by,
                   c.snippet=r.snippet, c.file_path=r.file_path, c.line_start=r.line_start, c.line_end=r.line_end,
                   c.tier=r.tier, c.date=r.date, c.kind=r.kind, c.extractor=r.extractor, c.modality=r.modality,
                   c.props=r.props, c.subject=r.subject, c.object=r.object
               WITH c, r
               MATCH (s:Entity {id: r.subject}), (o:Entity {id: r.object}), (src:Source {path: r.file_path})
               MERGE (c)-[:SUBJECT]->(s) MERGE (c)-[:OBJECT]->(o) MERGE (c)-[:FROM]->(src)""", rows=b)

    edges_by_type = defaultdict(list)
    for e in g.edges:
        edges_by_type[e.type.value].append({"source": e.source, "target": e.target, "status": e.status,
                                            "confidence": e.confidence, "claim_ids": e.claim_ids,
                                            "best_tier": e.best_tier, "latest_date": e.latest_date})
    for t, rows in edges_by_type.items():
        assert t in REL_TYPES
        for b in _batches(rows):
            run(f"""UNWIND $rows AS r MATCH (a:Entity {{id: r.source}}), (b:Entity {{id: r.target}})
                    MERGE (a)-[x:{t}]->(b)
                    SET x.status=r.status, x.confidence=r.confidence, x.claim_ids=r.claim_ids,
                        x.best_tier=r.best_tier, x.latest_date=r.latest_date""", rows=b)

    risk_rows = [{**{k: v for k, v in r.model_dump().items() if k not in {"evidence", "entities"}},
                  "entities": r.entities, "entities_text": " ".join(r.entities),
                  "evidence": json.dumps([e.model_dump() for e in r.evidence]),
                  "claim_ids": [e.claim_id for e in r.evidence if e.claim_id],
                  "files": sorted({e.file_path for e in r.evidence}),
                  "embedding": emb.get(f"risk::{r.id}")} for r in g.risks]
    run("""UNWIND $rows AS r MERGE (k:Risk {id: r.id})
           SET k.category=r.category, k.severity=r.severity, k.title=r.title, k.description=r.description,
               k.entities=r.entities, k.entities_text=r.entities_text, k.evidence=r.evidence,
               k.current_truth=r.current_truth, k.guidance_for_codegen=r.guidance_for_codegen,
               k.detected_by=r.detected_by, k.confidence=r.confidence
           WITH k, r
           FOREACH (_ IN CASE WHEN r.embedding IS NULL THEN [] ELSE [1] END | SET k.embedding = r.embedding)
           WITH k, r
           CALL (k, r) { UNWIND r.entities AS eid MATCH (e:Entity {id: eid}) MERGE (k)-[:CONCERNS]->(e) }
           CALL (k, r) { UNWIND r.claim_ids AS cid MATCH (c:Claim {id: cid}) MERGE (k)-[:EVIDENCED_BY]->(c) }
           CALL (k, r) { UNWIND r.files AS f MATCH (s:Source {path: f}) MERGE (k)-[:CITES]->(s) }""",
        rows=risk_rows)

    chunk_rows = [{**{k: c[k] for k in ("id", "file_path", "line_start", "line_end", "tier", "date", "kind", "scope",
                                         "text")}, "about": c["about"], "embedding": emb.get(f"chunk::{c['id']}")}
                  for c in chunks]
    for b in _batches(chunk_rows):
        run("""UNWIND $rows AS r MERGE (c:Chunk {id: r.id})
               SET c.file_path=r.file_path, c.line_start=r.line_start, c.line_end=r.line_end, c.tier=r.tier,
                   c.date=r.date, c.kind=r.kind, c.scope=r.scope, c.text=r.text, c.about=r.about
               FOREACH (_ IN CASE WHEN r.embedding IS NULL THEN [] ELSE [1] END | SET c.embedding = r.embedding)
               WITH c, r MATCH (s:Source {path: r.file_path}) MERGE (c)-[:PART_OF]->(s)
               WITH c, r UNWIND r.about AS eid MATCH (e:Entity {id: eid}) MERGE (c)-[:ABOUT]->(e)""", rows=b)

    # entities no longer referenced by any claim/edge are stale
    run("""MATCH (e:Entity) WHERE NOT (e)--() AND NOT e.id IN $ids DETACH DELETE e""",
        ids=[e.id for e in g.entities])
    integrity = run("""
        CALL () { MATCH (c:Claim) WHERE NOT (c)-[:SUBJECT]->() OR NOT (c)-[:OBJECT]->() OR NOT (c)-[:FROM]->()
               RETURN count(c) AS dangling_claims }
        CALL () { MATCH (r:Risk) WHERE NOT (r)-[:CITES]->() RETURN count(r) AS risks_without_evidence }
        CALL () { MATCH (:Entity)-[x]->(:Entity) WHERE x.status = 'VERIFIED'
               AND NOT EXISTS { MATCH (c:Claim) WHERE c.id IN x.claim_ids AND c.tier = 1 }
               RETURN count(x) AS verified_without_tier1 }
        RETURN dangling_claims, risks_without_evidence, verified_without_tier1""")[0]
    stats = {"changed_sources": len(changed), "removed_sources": len(removed), "stale_claims_removed": stale, **sem,
             "integrity": integrity}
    log.info("Neo4j load complete: %s", stats)
    (config.OUT_DIR / "neo4j_load.json").write_text(json.dumps(stats, indent=1))
    return stats


# ============================================================================ repository
def _lucene(query: str) -> str:
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", query) if len(t) > 1]
    return " OR ".join(toks) if toks else "*"


def _edge_row(r: dict, ev: list[dict] | None) -> dict:
    out = {k: r[k] for k in ("source", "type", "target", "status", "confidence", "best_tier", "latest_date")}
    if ev is not None:
        out["evidence"] = sorted(ev, key=lambda c: (c["tier"], c["date"] or ""))
    return out


CLAIM_FIELDS = ("id", "subject", "predicate", "object", "mode", "status", "superseded_by", "file_path", "line_start",
                "tier", "date", "kind", "snippet", "extractor")


class Neo4jGraph:
    def __init__(self):
        wait_for_neo4j()
        self._index: dict | None = None

    def entity_index(self) -> dict[str, dict]:
        if self._index is None:
            rows = run("MATCH (e:Entity) RETURN e.id AS id, e.name AS name, e.type AS type, e.aliases AS aliases, "
                       "e.scope AS scope")
            self._index = {r["id"]: {**r, "aliases": r["aliases"] or []} for r in rows}
        return self._index

    def get_entity(self, eid: str) -> dict | None:
        rows = run("MATCH (e:Entity {id: $id}) RETURN e {.*, embedding: null} AS e", id=eid)
        if not rows:
            return None
        e = rows[0]["e"]
        e["properties"] = json.loads(e.pop("props") or "{}")
        return e

    def _evidence(self, claim_ids: list[str]) -> dict[str, dict]:
        rows = run("MATCH (c:Claim) WHERE c.id IN $ids RETURN c {" + ", ".join("." + f for f in CLAIM_FIELDS) + "} AS c",
                   ids=claim_ids)
        return {r["c"]["id"]: r["c"] for r in rows}

    def edges_of(self, ids: Iterable[str], direction: str = "both", with_evidence: bool = True) -> list[dict]:
        ids = list(ids)
        cond = {"out": "a.id IN $ids", "in": "b.id IN $ids", "both": "a.id IN $ids OR b.id IN $ids"}[direction]
        rows = run(f"""MATCH (a:Entity)-[x]->(b:Entity) WHERE {cond}
                       RETURN a.id AS source, type(x) AS type, b.id AS target, x.status AS status,
                              x.confidence AS confidence, x.best_tier AS best_tier, x.latest_date AS latest_date,
                              x.claim_ids AS claim_ids""", ids=ids)
        ev = self._evidence([c for r in rows for c in r["claim_ids"]]) if with_evidence else {}
        return [_edge_row(r, [ev[c] for c in r["claim_ids"] if c in ev] if with_evidence else None) for r in rows]

    def reverse_impact(self, eid: str, depth: int) -> list[dict]:
        types = "|".join(e.value for e in IMPACT_EDGES)
        rows = run(f"""MATCH p = (x:Entity)-[:{types}*1..{int(depth)}]->(t:Entity {{id: $id}})
                       WHERE all(r IN relationships(p) WHERE r.status IN ['VERIFIED', 'DOCUMENTED'])
                         AND x <> t
                       WITH x, p ORDER BY length(p) ASC
                       WITH x, collect(p)[0] AS p
                       WITH x, length(p) AS hops, relationships(p)[0] AS r
                       RETURN x.id AS entity, hops, startNode(r).id AS source, type(r) AS type, endNode(r).id AS target,
                              r.status AS status, r.confidence AS confidence, r.best_tier AS best_tier,
                              r.latest_date AS latest_date, r.claim_ids AS claim_ids
                       ORDER BY hops, entity""", id=eid)
        ev = self._evidence([c for r in rows for c in r["claim_ids"]])
        return [{"entity": r["entity"], "hops": r["hops"],
                 "via": _edge_row(r, [ev[c] for c in r["claim_ids"] if c in ev])} for r in rows]

    def shortest_path(self, a: str, b: str) -> list[dict]:
        types = "|".join(sorted(PATH_EDGES))
        rows = run(f"""MATCH (a:Entity {{id: $a}}), (b:Entity {{id: $b}})
                       MATCH p = shortestPath((a)-[:{types}*..8]-(b))
                       WHERE all(r IN relationships(p) WHERE r.status IN ['VERIFIED', 'DOCUMENTED', 'ASSERTED'])
                       UNWIND relationships(p) AS r
                       RETURN startNode(r).id AS source, type(r) AS type, endNode(r).id AS target, r.status AS status,
                              r.confidence AS confidence, r.best_tier AS best_tier, r.latest_date AS latest_date,
                              r.claim_ids AS claim_ids""", a=a, b=b)
        ev = self._evidence([c for r in rows for c in r["claim_ids"]])
        return [_edge_row(r, [ev[c] for c in r["claim_ids"] if c in ev]) for r in rows]

    def risks(self, entity_ids=None, categories=None) -> list[dict]:
        rows = run("""MATCH (r:Risk)
                      WHERE ($ids IS NULL OR any(e IN r.entities WHERE e IN $ids))
                        AND ($cats IS NULL OR r.category IN $cats)
                      RETURN r {.*, embedding: null} AS r""",
                   ids=list(entity_ids) if entity_ids is not None else None,
                   cats=list(categories) if categories else None)
        out = []
        for row in rows:
            r = row["r"]
            r.pop("embedding", None)
            r.pop("entities_text", None)
            r["evidence"] = json.loads(r.get("evidence") or "[]")
            out.append(r)
        return out

    def claims_for(self, eid: str) -> list[dict]:
        rows = run("MATCH (c:Claim)-[:SUBJECT|OBJECT]->(:Entity {id: $id}) RETURN DISTINCT c {"
                   + ", ".join("." + f for f in CLAIM_FIELDS) + "} AS c", id=eid)
        return sorted((r["c"] for r in rows), key=lambda c: (c["date"] or "0000", c["tier"]))

    def chunk(self, cid: str) -> dict | None:
        rows = run("MATCH (c:Chunk {id: $id}) RETURN c {.*, embedding: null} AS c", id=cid)
        return rows[0]["c"] if rows else None

    def search(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        q = _lucene(query)
        lex = [(f"entity::{r['id']}", r["score"]) for r in run(
            "CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score "
            "RETURN node.id AS id, score LIMIT 25", q=q)]
        lex_c = [(f"chunk::{r['id']}", r["score"]) for r in run(
            "CALL db.index.fulltext.queryNodes('chunk_text', $q) YIELD node, score "
            "RETURN node.id AS id, score LIMIT 25", q=q)]
        lex_r = [(f"risk::{r['id']}", r["score"]) for r in run(
            "CALL db.index.fulltext.queryNodes('risk_text', $q) YIELD node, score "
            "RETURN node.id AS id, score LIMIT 15", q=q)]
        vec: list[tuple[str, float]] = []
        qv = V.embed_query(query)
        if qv:
            for idx, prefix in (("entity_emb", "entity"), ("chunk_emb", "chunk"), ("risk_emb", "risk")):
                try:
                    vec += [(f"{prefix}::{r['id']}", r["score"]) for r in run(
                        f"CALL db.index.vector.queryNodes('{idx}', 15, $v) YIELD node, score "
                        f"RETURN node.id AS id, score", v=qv)]
                except Exception as e:  # noqa: BLE001 - index may be empty when built without embeddings
                    log.debug("vector query failed: %s", e)
            vec.sort(key=lambda x: -x[1])
        index = self.entity_index()
        exact = []
        for m in RECORD_RE.finditer(query):
            for p in ("decision:", "issue:", "incident:"):
                if f"{p}{m.group(1)}" in index:
                    exact.append((f"entity::{p}{m.group(1)}", 1.0))
        for tok in re.findall(r"[A-Za-z][\w.-]{2,}", query):
            h = resolve_name(tok, index)
            if h and name_key(tok) in {name_key(n) for n in [index[h]["name"], h.split(":", 1)[-1], *index[h]["aliases"]]}:
                exact.append((f"entity::{h}", 1.0))
        lexical = sorted(lex + lex_c + lex_r, key=lambda x: -x[1])
        return V.rrf(exact, lexical, vec, weights=[2.0, 1.0, 1.2])[:k]

    def export(self) -> dict:
        ents = run("MATCH (e:Entity) RETURN e {.*, embedding: null, aliases_text: null} AS e")
        edges = run("""MATCH (a:Entity)-[x]->(b:Entity) RETURN a.id AS source, type(x) AS type, b.id AS target,
                       x.status AS status, x.confidence AS confidence""")
        return {"entities": [r["e"] for r in ents], "edges": edges, "risks": self.risks()}
