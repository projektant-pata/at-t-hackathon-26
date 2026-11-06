"""Agentic ETL entrypoint.

  python -m etl_pipeline.run_etl [--repo PATH] [--no-llm] [--no-load] [--no-judge]

Outputs out/validated_context_graph.json + out/etl_audit_report.md and (unless --no-load)
loads the graph into Neo4j (or nothing for the in-memory backend, which reads the JSON).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import config
from etl_pipeline import extractors as X
from etl_pipeline import llm
from etl_pipeline.parsers.code import parse_code
from etl_pipeline.parsers.common import ParseResult, build_repo_index
from etl_pipeline.parsers.security_rules import scan
from etl_pipeline.parsers.specs_config import parse_spec_or_config
from etl_pipeline.parsers.sql_migrations import parse_migrations
from etl_pipeline.resolver import Resolver
from etl_pipeline.router import RoutedFile, crawl
from etl_pipeline.schema import ContextGraph, EdgeType, NodeType
from etl_pipeline.validator import aggregate_edges, dedupe_risks, integrity_checks, reconcile

log = logging.getLogger("etl")

LLM_DOC_KINDS = {"wiki", "adr", "architecture", "design", "howto", "reference", "runbook", "meeting", "issue",
                 "incident", "release", "readme", "api_spec_md", "api_history", "archive"}


def deterministic_stage(files: list[RoutedFile], idx) -> ParseResult:
    res = ParseResult()
    migrations = []
    for f in files:
        s = f.source
        if f.text is None:
            continue
        if s.kind == "code":
            res.merge(parse_code(s, f.text, idx))
            res.merge(scan(s, f.text, idx))
        elif s.kind == "sql_migration" or (s.kind == "archive" and s.path.endswith(".sql")):
            migrations.append((s, f.text))
        elif s.tier in (1, 2) or s.kind == "api_history":
            res.merge(parse_spec_or_config(s, f.text, idx))
    res.merge(parse_migrations(migrations, idx))
    X.add_record_entities(files, res)
    return res


async def llm_stage(files: list[RoutedFile], res: ParseResult, resolver: Resolver, audit: X.ExtractionAudit,
                    run_judge: bool, run_audit: dict) -> None:
    catalog = X.build_catalog(res)
    docs = [f for f in files if f.text is not None and f.source.kind in LLM_DOC_KINDS and f.source.path.endswith(".md")
            or (f.source.kind == "archive" and f.text is not None)]
    diagrams = [f for f in files if f.source.kind == "diagram"]
    batches = X.make_batches(docs)
    log.info("LLM extraction: %d documents in %d batches, %d diagrams", len(docs), len(batches), len(diagrams))
    t0 = time.time()
    batch_out, diag_out = await asyncio.gather(
        asyncio.gather(*(X.extract_doc_batch(b, catalog) for b in batches)),
        asyncio.gather(*(X.extract_diagram(f, catalog) for f in diagrams)))
    by_doc: dict = {}
    for d in batch_out:
        by_doc.update(d)
    missing = [f for f in docs if f.source.path not in by_doc]
    if missing:  # resilience: documents a batch dropped are retried one by one
        log.info("re-extracting %d documents individually", len(missing))
        for f, ext in zip(missing, await asyncio.gather(*(X.extract_doc(f, catalog) for f in missing))):
            if ext:
                by_doc[f.source.path] = ext
    doc_out = [by_doc.get(f.source.path) for f in docs]
    run_audit["batching"] = {"documents": len(docs), "batches": len(batches), "individual_retries": len(missing)}
    run_audit["timings"]["llm_extraction_s"] = round(time.time() - t0, 1)
    # pass 1: aliases from every document (so cross-document nicknames resolve), pass 2: claims
    for f, ext in zip(docs, doc_out):
        if ext:
            X.apply_aliases(ext, f, resolver, audit)
        else:
            audit.rejected_schema.append(f"{f.source.path}: extraction failed")
    for f, ext in zip(docs, doc_out):
        if ext:
            X.apply_doc_extraction(ext, f, resolver, res, audit)
    for f, (ext, ver) in zip(diagrams, diag_out):
        if ext:
            X.apply_diagram(ext, ver, f, resolver, res, audit)
        else:
            audit.rejected_schema.append(f"{f.source.path}: diagram extraction failed")

    # code review per backend service, with its contract + policies that govern it
    by_path = {f.source.path: f for f in files}
    sources = {f.source.path: f.source for f in files}
    tasks, meta = [], []
    for e in [e for e in res.entities.values() if e.type == NodeType.SERVICE and e.scope == "live"]:
        name = e.id.split(":", 1)[1]
        code_files = [f for f in files if f.source.kind == "code" and f.source.path.startswith(f"apps/{name}/")]
        if not code_files:
            continue
        spec_files = [by_path[p] for p in (f"api-specs/{name}.md", f"api-specs/{name}.openapi.json") if p in by_path]
        policy_ids = {c.subject for c in res.claims if c.predicate == EdgeType.GOVERNS and c.object == e.id
                      and c.mode != "proposed"}
        policies = [(res.entities[p], sources[res.entities[p].properties["path"]]) for p in sorted(policy_ids)
                    if res.entities[p].properties.get("path") in sources]
        context_files = spec_files + [by_path[s.path] for _, s in policies if s.path in by_path]
        tasks.append(X.review_code(name, code_files, [f for f in spec_files if f.source.path.endswith(".md")], policies))
        meta.append((e.id, code_files, context_files))
    t0 = time.time()
    reviews = await asyncio.gather(*tasks)
    run_audit["timings"]["code_review_s"] = round(time.time() - t0, 1)
    for (eid, code_files, ctx), rv in zip(meta, reviews):
        if rv:
            X.apply_code_review(rv, eid, code_files, ctx, res, audit)
    from etl_pipeline.policy_linker import link_policies
    await link_policies(res, run_audit)
    t0 = time.time()
    await reconcile(res, use_llm=run_judge, audit=run_audit)
    run_audit["timings"]["judge_s"] = round(time.time() - t0, 1)


def summarize_entities(res: ParseResult, edges) -> None:
    out_edges = defaultdict(list)
    for e in edges:
        if e.status in ("VERIFIED", "DOCUMENTED"):
            out_edges[e.source].append(e)
    for ent in res.entities.values():
        if ent.summary:
            continue
        parts = [f"{ent.name} [{ent.type.value}]"]
        if ent.aliases:
            parts.append("aka " + ", ".join(ent.aliases))
        p = ent.properties
        for k in ("port", "status", "data_type", "current_columns", "removed_columns", "statement", "date", "kind"):
            if p.get(k):
                parts.append(f"{k}={p[k]}")
        rel = defaultdict(list)
        for e in out_edges.get(ent.id, []):
            rel[e.type.value].append(e.target.split(":", 1)[-1])
        for k, v in rel.items():
            parts.append(f"{k}: {', '.join(sorted(set(v))[:12])}")
        ent.summary = "; ".join(parts)[:900]


def write_audit_report(g: ContextGraph, path: Path) -> None:
    a = g.audit
    L = [f"# ETL Audit Report", "", f"Generated {g.generated_at} from `{g.repo_root}`", ""]
    L += ["## Pipeline", "", "| Metric | Value |", "|---|---|"]
    for k in ("sources", "entities", "claims", "edges", "risks", "llm_enabled"):
        L.append(f"| {k} | {a.get(k)} |")
    for k, v in a.get("llm_stats", {}).items():
        L.append(f"| llm.{k} | {v} |")
    for k, v in a.get("timings", {}).items():
        L.append(f"| time.{k} | {v} |")
    L += ["", "### Sources by authority tier", ""]
    for k, v in sorted(a.get("sources_by_tier", {}).items()):
        L.append(f"- tier {k}: {v}")
    L += ["", "### Edge epistemic status", ""]
    for k, v in sorted(a.get("edge_status", {}).items()):
        L.append(f"- {k}: {v}")
    L += ["", "## Validation gates", ""]
    ex = a.get("extraction", {})
    L.append(f"- LLM claims proposed: {ex.get('llm_claims', 0)}, grounded & accepted: {ex.get('grounded', 0)}, "
             f"rejected as ungrounded: {len(ex.get('rejected_ungrounded', []))}")
    L.append(f"- Schema failures: {len(ex.get('rejected_schema', []))}")
    L.append(f"- Diagram arrows dropped by 2nd-pass verification: {len(ex.get('dropped_arrows', []))}; "
             f"unverified arrows kept (confidence capped): {ex.get('unverified_arrows', 0)}")
    L.append(f"- Code-review findings accepted: {ex.get('code_findings', 0)}, rejected (snippet not in code): "
             f"{len(ex.get('code_findings_rejected', []))}")
    L.append(f"- Entity merges refused by scope guard / alias conflicts: {len(a.get('refused_merges', []))}")
    for r in a.get("refused_merges", [])[:20]:
        L.append(f"  - `{r['alias']}` -> `{r['canonical']}` from {r['source']}: {r['reason']}")
    L.append(f"- Judge: {a.get('judge')}")
    L.append(f"- Candidates: {a.get('candidates')}")
    L.append(f"- Integrity: {a.get('integrity')}")
    L += ["", "## Risks", ""]
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for cat in ["SECURITY", "POLICY_VIOLATION", "BUG", "STALE_DOC", "CONTRADICTION", "OWNERSHIP_DRIFT",
                "UNDOCUMENTED_DEP", "UNVERIFIED_ASSUMPTION"]:
        rs = sorted([r for r in g.risks if r.category == cat], key=lambda r: order[r.severity])
        if not rs:
            continue
        L += [f"### {cat} ({len(rs)})", ""]
        for r in rs:
            L.append(f"- **[{r.severity}] {r.title}** — entities: {', '.join(f'`{e}`' for e in r.entities[:6])} "
                     f"(conf {r.confidence:.2f}, {r.detected_by})")
            L.append(f"  - {r.description}")
            if r.current_truth:
                L.append(f"  - Current truth: {r.current_truth}")
            if r.guidance_for_codegen:
                L.append(f"  - Codegen guidance: {r.guidance_for_codegen}")
            for e in r.evidence[:6]:
                L.append(f"  - [{e.side}] `{e.file_path}:{e.line_start or '-'}` (tier {e.tier}, {e.date or 'undated'}): "
                         f"{e.snippet[:200]}")
        L.append("")
    if ex.get("rejected_ungrounded"):
        L += ["## Rejected (ungrounded) LLM output — sample", ""]
        for r in ex["rejected_ungrounded"][:25]:
            L.append(f"- {r['file']}: \"{r['snippet']}\"")
    path.write_text("\n".join(L) + "\n")


def run(repo: Path, use_llm: bool, run_judge: bool, load: bool) -> ContextGraph:
    t_start = time.time()
    files = crawl(repo)
    idx = build_repo_index(repo)
    run_audit: dict = {"timings": {}}
    t0 = time.time()
    res = deterministic_stage(files, idx)
    run_audit["timings"]["deterministic_s"] = round(time.time() - t0, 1)
    resolver = Resolver(res)
    X.title_aliases(files, resolver)
    ex_audit = X.ExtractionAudit()
    if use_llm:
        asyncio.run(llm_stage(files, res, resolver, ex_audit, run_judge, run_audit))
    else:
        asyncio.run(reconcile(res, use_llm=False, audit=run_audit))
    res.risks = dedupe_risks(res.risks)
    for c in res.claims:
        if not c.id:
            c.compute_id()
    # drop exact duplicate claims (same id)
    uniq = {}
    for c in res.claims:
        uniq.setdefault(c.id, c)
    res.claims = list(uniq.values())
    edges = aggregate_edges(res)
    summarize_entities(res, edges)
    g = ContextGraph(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), repo_root=str(repo),
                     sources=[f.source for f in files], entities=list(res.entities.values()), claims=res.claims,
                     edges=edges, risks=res.risks)
    g.audit = {
        **run_audit, "sources": len(g.sources), "entities": len(g.entities), "claims": len(g.claims),
        "edges": len(g.edges), "risks": len(g.risks), "llm_enabled": use_llm,
        "sources_by_tier": dict(Counter(s.tier for s in g.sources)),
        "edge_status": dict(Counter(e.status for e in g.edges)),
        "risk_categories": dict(Counter(r.category for r in g.risks)),
        "extraction": {**ex_audit.__dict__, "ontology_coerced_count": len(ex_audit.ontology_coerced)}, "refused_merges": resolver.refused,
        "llm_stats": dict(llm.STATS),
    }
    g.audit["integrity"] = integrity_checks(g)
    g.audit["timings"]["total_s"] = round(time.time() - t_start, 1)
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    config.GRAPH_JSON.write_text(g.model_dump_json(indent=1))
    write_audit_report(g, config.AUDIT_REPORT)
    from knowledge_store.vector_db import build_semantic_index
    g.audit["semantic_index"] = build_semantic_index(g, repo)
    log.info("semantic index: %s", g.audit["semantic_index"])
    log.info("wrote %s (%d entities, %d claims, %d edges, %d risks)", config.GRAPH_JSON, len(g.entities),
             len(g.claims), len(g.edges), len(g.risks))
    if load and config.GRAPH_BACKEND == "neo4j":
        from knowledge_store.neo4j_client import load_graph
        load_graph(g)
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=config.REPO_ROOT)
    ap.add_argument("--no-llm", action="store_true", help="deterministic parsers only (ablation / offline)")
    ap.add_argument("--no-judge", action="store_true", help="LLM extraction without the adversarial judge (ablation)")
    ap.add_argument("--no-load", action="store_true", help="do not load into Neo4j")
    ap.add_argument("--load-only", action="store_true", help="(re)load the existing graph JSON into Neo4j")
    args = ap.parse_args()
    if args.load_only:
        logging.basicConfig(level=logging.INFO)
        from knowledge_store.neo4j_client import load_graph
        print(load_graph(ContextGraph.model_validate_json(config.GRAPH_JSON.read_text())))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("neo4j.notifications", "LiteLLM", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    use_llm = config.llm_enabled() and not args.no_llm
    if not use_llm:
        log.warning("LLM stages disabled (no GEMINI_API_KEY or --no-llm): deterministic extraction only")
    g = run(args.repo.resolve(), use_llm, use_llm and not args.no_judge, not args.no_load)
    print(json.dumps({k: g.audit[k] for k in ("sources", "entities", "claims", "edges", "risks", "edge_status",
                                               "risk_categories", "llm_stats", "timings")}, indent=1))
    print("integrity:", g.audit["integrity"])


if __name__ == "__main__":
    main()
