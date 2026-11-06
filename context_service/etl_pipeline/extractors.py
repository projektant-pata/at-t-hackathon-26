"""LLM/VLM extraction workers + conversion of their output into grounded Claims / Risks.

Stages:
  1. extract_doc      - prose (wiki, ADR, notes, issues, incidents, releases, spec .md) -> claims, aliases, policies
  2. extract_diagram  - PNG -> boxes/arrows (vision), then a second, independent vision pass verifies every arrow
  3. review_code      - per service: code + contract + governing policies -> security/policy/bug findings
All outputs pass the grounding gate before they become claims.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

import config
from etl_pipeline import llm, prompts
from etl_pipeline.grounding import ground
from etl_pipeline.parsers.common import ParseResult, scope_prefix
from etl_pipeline.resolver import Resolver
from etl_pipeline.router import RoutedFile
from etl_pipeline.schema import (type_check, EdgeType, Entity, EvidenceRef, LLMCodeReview, LLMArrowVerification, LLMDocBatch,
                                 LLMDiagramExtraction, LLMDocExtraction, NodeType, Risk, SourceDoc)

log = logging.getLogger("extractors")

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
VALID_MODES = {"observed", "asserted", "proposed", "deprecated", "rejected", "negated"}


@dataclass
class ExtractionAudit:
    llm_claims: int = 0
    grounded: int = 0
    rejected_ungrounded: list[dict] = field(default_factory=list)
    rejected_schema: list[str] = field(default_factory=list)
    ontology_coerced: list[str] = field(default_factory=list)
    dropped_arrows: list[dict] = field(default_factory=list)
    unverified_arrows: int = 0
    code_findings: int = 0
    code_findings_rejected: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- record entities
def record_entity_id(src: SourceDoc) -> str:
    stem = src.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    prefix = scope_prefix(src)
    return {
        "adr": f"decision:{src.record_id}", "issue": f"issue:{src.record_id}", "incident": f"incident:{src.record_id}",
        "release": f"release:{src.record_id}", "meeting": f"meeting:{stem}", "diagram": f"diagram:{stem}",
    }.get(src.kind, f"{prefix}doc:{src.path}")


RECORD_TYPES = {"adr": NodeType.DECISION, "issue": NodeType.ISSUE, "incident": NodeType.INCIDENT,
                "release": NodeType.RELEASE, "meeting": NodeType.MEETING, "diagram": NodeType.DIAGRAM}
RECORD_REF = re.compile(r"\b(ADR-\d{3}|INC-20\d\d-\d\d-\d\d|[A-Z]{2,7}-\d{1,4})\b")


def add_record_entities(files: list[RoutedFile], res: ParseResult) -> None:
    """Every document becomes a typed node (ADR, issue, ...) + deterministic cross-reference claims."""
    ids = {}
    for f in files:
        s = f.source
        if s.tier < 3 and s.kind not in {"api_spec_md"}:
            continue
        rid = record_entity_id(s)
        ids[s.record_id or ""] = rid
        res.entity(rid, RECORD_TYPES.get(s.kind, NodeType.DOCUMENT), s.title or s.path, tier=s.tier, scope=s.scope,
                   path=s.path, date=s.date, status=s.status, kind=s.kind, owner=s.owner)
    for f in files:
        s = f.source
        if f.text is None or (s.tier < 3 and s.kind != "api_spec_md"):
            continue
        rid = record_entity_id(s)
        seen = set()
        for m in RECORD_REF.finditer(f.text):
            ref = m.group(1)
            target = ids.get(ref)
            if not target or target == rid or target in seen:
                continue
            seen.add(target)
            ln = f.text.count("\n", 0, m.start()) + 1
            res.claim(s, rid, EdgeType.MENTIONS, target, f.text.splitlines()[ln - 1].strip(), ln, "parser:xref",
                      mode="observed")


# --------------------------------------------------------------------------- catalog
def build_catalog(res: ParseResult, limit: int = 260) -> str:
    order = [NodeType.SERVICE, NodeType.FRONTEND_APP, NodeType.TEAM, NodeType.DATASTORE, NodeType.TABLE,
             NodeType.COLUMN, NodeType.API_ENDPOINT, NodeType.LIBRARY, NodeType.DECISION, NodeType.ISSUE,
             NodeType.INCIDENT, NodeType.RELEASE]
    rows = []
    for t in order:
        for e in sorted((e for e in res.entities.values() if e.type == t and e.scope == "live"), key=lambda e: e.id):
            if t == NodeType.LIBRARY and not e.id.startswith("lib:@repo/"):
                continue
            extra = ""
            if t == NodeType.COLUMN and e.properties.get("status") not in (None, "current"):
                extra = f" [{e.properties['status']}]"
            rows.append(f"{e.id} | {e.name}{extra} | {', '.join(e.aliases)}")
    return "\n".join(rows[:limit])


# --------------------------------------------------------------------------- docs
async def extract_doc(f: RoutedFile, catalog: str) -> LLMDocExtraction | None:
    s = f.source
    numbered = f.text or ""
    user = prompts.DOC_USER.format(path=s.path, kind=s.kind, tier=s.tier, date=s.date, owner=s.owner, status=s.status,
                                   catalog=catalog, text=numbered[:30000])
    user += f"\nThis document's own record id is `{record_entity_id(s)}` - use it as subject for AFFECTS/DECIDES/" \
            f"SUPERSEDES claims made by this record."
    return await llm.structured(config.EXTRACT_MODEL, prompts.DOC_SYSTEM, user, LLMDocExtraction)


def make_batches(files: list[RoutedFile], max_chars: int = 9000, max_docs: int = 8) -> list[list[RoutedFile]]:
    """Pack small documents into batches (sorted by path so related records travel together).
    Large documents (> max_chars) get a batch of their own."""
    batches, cur, size = [], [], 0
    for f in sorted(files, key=lambda f: f.source.path):
        n = len(f.text or "")
        if cur and (size + n > max_chars or len(cur) >= max_docs):
            batches.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += n
    if cur:
        batches.append(cur)
    return batches


async def extract_doc_batch(batch: list[RoutedFile], catalog: str) -> dict[str, LLMDocExtraction]:
    """One LLM call for several documents; returns {path: extraction}. Missing paths are simply absent."""
    if len(batch) == 1:
        one = await extract_doc(batch[0], catalog)
        return {batch[0].source.path: one} if one else {}
    docs = "\n\n".join(prompts.DOC_BATCH_ITEM.format(
        path=f.source.path, kind=f.source.kind, tier=f.source.tier, date=f.source.date, owner=f.source.owner,
        status=f.source.status, rid=record_entity_id(f.source), text=(f.text or "")[:12000]) for f in batch)
    user = prompts.DOC_BATCH_USER.format(catalog=catalog, documents=docs)
    out = await llm.structured(config.EXTRACT_MODEL, prompts.DOC_BATCH_SYSTEM, user, LLMDocBatch)
    if not out and config.EXTRACT_FALLBACK_MODEL:
        log.warning("batch failed on %s, falling back to %s", config.EXTRACT_MODEL, config.EXTRACT_FALLBACK_MODEL)
        out = await llm.structured(config.EXTRACT_FALLBACK_MODEL, prompts.DOC_BATCH_SYSTEM, user, LLMDocBatch)
    if not out:
        return {}
    wanted = {f.source.path for f in batch}
    return {d.path.strip(): LLMDocExtraction(**d.model_dump(exclude={"path"}))
            for d in out.documents if d.path.strip() in wanted}


def apply_doc_extraction(ext: LLMDocExtraction, f: RoutedFile, resolver: Resolver, res: ParseResult,
                         audit: ExtractionAudit) -> None:
    s, text = f.source, f.text or ""
    prefix = scope_prefix(s)
    rid = record_entity_id(s)
    if rid in res.entities and ext.summary:
        res.entities[rid].summary = ext.summary
    proposed_doc = (s.status or "").lower().startswith(("proposed", "design review", "draft"))
    for c in ext.claims:
        audit.llm_claims += 1
        try:
            pred = EdgeType(c.predicate.strip().upper())
        except ValueError:
            audit.rejected_schema.append(f"{s.path}: bad predicate {c.predicate}")
            continue
        ok, l1, l2, score = ground(c.snippet, text)
        if not ok:
            audit.rejected_ungrounded.append({"file": s.path, "snippet": c.snippet[:160], "score": round(score, 2)})
            continue
        mode = c.mode.lower().strip() if c.mode.lower().strip() in VALID_MODES else "asserted"
        if proposed_doc and mode in {"asserted", "observed"}:
            mode = "proposed"
        if s.tier >= 3 and mode == "observed" and s.kind not in {"release", "incident", "issue", "adr", "meeting"}:
            mode = "asserted"  # wikis / how-tos cannot "observe" runtime behaviour
        subj = rid if c.subject.strip() in {rid, "this", "self"} else \
            resolver.resolve(c.subject, c.subject_type, s.scope, prefix, s.tier)
        obj = resolver.resolve(c.object, c.object_type, s.scope, prefix, s.tier)
        if subj == obj:
            continue
        pred = _validate_types(pred, subj, obj, res, audit, s.path)
        snippet = " ".join(text.splitlines()[l1 - 1:l2]).strip() if l1 else c.snippet
        res.claim(s, subj, pred, obj, snippet, l1, f"llm:{config.EXTRACT_MODEL}", mode=mode, line_end=l2,
                  grounding_score=round(score, 3), quoted=c.snippet[:300])
        audit.grounded += 1
    for p in ext.policies:
        ok, l1, l2, score = ground(p.snippet, text)
        if not ok:
            audit.rejected_ungrounded.append({"file": s.path, "snippet": p.snippet[:160], "score": round(score, 2),
                                              "what": "policy"})
            continue
        pid = "policy:" + hashlib.sha1(f"{s.path}|{p.statement}".encode()).hexdigest()[:10]
        res.entity(pid, NodeType.POLICY, p.statement[:120], tier=s.tier, scope=s.scope, statement=p.statement,
                   strength=p.strength.upper(), path=s.path, date=s.date)
        res.entities[pid].summary = p.statement
        res.claim(s, rid, EdgeType.DECIDES, pid, p.snippet, l1, f"llm:{config.EXTRACT_MODEL}", mode="asserted",
                  line_end=l2, grounding_score=round(score, 3))
        for g in p.governs:
            target = resolver.resolve(g, None, s.scope, prefix, s.tier)
            res.claim(s, pid, EdgeType.GOVERNS, target, p.snippet, l1, f"llm:{config.EXTRACT_MODEL}",
                      mode="proposed" if proposed_doc else "asserted", line_end=l2, strength=p.strength.upper(),
                      grounding_score=round(score, 3))


def apply_aliases(ext: LLMDocExtraction, f: RoutedFile, resolver: Resolver, audit: ExtractionAudit) -> None:
    for a in ext.aliases:
        ok, *_ = ground(a.snippet, f.text or "")
        if ok:
            resolver.add_alias(a.alias, a.canonical, f.source.path, f.source.scope)
        else:
            audit.rejected_ungrounded.append({"file": f.source.path, "snippet": a.snippet[:160], "what": "alias"})


def title_aliases(files: list[RoutedFile], resolver: Resolver) -> None:
    """Deterministic alias seed: titles like '# Stock / inventory-service'."""
    for f in files:
        s = f.source
        m = re.match(r"^([A-Z][\w ]+?)\s*/\s*([a-z][\w-]+)$", s.title or "")
        if m and s.scope == "live":
            canonical = resolver.alias.get(re.sub(r"[^a-z0-9]", "", m.group(2).lower()))
            if canonical:
                resolver.add_alias(m.group(1).strip(), canonical, s.path, s.scope)


def _validate_types(pred: EdgeType, subj: str, obj: str, res: ParseResult, audit: ExtractionAudit,
                    path: str) -> EdgeType:
    """Ontology gate: ill-typed relations (e.g. a service CALLS a table) are kept only as weak MENTIONS."""
    st, ot = res.entities[subj].type.value, res.entities[obj].type.value
    if type_check(pred.value, st, ot):
        return pred
    audit.ontology_coerced.append(f"{path}: {subj}({st}) -{pred.value}-> {obj}({ot})")
    return EdgeType.MENTIONS


# --------------------------------------------------------------------------- diagrams
def _date_from_text(text: str) -> str | None:
    if m := re.search(r"(20\d\d)-(\d\d)(?:-(\d\d))?", text):
        return m.group(0)
    for name, num in MONTHS.items():
        if re.search(name, text, re.I) and (y := re.search(r"20\d\d", text)):
            return f"{y.group(0)}-{num:02d}"
    return None


async def extract_diagram(f: RoutedFile, catalog: str) -> tuple[LLMDiagramExtraction | None, LLMArrowVerification | None]:
    user = prompts.DIAGRAM_USER.format(path=f.source.path, catalog=catalog)
    ext = await llm.structured(config.VISION_MODEL, prompts.DIAGRAM_SYSTEM, user, LLMDiagramExtraction,
                               images=[f.abs_path])
    if not ext or not ext.arrows:
        return ext, None
    arrows = "\n".join(f"{i}: {a.source} -> {a.target} [{a.label}] {a.style}" for i, a in enumerate(ext.arrows))
    ver = await llm.structured(config.VISION_MODEL, prompts.ARROW_VERIFY_SYSTEM,
                               prompts.ARROW_VERIFY_USER.format(path=f.source.path, arrows=arrows),
                               LLMArrowVerification, images=[f.abs_path])
    return ext, ver


def apply_diagram(ext: LLMDiagramExtraction, ver: LLMArrowVerification | None, f: RoutedFile, resolver: Resolver,
                  res: ParseResult, audit: ExtractionAudit) -> None:
    s = f.source
    if not s.date:
        s.date = _date_from_text(f"{ext.date_text} {ext.title}")
    did = record_entity_id(s)
    status = ext.diagram_status.lower()
    res.entity(did, NodeType.DIAGRAM, ext.title or s.path, tier=3, path=s.path, date=s.date, diagram_status=status,
               annotations=ext.annotations)
    res.entities[did].summary = f"{ext.title} ({ext.date_text}); status={status}; " + "; ".join(ext.annotations)[:400]
    label_to_id: dict[str, str] = {}
    kind_type = {"service": "SERVICE", "frontend": "FRONTEND_APP", "datastore": "DATASTORE", "table": "TABLE",
                 "team": "TEAM", "external": "CONCEPT", "process": "CONCEPT", "note": "CONCEPT"}
    model = f"vlm:{config.VISION_MODEL}"
    for b in ext.boxes:
        t = kind_type.get(b.kind.lower(), "CONCEPT")
        eid = resolver.resolve(b.canonical or b.label, t, s.scope, scope_prefix(s), 3)
        label_to_id[b.label] = eid
        res.claim(s, did, EdgeType.MENTIONS, eid, f"[{ext.title}] box '{b.label}'", None, model, mode="asserted")
        if b.fields and res.entities[eid].type == NodeType.TABLE:
            tname = eid.split(":", 1)[1]
            for fld in b.fields:
                col = fld.split(":")[0].strip()
                ctype = fld.split(":", 1)[1].strip() if ":" in fld else ""
                cid = resolver.resolve(f"column:{tname}.{col}", "COLUMN", s.scope, scope_prefix(s), 3)
                res.claim(s, eid, EdgeType.HAS_COLUMN, cid, f"[{ext.title}] {b.label}.{fld}", None, model,
                          mode="asserted" if status in {"current", "historical"} else "proposed", data_type=ctype)
    checks = {c.index: c for c in (ver.checks if ver else [])}
    for i, a in enumerate(ext.arrows):
        chk = checks.get(i)
        if ver and (chk is None or not chk.present or not chk.direction_correct):
            audit.dropped_arrows.append({"file": s.path, "arrow": f"{a.source} -> {a.target} [{a.label}]"})
            continue
        if not ver:
            audit.unverified_arrows += 1
        src_id = label_to_id.get(a.source) or resolver.resolve(a.source, None, s.scope, scope_prefix(s), 3)
        tgt_id = label_to_id.get(a.target) or resolver.resolve(a.target, None, s.scope, scope_prefix(s), 3)
        if src_id == tgt_id:
            continue
        style = (chk.style if chk else a.style).lower()
        tgt_type = res.entities[tgt_id].type
        if status == "process":
            pred, mode = EdgeType.MENTIONS, "asserted"
        else:
            pred = EdgeType.DEPENDS_ON if tgt_type in (NodeType.DATASTORE, NodeType.LIBRARY) else \
                EdgeType.OWNED_BY if tgt_type == NodeType.TEAM else EdgeType.CALLS
            mode = "proposed" if ("dash" in style or status in {"target", "proposal"}) else "asserted"
            pred = _validate_types(pred, src_id, tgt_id, res, audit, s.path)
        res.claim(s, src_id, pred, tgt_id, f"[{ext.title} | {ext.date_text}] {a.source} -> {a.target} "
                                           f"[{a.label}] ({style} line)", None, model, mode=mode,
                  arrow_label=a.label, line_style=style, diagram_status=status, second_pass_verified=bool(chk))


# --------------------------------------------------------------------------- code review
def numbered(text: str) -> str:
    return "\n".join(f"{i:4d} | {ln}" for i, ln in enumerate(text.splitlines(), 1))


async def review_code(service: str, code_files: list[RoutedFile], spec_files: list[RoutedFile],
                      policies: list[tuple[Entity, SourceDoc]]) -> LLMCodeReview | None:
    code = "\n\n".join(f"### {f.source.path}\n{numbered(f.text or '')}" for f in code_files)
    spec = "\n\n".join(f"### {f.source.path}\n{f.text}" for f in spec_files) or "(no contract found)"
    pol = "\n".join(f"- [{p.properties.get('strength', '')}] {p.properties.get('statement', p.name)} "
                    f"(source: {src.path}, {src.date})" for p, src in policies) or "(none)"
    user = prompts.CODE_REVIEW_USER.format(service=service, spec_paths=", ".join(f.source.path for f in spec_files),
                                           spec=spec[:20000], policies=pol, code=code[:60000])
    return await llm.structured(config.REVIEW_MODEL, prompts.CODE_REVIEW_SYSTEM, user, LLMCodeReview)


def apply_code_review(review: LLMCodeReview, service_eid: str, code_files: list[RoutedFile],
                      context_files: list[RoutedFile], res: ParseResult, audit: ExtractionAudit) -> None:
    for fnd in review.findings:
        code_ev = None
        for f in code_files:
            ok, l1, l2, _ = ground(fnd.code_snippet, f.text or "")
            if ok:
                code_ev = EvidenceRef(file_path=f.source.path, snippet=" ".join((f.text or "").splitlines()[l1 - 1:l2]).strip(),
                                      line_start=l1, tier=f.source.tier, date=f.source.date, side="truth")
                break
        if not code_ev:
            audit.code_findings_rejected.append({"service": service_eid, "title": fnd.title,
                                                 "snippet": fnd.code_snippet[:160]})
            continue
        evidence = [code_ev]
        if fnd.contract_snippet:
            for f in context_files:
                ok, l1, l2, _ = ground(fnd.contract_snippet, f.text or "")
                if ok:
                    evidence.append(EvidenceRef(file_path=f.source.path,
                                                snippet=" ".join((f.text or "").splitlines()[l1 - 1:l2]).strip(),
                                                line_start=l1, tier=f.source.tier, date=f.source.date, side="claim"))
                    break
        cat = fnd.category.upper().strip()
        cat = cat if cat in {"SECURITY", "POLICY_VIOLATION", "BUG"} else "SECURITY"
        if cat == "BUG" and len(evidence) > 1:
            cat = "POLICY_VIOLATION"  # behaviour contradicts the documented contract
        sev = fnd.severity.upper() if fnd.severity.upper() in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM"
        r = Risk(category=cat, severity=sev, title=fnd.title, description=fnd.explanation, entities=[service_eid],
                 evidence=evidence, guidance_for_codegen=fnd.guidance_for_codegen,
                 current_truth=f"Code at {code_ev.file_path}:{code_ev.line_start} behaves as: {code_ev.snippet[:160]}",
                 detected_by=f"llm:{config.REVIEW_MODEL}", confidence=0.75 if len(evidence) > 1 else 0.6)
        r.compute_id()
        res.risks.append(r)
        audit.code_findings += 1
