"""Evaluate the built graph against the golden set (eval/golden.yaml).

  python -m eval.run_eval                 # evaluate config.OUT_DIR, write out/eval_report.md
  python -m eval.run_eval --ablation      # rebuild 3 variants (deterministic / +LLM extraction / +judge) and compare
  python -m eval.run_eval --json          # machine-readable metrics on stdout (used by --ablation)

Retrieval probes run against the in-memory backend over the same JSON so results are reproducible
without Neo4j; pass --backend neo4j to probe the live store instead.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

import config

GOLDEN = Path(__file__).with_name("golden.yaml")
LIVE = {"VERIFIED", "DOCUMENTED", "ASSERTED"}


def _match_file(path: str, patterns: list[str]) -> bool:
    return any(path == p or path.startswith(p) for p in patterns)


def eval_risk_case(case: dict, risks: list[dict]) -> dict:
    best = None
    for r in risks:
        if r["category"] not in case["categories"]:
            continue
        files = [e["file_path"] for e in r["evidence"]]
        claim_ok = any(_match_file(f, case["claim_side"]) for f in files)
        truth_ok = any(_match_file(f, case["truth_side"]) for f in files)
        ent_ok = not case.get("entities_any") or bool(set(case["entities_any"]) & set(r["entities"]))
        passed = ent_ok and truth_ok and (claim_ok or not case.get("require_both"))
        score = int(claim_ok) + int(truth_ok) + int(ent_ok)
        if passed:
            return {"id": case["id"], "name": case["name"], "passed": True, "risk": r["title"], "category": r["category"],
                    "files": sorted(set(files))[:6], "both_sides": claim_ok and truth_ok}
        if best is None or score > best[0]:
            best = (score, r, claim_ok, truth_ok, ent_ok)
    out = {"id": case["id"], "name": case["name"], "passed": False}
    if best:
        _, r, c, t, e = best
        out.update(closest=r["title"], closest_category=r["category"],
                   missing=[m for m, ok in (("claim-side citation", c), ("truth-side citation", t), ("entity", e)) if not ok])
    return out


def eval_assertion(a: dict, g: dict) -> dict:
    ents = {e["id"]: e for e in g["entities"]}
    claims = g["claims"]
    edges = g["edges"]
    res = {"id": a["id"], "name": a["name"], "passed": True, "violations": []}
    t = a["type"]
    if t == "no_link_between_scopes":
        live = set(a["live_entities"])
        for e in edges:
            pair = {e["source"], e["target"]}
            if pair & live and any(x.startswith(a["archive_prefix"]) for x in pair):
                res["violations"].append(f"{e['source']} -{e['type']}-> {e['target']} ({e['status']})")
        for c in claims:
            f = c["provenance"]["file_path"]
            if _match_file(f, a.get("also_files", [])) and ({c["subject"], c["object"]} & live) and \
                    c["predicate"] in ("CALLS", "DEPENDS_ON", "EXPOSES", "READS_WRITES", "ALIAS_OF", "HAS_COLUMN") and \
                    c["mode"] in ("asserted", "observed"):
                res["violations"].append(f"claim {f}: {c['subject']} -{c['predicate']}-> {c['object']} ({c['mode']})")
        for eid in live:
            for alias, src in (ents.get(eid, {}).get("properties", {}).get("alias_sources") or {}).items():
                if _match_file(src, a.get("also_files", [])):
                    res["violations"].append(f"alias '{alias}' of {eid} learned from {src}")
    elif t == "no_live_dependency_claims_from_files":
        excepted = {tuple(p) for p in a.get("except_pairs", [])}
        for c in claims:
            f = c["provenance"]["file_path"]
            if not _match_file(f, a["files"]) or c["predicate"] not in a["predicates"]:
                continue
            if (c["subject"], c["object"]) in excepted:
                continue
            if a.get("targets_contain") and not any(s in c["object"] for s in a["targets_contain"]):
                continue
            if c["mode"] in ("asserted", "observed") and c.get("status") not in ("PROPOSED", "REJECTED", "SUPERSEDED"):
                res["violations"].append(f"{f}: {c['subject']} -{c['predicate']}-> {c['object']} ({c['mode']})")
    elif t == "no_live_edge":
        for e in edges:
            if e["type"] not in a["types"] or e["status"] not in LIVE:
                continue
            src_ok = e["source"] in a.get("source_any", []) or any(e["source"].startswith(p) for p in a.get("source_prefix", []))
            if src_ok and any(s in e["target"].lower() for s in a["target_contains"]):
                res["violations"].append(f"{e['source']} -{e['type']}-> {e['target']} ({e['status']})")
    elif t == "edge_status":
        hit = [e for e in edges if e["source"] == a["source"] and e["target"] == a["target"] and e["type"] == a["edge_type"]]
        if not hit or hit[0]["status"] not in a["allowed"]:
            res["violations"].append(f"edge status = {hit[0]['status'] if hit else 'missing'}")
    elif t == "no_stale_flag_on_files":
        for r in g["risks"]:
            if r["category"] in ("STALE_DOC", "CONTRADICTION") and \
                    any(e["side"] == "claim" and _match_file(e["file_path"], a["files"]) for e in r["evidence"]):
                res["violations"].append(f"{r['category']}: {r['title']}")
    res["passed"] = not res["violations"]
    res["violations"] = res["violations"][:5]
    return res


def eval_probe(p: dict, svc) -> dict:
    fn = {"who_owns": svc.who_owns, "query": svc.query, "change_impact": svc.change_impact,
          "security_guidance": svc.security_guidance, "validate_plan": svc.validate_plan}[p["call"]]
    try:
        out = fn(*p["args"])
    except Exception as e:  # noqa: BLE001
        return {"id": p["id"], "name": p["name"], "passed": False, "error": str(e)[:200]}
    text = out if isinstance(out, str) else json.dumps(out, default=str)
    missing = [s for s in p.get("expect_all", []) if s not in text]
    any_ok = all(any(s in text for s in p[k]) for k in ("expect_any", "expect_any_2") if p.get(k))
    return {"id": p["id"], "name": p["name"], "passed": not missing and any_ok, "missing": missing,
            "any_group_ok": any_ok}


def evaluate(backend: str = "memory") -> dict:
    golden = yaml.safe_load(GOLDEN.read_text())
    g = json.loads(config.GRAPH_JSON.read_text())
    risks = g["risks"]
    risk_results = [eval_risk_case(c, risks) for c in golden["risk_cases"]]
    assertion_results = [eval_assertion(a, g) for a in golden["graph_assertions"]]
    if backend == "memory":
        from knowledge_store.graph_db import MemoryGraph
        from knowledge_store.hybrid_retriever import ContextService
        svc = ContextService(MemoryGraph())
    else:
        from knowledge_store.hybrid_retriever import ContextService
        svc = ContextService()
    probe_results = [eval_probe(p, svc) for p in golden["probes"]]
    decoys = [a for a in assertion_results if a["id"].startswith("D")]
    positives = [a for a in assertion_results if a["id"].startswith("P")]
    matched_titles = {r.get("risk") for r in risk_results if r["passed"]}
    ex = g["audit"].get("extraction", {})
    proposed = ex.get("llm_claims", 0)
    metrics = {
        "golden_recall": f"{sum(r['passed'] for r in risk_results)}/{len(risk_results)}",
        "golden_both_sides": f"{sum(bool(r.get('both_sides')) for r in risk_results)}/{len(risk_results)}",
        "decoy_false_links": sum(not a["passed"] for a in decoys),
        "decoys_total": len(decoys),
        "positive_controls": f"{sum(a['passed'] for a in positives)}/{len(positives)}",
        "probe_hit_rate": f"{sum(p['passed'] for p in probe_results)}/{len(probe_results)}",
        "risks_total": len(risks),
        "risks_matching_golden": len(matched_titles),
        "llm_claims_proposed": proposed,
        "grounding_rejection_rate": round(len(ex.get("rejected_ungrounded", [])) / proposed, 3) if proposed else None,
        "integrity_passed": g["audit"].get("integrity", {}).get("passed"),
        "llm_enabled": g["audit"].get("llm_enabled"),
    }
    return {"metrics": metrics, "risk_cases": risk_results, "assertions": assertion_results, "probes": probe_results}


def write_report(result: dict, ablation: dict | None = None) -> Path:
    m = result["metrics"]
    L = ["# Evaluation Report", "", "Golden set: `eval/golden.yaml` (hand-mapped planted problems, decoys, probes).", "",
         "## Metrics", "", "| Metric | Value | Target |", "|---|---|---|"]
    targets = {"golden_recall": ">= 9/10", "decoy_false_links": "0", "positive_controls": "all",
               "probe_hit_rate": ">= 6/7", "integrity_passed": "True"}
    for k, v in m.items():
        L.append(f"| {k} | {v} | {targets.get(k, '')} |")
    if ablation:
        L += ["", "## Ablation (same corpus, same cache)", "", "| Variant | golden recall | both sides | decoy false links | "
              "positive controls | probes | risks total |", "|---|---|---|---|---|---|---|"]
        for name, mm in ablation.items():
            L.append(f"| {name} | {mm['golden_recall']} | {mm['golden_both_sides']} | {mm['decoy_false_links']} | "
                     f"{mm['positive_controls']} | {mm['probe_hit_rate']} | {mm['risks_total']} |")
    L += ["", "## Golden risk cases", "", "| Case | Result | Detail |", "|---|---|---|"]
    for r in result["risk_cases"]:
        detail = f"{r['category']}: {r['risk']} — {', '.join(r['files'])}" if r["passed"] else \
            f"closest: {r.get('closest_category', '-')}: {r.get('closest', '-')} (missing {', '.join(r.get('missing', []))})"
        L.append(f"| {r['id']} {r['name']} | {'PASS' if r['passed'] else 'FAIL'} | {detail} |")
    L += ["", "## Decoys & positive controls", "", "| Check | Result | Violations |", "|---|---|---|"]
    for a in result["assertions"]:
        L.append(f"| {a['id']} {a['name']} | {'PASS' if a['passed'] else 'FAIL'} | {'; '.join(a['violations'])} |")
    L += ["", "## Retrieval / MCP probes", "", "| Probe | Result | Missing |", "|---|---|---|"]
    for p in result["probes"]:
        L.append(f"| {p['id']} {p['name']} | {'PASS' if p['passed'] else 'FAIL'} | "
                 f"{', '.join(p.get('missing', [])) or ('' if p.get('any_group_ok', True) else 'expect_any group')}"
                 f"{p.get('error', '')} |")
    path = config.OUT_DIR / "eval_report.md"
    path.write_text("\n".join(L) + "\n")
    return path


def run_ablation() -> dict:
    variants = {"deterministic only": ["--no-llm"], "+ LLM extraction (no judge)": ["--no-judge"],
                "+ LLM extraction + judge (full)": []}
    out = {}
    for name, flags in variants.items():
        d = config.OUT_DIR / "ablation" / name.split()[1].strip("+()") if False else \
            config.OUT_DIR / "ablation" / ("det" if "--no-llm" in flags else "nojudge" if "--no-judge" in flags else "full")
        env = {**os.environ, "CS_OUT_DIR": str(d), "CS_GRAPH_BACKEND": "memory"}
        subprocess.run([sys.executable, "-m", "etl_pipeline.run_etl", "--no-load", *flags], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        js = subprocess.run([sys.executable, "-m", "eval.run_eval", "--json"], env=env, check=True,
                            capture_output=True, text=True).stdout
        out[name] = json.loads(js)["metrics"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--backend", default="memory", choices=["memory", "neo4j"])
    a = ap.parse_args()
    result = evaluate(a.backend)
    if a.json:
        print(json.dumps(result))
        return
    ablation = run_ablation() if a.ablation else None
    path = write_report(result, ablation)
    print(json.dumps(result["metrics"], indent=1))
    if ablation:
        print(json.dumps(ablation, indent=1))
    print(f"report: {path}")


if __name__ == "__main__":
    main()
