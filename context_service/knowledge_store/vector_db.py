"""Semantic layer: document chunking, embeddings (Gemini via litellm), lexical scoring, RRF fusion.

Embeddings are computed once by the ETL and written to out/embeddings.json; the Neo4j loader puts them
into native vector indexes, the in-memory backend uses them directly. Query-time embedding needs an API
key - without one, retrieval degrades gracefully to full-text + alias matching.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import config
from etl_pipeline import llm
from etl_pipeline.schema import ContextGraph

CHUNKS_JSON = config.OUT_DIR / "chunks.json"
EMB_JSON = config.OUT_DIR / "embeddings.json"
TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
STOP = set("the a an and or of to in on for is are was be by with as at from this that it its into not no can "
           "should must may will what which who how why when does do if any all their there".split())


def tokens(text: str) -> list[str]:
    out = []
    for t in TOKEN.findall(text.lower()):
        if t in STOP:
            continue
        out.append(t)
        if "-" in t or "_" in t or "." in t:
            out.extend(p for p in re.split(r"[-_.]", t) if p and p not in STOP)
    return out


def build_chunks(g: ContextGraph, repo_root: Path) -> list[dict]:
    """Paragraph-level chunks of Tier-2/3 prose, linked to the entities claimed on those lines."""
    claims_by_file: dict[str, list] = {}
    for c in g.claims:
        claims_by_file.setdefault(c.provenance.file_path, []).append(c)
    chunks = []
    for s in g.sources:
        if s.modality == "diagram" or s.tier == 1 or not s.path.endswith(".md"):
            continue
        p = repo_root / s.path
        if not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        start, buf = 1, []
        paras = []
        for i, ln in enumerate(lines + [""], 1):
            if ln.strip():
                if not buf:
                    start = i
                buf.append(ln)
            elif buf:
                paras.append((start, i - 1, "\n".join(buf)))
                buf = []
        title = s.title or s.path
        for j, (a, b, text) in enumerate(paras):
            if text.lstrip().startswith("#") and len(paras) > 1:
                continue
            about = sorted({e for c in claims_by_file.get(s.path, []) if c.provenance.line_start and
                            a <= c.provenance.line_start <= b for e in (c.subject, c.object)})
            from etl_pipeline.extractors import record_entity_id
            about = sorted(set(about) | {record_entity_id(s)})
            chunks.append({"id": f"{s.path}#{a}", "file_path": s.path, "line_start": a, "line_end": b,
                           "tier": s.tier, "date": s.date, "kind": s.kind, "scope": s.scope,
                           "text": f"{title} ({s.kind}, {s.date or 'undated'}): {text}"[:2000], "about": about})
    return chunks


def embedding_texts(g: ContextGraph, chunks: list[dict]) -> dict[str, str]:
    texts = {f"entity::{e.id}": f"{e.id} | {e.summary or e.name}" for e in g.entities}
    texts |= {f"risk::{r.id}": f"{r.category} {r.severity}: {r.title}. {r.description}" for r in g.risks}
    texts |= {f"chunk::{c['id']}": c["text"] for c in chunks}
    return texts


def build_semantic_index(g: ContextGraph, repo_root: Path) -> dict:
    chunks = build_chunks(g, repo_root)
    CHUNKS_JSON.write_text(json.dumps(chunks))
    texts = embedding_texts(g, chunks)
    keys = list(texts)
    vecs = llm.embed_sync([texts[k] for k in keys]) if config.llm_enabled() else None
    if vecs:
        EMB_JSON.write_text(json.dumps({k: v for k, v in zip(keys, vecs) if v}))
    elif EMB_JSON.exists():
        EMB_JSON.unlink()
    return {"chunks": len(chunks), "embeddings": len(vecs) if vecs else 0}


def load_chunks() -> list[dict]:
    return json.loads(CHUNKS_JSON.read_text()) if CHUNKS_JSON.exists() else []


def load_embeddings() -> dict[str, list[float]]:
    return json.loads(EMB_JSON.read_text()) if EMB_JSON.exists() else {}


def embed_query(text: str) -> list[float] | None:
    if not config.llm_enabled():
        return None
    out = llm.embed_sync([text])
    return out[0] if out else None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.4, b: float = 0.75):
        self.docs = {k: Counter(tokens(v)) for k, v in docs.items()}
        self.len = {k: sum(c.values()) for k, c in self.docs.items()}
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 1
        df = Counter(t for c in self.docs.values() for t in c)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        q = tokens(query)
        scores = {}
        for key, tf in self.docs.items():
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[key] / self.avg))
            if s > 0:
                scores[key] = s
        return sorted(scores.items(), key=lambda x: -x[1])[:k]


def rrf(*rankings: list[tuple[str, float]], k: int = 60, weights: list[float] | None = None) -> list[tuple[str, float]]:
    agg: dict[str, float] = {}
    for i, ranking in enumerate(rankings):
        w = weights[i] if weights else 1.0
        for rank, (key, _) in enumerate(ranking):
            agg[key] = agg.get(key, 0.0) + w / (k + rank + 1)
    return sorted(agg.items(), key=lambda x: -x[1])
