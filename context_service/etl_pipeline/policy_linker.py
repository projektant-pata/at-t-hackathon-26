"""Semantic policy linking: connect code-level findings to the documented policies they violate.

A rule like "full record written to logs" finds the code line; a wiki may separately state
"email addresses and full employee snapshots are masked". Both are about the same component, but
nothing links them lexically. We embed each code finding and each POLICY that GOVERNS one of the
finding's entities, and attach the best policy (cosine >= threshold) as claim-side evidence.
The finding then carries both sides: documented intent and contradicting runtime behaviour.
"""
from __future__ import annotations

import logging

from etl_pipeline import llm
from etl_pipeline.parsers.common import ParseResult
from etl_pipeline.schema import EdgeType, EvidenceRef
from knowledge_store.vector_db import cosine

log = logging.getLogger("policy_linker")
THRESHOLD = 0.73  # calibrated: true links >= 0.74, best false link 0.716


def _risk_text(r) -> str:
    return f"{r.title}. {r.description} {r.guidance_for_codegen}"


async def link_policies(res: ParseResult, audit: dict, threshold: float = THRESHOLD) -> None:
    governs: dict[str, list] = {}
    for c in res.claims:
        if c.predicate == EdgeType.GOVERNS and c.mode != "proposed" and c.status not in ("SUPERSEDED", "REJECTED"):
            governs.setdefault(c.object, []).append(c)
    candidates = []
    for r in res.risks:
        if r.category not in ("SECURITY", "POLICY_VIOLATION", "BUG") or any(e.side == "claim" for e in r.evidence):
            continue
        pols = {c.subject: c for e in r.entities for c in governs.get(e, [])}
        if pols:
            candidates.append((r, pols))
    if not candidates:
        audit["policy_linking"] = {"candidates": 0, "linked": 0}
        return
    texts = [_risk_text(r) for r, _ in candidates]
    pol_ids = sorted({p for _, pols in candidates for p in pols})
    texts += [res.entities[p].properties.get("statement", res.entities[p].name) for p in pol_ids]
    vecs = await llm.embed(texts)
    if not vecs:
        audit["policy_linking"] = {"candidates": len(candidates), "linked": 0, "note": "embeddings unavailable"}
        return
    pvec = dict(zip(pol_ids, vecs[len(candidates):]))
    linked, scores = 0, []
    for (r, pols), rv in zip(candidates, vecs[:len(candidates)]):
        best = max(((cosine(rv, pvec[p]), p) for p in pols), default=(0.0, None))
        scores.append(round(best[0], 3))
        if best[1] and best[0] >= threshold:
            c = pols[best[1]]
            p = c.provenance
            r.evidence.append(EvidenceRef(file_path=p.file_path, snippet=p.snippet, line_start=p.line_start,
                                          tier=p.tier, date=p.effective_date, side="claim", claim_id=c.id))
            r.description += (f" Documented policy ({p.file_path}) says: \"{p.snippet[:200]}\" "
                              f"- the code contradicts it (semantic link, cosine {best[0]:.2f}).")
            r.detected_by += ",policy-linker"
            linked += 1
    audit["policy_linking"] = {"candidates": len(candidates), "linked": linked, "threshold": threshold,
                               "scores": sorted(scores, reverse=True)}
