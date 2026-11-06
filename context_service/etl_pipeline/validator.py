"""Validation & reconciliation.

1. Deterministic cross-checks produce *candidate* conflicts (cheap, high recall).
2. Candidates are clustered per anchor component; the adversarial LLM judge sees every claim of the cluster
   (all tiers, dates, modes) and decides: real problem? which side is current? which claims are superseded?
3. Judge output is accepted only if it cites >= 2 grounded claims from different files (else downgraded).
4. Claims are aggregated into edges with an epistemic status + computed confidence.
5. Integrity checks run over the final graph.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

import config
from etl_pipeline import llm, prompts
from etl_pipeline.parsers.common import ParseResult
from etl_pipeline.schema import (Claim, ContextGraph, Edge, EdgeType, EvidenceRef, LLMJudgeVerdict, NodeType, Risk,
                                 SourceDoc)

log = logging.getLogger("validator")
RUNTIME = {NodeType.SERVICE, NodeType.FRONTEND_APP}
DEP_PREDS = {EdgeType.CALLS, EdgeType.DEPENDS_ON}


@dataclass
class Candidate:
    id: str
    kind: str                 # endpoint_drift | column_drift | ownership_drift | unverified_dependency | undocumented_dependency
    anchor: str               # entity id used for clustering
    description: str
    claim_ids: list[str]
    truth_claim_ids: list[str] = field(default_factory=list)
    default_category: str = "CONTRADICTION"
    default_severity: str = "MEDIUM"


def _anchor_of(eid: str, res: ParseResult) -> str:
    e = res.entities.get(eid)
    if e is None:
        return eid
    if e.type in RUNTIME:
        return eid
    svc = e.properties.get("service")
    if svc:
        for p in ("service:", "app:"):
            if f"{p}{svc}" in res.entities:
                return f"{p}{svc}"
    if e.type in (NodeType.TABLE, NodeType.COLUMN):
        svc = eid.split(":", 1)[1].split(".")[0]
        if f"service:{svc}" in res.entities:
            return f"service:{svc}"
    return eid


def is_live_claim(c: Claim) -> bool:
    return c.mode in {"asserted", "observed"} and c.status not in {"SUPERSEDED", "REJECTED"}


# ------------------------------------------------------------------------- candidates
def find_candidates(res: ParseResult) -> list[Candidate]:
    cands: list[Candidate] = []
    by_spo: dict[tuple, list[Claim]] = defaultdict(list)
    for c in res.claims:
        by_spo[(c.subject, c.predicate, c.object)].append(c)
    tier1_pairs = {(c.subject, c.object) for c in res.claims if c.provenance.tier == 1 and c.mode == "observed"
                   and c.predicate in DEP_PREDS}
    tier1_exposed = {c.object for c in res.claims if c.predicate == EdgeType.EXPOSES and c.provenance.tier == 1}
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"K{n}"

    # C1 endpoint drift: doc/spec says an endpoint exists that code does not expose
    for c in res.claims:
        if c.predicate not in (EdgeType.EXPOSES, EdgeType.CALLS) or c.provenance.tier == 1 or not is_live_claim(c):
            continue
        e = res.entities.get(c.object)
        if not e or e.type != NodeType.API_ENDPOINT or e.scope != "live" or c.object in tier1_exposed:
            continue
        if e.properties.get("method") == "GRAPHQL":
            continue
        svc = e.properties.get("service", "")
        truth = [x.id for x in res.claims if x.predicate == EdgeType.EXPOSES and x.provenance.tier == 1
                 and res.entities.get(x.object) and res.entities[x.object].properties.get("service") == svc][:8]
        cands.append(Candidate(nid(), "endpoint_drift", _anchor_of(c.object, res),
                               f"{c.provenance.file_path} claims endpoint {e.name} on {svc}, but no Tier-1 code exposes it.",
                               [c.id], truth, "STALE_DOC", "MEDIUM"))
    # C2 column drift: doc/diagram describes a column that is not in the current (replayed) schema
    for c in res.claims:
        if c.predicate != EdgeType.HAS_COLUMN or c.provenance.tier == 1 or not is_live_claim(c):
            continue
        col = res.entities.get(c.object)
        if not col or col.scope != "live":
            continue
        status = col.properties.get("status")
        if status == "current":
            continue
        truth = [x.id for x in res.claims if x.provenance.tier == 1 and
                 (x.subject == c.object or x.object == c.object or
                  (x.predicate == EdgeType.HAS_COLUMN and x.subject == c.subject and x.mode == "observed"))][:8]
        desc = (f"{c.provenance.file_path} describes column {col.name}, which the migrations mark as {status}."
                if status else f"{c.provenance.file_path} describes column {col.name}, absent from all migrations.")
        cands.append(Candidate(nid(), "column_drift", _anchor_of(c.subject, res), desc, [c.id], truth,
                               "STALE_DOC", "MEDIUM"))
    # C3 ownership drift
    owners: dict[str, list[Claim]] = defaultdict(list)
    for c in res.claims:
        if c.predicate == EdgeType.OWNED_BY and c.mode != "rejected":
            owners[c.subject].append(c)
    for subj, cl in owners.items():
        t1 = [c for c in cl if c.provenance.tier == 1]
        for c in cl:
            if c.provenance.tier > 1 and t1 and all(c.object != x.object for x in t1) and is_live_claim(c):
                cands.append(Candidate(nid(), "ownership_drift", subj,
                                       f"{c.provenance.file_path} names {c.object} as owner of {subj}; "
                                       f"CODEOWNERS says {', '.join(x.object for x in t1)}.",
                                       [c.id], [x.id for x in t1], "OWNERSHIP_DRIFT", "MEDIUM"))
    # C4 dependency asserted by docs/diagrams without Tier-1 evidence
    for c in res.claims:
        if c.predicate not in DEP_PREDS or c.provenance.tier < 3 or not is_live_claim(c):
            continue
        s, o = res.entities.get(c.subject), res.entities.get(c.object)
        if not s or not o or s.scope != "live" or o.type not in RUNTIME | {NodeType.DATASTORE}:
            continue
        if (c.subject, c.object) in tier1_pairs:
            continue
        truth = [x.id for x in res.claims if x.provenance.tier == 1 and x.predicate in DEP_PREDS
                 and x.subject == c.subject and res.entities.get(x.object) and res.entities[x.object].type in RUNTIME][:6]
        cands.append(Candidate(nid(), "unverified_dependency", _anchor_of(c.subject, res),
                               f"{c.provenance.file_path} asserts {c.subject} -{c.predicate.value}-> {c.object}; "
                               f"no code/config evidence found.", [c.id], truth, "UNVERIFIED_ASSUMPTION", "LOW"))
    # C5 runtime dependency present in code/config but never described by any document
    doc_pairs = {(c.subject, c.object) for c in res.claims if c.provenance.tier >= 2 and c.predicate in DEP_PREDS}
    seen = set()
    for c in res.claims:
        if c.predicate != EdgeType.CALLS or c.provenance.tier != 1 or (c.subject, c.object) in seen:
            continue
        s, o = res.entities.get(c.subject), res.entities.get(c.object)
        if not s or not o or s.type not in RUNTIME or o.type not in RUNTIME:
            continue
        seen.add((c.subject, c.object))
        if (c.subject, c.object) not in doc_pairs:
            ids = [x.id for x in res.claims if x.subject == c.subject and x.object == c.object and x.provenance.tier == 1]
            cands.append(Candidate(nid(), "undocumented_dependency", c.subject,
                                   f"Code/config shows {c.subject} calls {c.object}, but no document or diagram describes it.",
                                   ids[:4], ids[:4], "UNDOCUMENTED_DEP", "LOW"))
    return cands


# ------------------------------------------------------------------------- judge
def _cluster_claims(anchor: str, cands: list[Candidate], res: ParseResult, cap: int = 140) -> list[Claim]:
    related = {anchor} | {e.id for e in res.entities.values()
                          if _anchor_of(e.id, res) == anchor and e.id != anchor}
    must = {cid for k in cands for cid in k.claim_ids + k.truth_claim_ids}
    picked: list[Claim] = [c for c in res.claims if c.id in must]
    seen_t1 = {(c.subject, c.predicate, c.object) for c in picked if c.provenance.tier == 1}
    rest = []
    for c in res.claims:
        if c.id in must or not (c.subject in related or c.object in related):
            continue
        if c.provenance.tier == 1:
            k = (c.subject, c.predicate, c.object)
            if k in seen_t1 or c.predicate in (EdgeType.CONFIGURED_BY,):
                continue
            seen_t1.add(k)
        rest.append(c)
    rest.sort(key=lambda c: (c.provenance.tier == 1, c.predicate == EdgeType.MENTIONS))
    return (picked + rest)[:cap]


def _fmt_claim(short: str, c: Claim) -> str:
    p = c.provenance
    return (f"{short} | T{p.tier} | {p.effective_date or '-'} | {p.doc_kind} | {c.mode} | {c.subject} -{c.predicate.value}-> "
            f"{c.object} | {p.file_path}:{p.line_start or '-'} | {p.snippet[:220]}")


async def judge_batch(group: list[tuple[str, list[Candidate]]], res: ParseResult
                      ) -> tuple[LLMJudgeVerdict | None, dict]:
    """One judge call for several entity clusters (claim ids are unique across the whole batch)."""
    sections, short, n = [], {}, 0
    for anchor, cands in group:
        claims = _cluster_claims(anchor, cands, res, cap=110)
        lines = []
        for c in claims:
            if c.id not in short:
                n += 1
                short[c.id] = f"C{n}"
            lines.append(_fmt_claim(short[c.id], c))
        cand_txt = "\n".join(f"{k.id} [{k.kind}] {k.description} | claims: {', '.join(short.get(i, '?') for i in k.claim_ids)}"
                             f" | tier-1 context: {', '.join(short.get(i, '?') for i in k.truth_claim_ids)}" for k in cands)
        sections.append(prompts.JUDGE_USER.replace("Return JSON for the schema.", "").format(
            cluster=anchor, claims="\n".join(lines), candidates=cand_txt or "(none)"))
    user = "\n\n".join(sections) + "\nJudge every cluster above. Return JSON for the schema."
    verdict = await llm.structured(config.JUDGE_MODEL, prompts.JUDGE_SYSTEM, user, LLMJudgeVerdict)
    return verdict, {v: k for k, v in short.items()}


def batch_clusters(clusters: dict[str, list[Candidate]], res: ParseResult, max_claims: int = 220,
                   max_clusters: int = 4) -> list[list[tuple[str, list[Candidate]]]]:
    batches, cur, size = [], [], 0
    for anchor, ks in sorted(clusters.items()):
        n = min(110, len(_cluster_claims(anchor, ks, res, cap=110)))
        if cur and (size + n > max_claims or len(cur) >= max_clusters):
            batches.append(cur)
            cur, size = [], 0
        cur.append((anchor, ks))
        size += n
    if cur:
        batches.append(cur)
    return batches


def _evidence(ids: list[str], side: str, claims: dict[str, Claim]) -> list[EvidenceRef]:
    out = []
    for i in ids:
        c = claims.get(i)
        if c:
            p = c.provenance
            out.append(EvidenceRef(file_path=p.file_path, snippet=p.snippet, line_start=p.line_start, tier=p.tier,
                                   date=p.effective_date, side=side, claim_id=c.id))
    return out


def _risk_entities(ids: list[str], claims: dict[str, Claim], res: ParseResult, anchor: str) -> list[str]:
    ents = {anchor}
    for i in ids:
        c = claims.get(i)
        if c:
            for e in (c.subject, c.object):
                ent = res.entities.get(e)
                if ent and ent.type not in (NodeType.DOCUMENT, NodeType.MEETING, NodeType.DIAGRAM, NodeType.POLICY):
                    ents.add(e)
    return sorted(ents)


def candidate_to_risk(k: Candidate, claims: dict[str, Claim], res: ParseResult, detected_by: str) -> Risk:
    r = Risk(category=k.default_category, severity=k.default_severity, title=k.description.split(";")[0][:140],
             description=k.description, entities=_risk_entities(k.claim_ids, claims, res, k.anchor),
             evidence=_evidence(k.claim_ids, "claim", claims) + _evidence(k.truth_claim_ids[:3], "truth", claims),
             current_truth="See Tier-1 evidence.", detected_by=detected_by, confidence=0.55)
    r.compute_id()
    return r


async def reconcile(res: ParseResult, use_llm: bool, audit: dict) -> None:
    cands = find_candidates(res)
    audit["candidates"] = {"total": len(cands)}
    for k in cands:
        audit["candidates"][k.kind] = audit["candidates"].get(k.kind, 0) + 1
    claims = {c.id: c for c in res.claims}
    if not use_llm:
        for k in cands:
            if k.kind in {"column_drift", "ownership_drift", "endpoint_drift"}:
                res.risks.append(candidate_to_risk(k, claims, res, "rule:deterministic-crosscheck"))
        audit["judge"] = "skipped (LLM disabled)"
        return

    clusters: dict[str, list[Candidate]] = defaultdict(list)
    for k in cands:
        clusters[k.anchor].append(k)
    # also judge clusters that only carry doc claims about runtime components (catches doc-vs-doc contradictions)
    for e in res.entities.values():
        if e.type in RUNTIME and e.scope == "live" and e.id not in clusters:
            if sum(1 for c in res.claims if c.provenance.tier >= 3 and (c.subject == e.id or c.object == e.id)) >= 3:
                clusters[e.id] = []
    batches = batch_clusters(clusters, res)
    results = await asyncio.gather(*(judge_batch(b, res) for b in batches))
    covered: set[str] = set()
    fp: set[str] = set()
    stats = {"clusters": len(clusters), "judge_calls": len(batches), "risks": 0, "downgraded": 0, "failed": 0,
             "status_updates": 0}
    for group, (verdict, back) in zip(batches, results):
        ks = [k for _, cs in group for k in cs]
        default_anchor = group[0][0]
        if verdict is None:
            stats["failed"] += 1
            for k in ks:
                if k.kind in {"column_drift", "ownership_drift"}:
                    res.risks.append(candidate_to_risk(k, claims, res, "rule:deterministic-crosscheck"))
            continue
        fp |= set(verdict.false_positive_candidates)
        for jr in verdict.risks:
            stale = [back[i] for i in jr.stale_claim_ids if i in back]
            truth = [back[i] for i in jr.truth_claim_ids if i in back]
            first = claims.get((stale + truth or [None])[0])
            anchor = _anchor_of(first.subject, res) if first else default_anchor
            if anchor not in res.entities or res.entities[anchor].type not in RUNTIME:
                anchor = _anchor_of(first.object, res) if first else default_anchor
            ev = _evidence(stale, "claim", claims) + _evidence(truth, "truth", claims)
            files = {e.file_path for e in ev}
            category = jr.category.upper().strip()
            if category not in {"STALE_DOC", "CONTRADICTION", "OWNERSHIP_DRIFT", "UNDOCUMENTED_DEP",
                                "UNVERIFIED_ASSUMPTION", "POLICY_VIOLATION"}:
                category = "CONTRADICTION"
            conf = 0.85
            if len(files) < 2 and category != "UNDOCUMENTED_DEP":  # validation rule A.5
                category, conf = "UNVERIFIED_ASSUMPTION", 0.4
                stats["downgraded"] += 1
            sev = jr.severity.upper() if jr.severity.upper() in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM"
            r = Risk(category=category, severity=sev, title=jr.title, description=jr.description,
                     entities=_risk_entities(stale + truth, claims, res, anchor), evidence=ev,
                     current_truth=jr.current_truth, guidance_for_codegen=jr.guidance_for_codegen,
                     detected_by=f"llm-judge:{config.JUDGE_MODEL}", confidence=conf)
            r.compute_id()
            res.risks.append(r)
            stats["risks"] += 1
            covered |= set(stale) | set(truth)
        for st in verdict.claim_statuses:
            cid = back.get(st.claim_id)
            if not cid or st.status.upper() == "VALID":
                continue
            c = claims[cid]
            if c.provenance.tier == 1 and st.status.upper() in {"SUPERSEDED", "REJECTED"}:
                continue  # judge may not overrule runtime observations
            c.status = st.status.upper()
            c.superseded_by = back.get(st.superseded_by) if st.superseded_by else None
            c.properties["status_reason"] = st.reason
            stats["status_updates"] += 1
        for k in ks:  # high-precision deterministic candidates the judge neither confirmed nor rejected
            if k.id not in fp and not (set(k.claim_ids) & covered) and k.kind in {"column_drift", "ownership_drift"}:
                res.risks.append(candidate_to_risk(k, claims, res, "rule:deterministic-crosscheck"))
    stats["false_positive_candidates"] = len(fp)
    audit["judge"] = stats


# ------------------------------------------------------------------------- aggregation
def aggregate_edges(res: ParseResult) -> list[Edge]:
    groups: dict[tuple, list[Claim]] = defaultdict(list)
    for c in res.claims:
        groups[(c.subject, c.predicate, c.object)].append(c)
    edges = []
    for (s, p, o), cl in groups.items():
        files = {c.provenance.file_path for c in cl}
        best_tier = min(c.provenance.tier for c in cl)
        statuses = {c.status for c in cl if c.status}
        modes = {c.mode for c in cl}
        t1_observed = any(c.provenance.tier == 1 and c.mode == "observed" for c in cl)
        live = [c for c in cl if c.mode in {"asserted", "observed"} and c.status not in {"SUPERSEDED", "REJECTED",
                                                                                          "CONTRADICTED", "PROPOSED"}]
        if t1_observed:
            status, conf = "VERIFIED", min(0.99, 0.9 + 0.02 * min(len(files), 4))
        elif modes <= {"rejected"} or "REJECTED" in statuses and not live:
            status, conf = "REJECTED", 0.05
        elif modes <= {"deprecated"} or ("SUPERSEDED" in statuses and not live):
            status, conf = "SUPERSEDED", 0.15
        elif "CONTRADICTED" in statuses and not live:
            status, conf = "CONTRADICTED", 0.2
        elif any(c.provenance.tier == 2 and c.mode in {"asserted", "observed"} for c in live):
            status, conf = "DOCUMENTED", 0.75
        elif not live and ("proposed" in modes or "PROPOSED" in statuses):
            status, conf = "PROPOSED", 0.3
        elif live:
            diagram_only = all(c.provenance.modality == "diagram" for c in live)
            unverified = any(not c.properties.get("second_pass_verified", True) for c in live)
            conf = min(0.7, 0.4 + 0.1 * len({c.provenance.file_path for c in live}))
            if diagram_only and unverified:
                conf = min(conf, 0.5)
            status = "ASSERTED"
        else:
            status, conf = "ASSERTED", 0.3
        dates = sorted(c.provenance.effective_date for c in cl if c.provenance.effective_date)
        edges.append(Edge(source=s, type=p, target=o, status=status, confidence=round(conf, 2),
                          claim_ids=[c.id for c in cl], best_tier=best_tier, latest_date=dates[-1] if dates else None))
    return edges


def dedupe_risks(risks: list[Risk]) -> list[Risk]:
    """Merge risks that point at the same code line (rule + LLM review) or share id."""
    out: dict[str, Risk] = {}
    by_line: dict[tuple, str] = {}
    for r in sorted(risks, key=lambda r: (not r.detected_by.startswith("rule:"), r.id)):
        t1 = next((e for e in r.evidence if e.tier == 1 and e.line_start), None)
        line_key = (t1.file_path, t1.line_start) if t1 else None
        target = out.get(r.id) or (out.get(by_line[line_key]) if line_key in by_line else None)
        if target is None:
            out[r.id] = r
            if line_key:
                by_line[line_key] = r.id
            continue
        known = {(e.file_path, e.line_start) for e in target.evidence}
        target.evidence += [e for e in r.evidence if (e.file_path, e.line_start) not in known]
        target.entities = sorted(set(target.entities) | set(r.entities))
        if r.category == "POLICY_VIOLATION" and target.category == "SECURITY" and \
                any(e.side == "claim" for e in r.evidence):
            target.description += f" Also violates the documented contract: {r.description}"
        if not target.guidance_for_codegen:
            target.guidance_for_codegen = r.guidance_for_codegen
        target.detected_by = ",".join(sorted(set(target.detected_by.split(",")) | {r.detected_by}))
        target.confidence = min(0.97, max(target.confidence, r.confidence) + 0.1)
    return list(out.values())


def integrity_checks(g: ContextGraph) -> dict:
    ents = {e.id for e in g.entities}
    claims = {c.id: c for c in g.claims}
    dangling = [c.id for c in g.claims if c.subject not in ents or c.object not in ents]
    no_evidence = [r.id for r in g.risks if not r.evidence]
    bad_verified = [f"{e.source}-{e.type.value}->{e.target}" for e in g.edges if e.status == "VERIFIED"
                    and not any(claims[i].provenance.tier == 1 for i in e.claim_ids)]
    superseded_unlinked = [c.id for c in g.claims if c.status == "SUPERSEDED" and not c.superseded_by]
    ungrounded_t3 = [c.id for c in g.claims if c.provenance.extractor.startswith("llm:") and
                     c.properties.get("grounding_score") is None]
    return {"dangling_claims": len(dangling), "risks_without_evidence": len(no_evidence),
            "verified_edges_without_tier1": len(bad_verified), "superseded_without_link": len(superseded_unlinked),
            "llm_claims_without_grounding": len(ungrounded_t3),
            "passed": not (dangling or no_evidence or bad_verified or ungrounded_t3),
            "examples": {"dangling": dangling[:5], "bad_verified": bad_verified[:5]}}
