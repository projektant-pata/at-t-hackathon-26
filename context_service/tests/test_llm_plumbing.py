"""End-to-end plumbing of the LLM stages with a stubbed model (no network): extraction -> grounding ->
diagram verification -> code review -> judge -> aggregation -> integrity checks."""
from __future__ import annotations

import re

import pytest

import config
from etl_pipeline import llm
from etl_pipeline.schema import (LLMDocBatch, LLMDocBatchItem, LLMArrowCheck, LLMArrowVerification, LLMClaim, LLMCodeFinding, LLMCodeReview,
                                 LLMDiagramArrow, LLMDiagramBox, LLMDiagramExtraction, LLMDocExtraction, LLMJudgeRisk,
                                 LLMJudgeVerdict, LLMPolicy)

pytestmark = pytest.mark.skipif(not config.REPO_ROOT.exists(), reason="corpus not available")


STATS = {"batch_calls": 0}


async def fake_structured(model, system, user, schema, temperature=0.0, images=None):
    if schema is LLMDocBatch:
        STATS["batch_calls"] += 1
        items = []
        for path, body in re.findall(r"==== DOCUMENT path=(\S+) [^\n]*\n(.*?)\n==== END", user, re.S):
            single = await fake_structured(model, system, f"Document path: {path}\n---- DOCUMENT START ----\n{body}"
                                                          f"---- DOCUMENT END", LLMDocExtraction)
            items.append(LLMDocBatchItem(path=path, **single.model_dump()))
        return LLMDocBatch(documents=items)
    if schema is LLMDocExtraction:
        if "Document path: api-specs/payment-service.md" in user:
            s = "Repeating the same key and request body MUST return the original charge identifier"
            return LLMDocExtraction(summary="payment contract", policies=[
                LLMPolicy(statement=s, strength="MUST", governs=["service:payment-service"], snippet=s)])
        if "Document path: wiki/orders-client.md" in user:
            m = re.search(r"---- DOCUMENT START ----\n(.*)---- DOCUMENT END", user, re.S)
            line = next((ln for ln in m.group(1).splitlines() if "total" in ln.lower()), "")
            return LLMDocExtraction(summary="orders client", claims=[
                LLMClaim(subject="table:order-service.orders", subject_type="TABLE", predicate="HAS_COLUMN",
                         object="column:order-service.orders.total", object_type="COLUMN", mode="asserted",
                         snippet=line[:120])])
        return LLMDocExtraction(summary="doc")
    if schema is LLMDiagramExtraction:
        return LLMDiagramExtraction(title="Architecture", date_text="January 2026", diagram_status="historical",
                                    boxes=[LLMDiagramBox(label="Stock", kind="service", canonical="service:inventory-service"),
                                           LLMDiagramBox(label="Redis read cache", kind="datastore")],
                                    arrows=[LLMDiagramArrow(source="Stock", target="Redis read cache", style="solid"),
                                            LLMDiagramArrow(source="Redis read cache", target="Stock", style="solid")])
    if schema is LLMArrowVerification:
        return LLMArrowVerification(checks=[LLMArrowCheck(index=0, present=True, direction_correct=True, style="solid"),
                                            LLMArrowCheck(index=1, present=False, direction_correct=False, style="solid")])
    if schema is LLMCodeReview:
        if "Service: inventory-service" in user:
            return LLMCodeReview(findings=[LLMCodeFinding(
                title="Off-by-one rejects ordering the last unit", category="BUG", severity="MEDIUM",
                code_snippet='if row["stock_on_hand"] <= item.quantity:',
                contract_snippet="Requests exactly equal to available stock should be accepted",
                explanation="<= rejects equal quantities", guidance_for_codegen="Use < when comparing stock to quantity.")])
        return LLMCodeReview(findings=[])
    if schema is LLMJudgeVerdict:
        ids = re.findall(r"^(C\d+) \| T(\d)", user, re.M)
        t3 = [i for i, t in ids if t == "3"]
        t1 = [i for i, t in ids if t == "1"]
        risks = []
        if t3 and t1:
            risks.append(LLMJudgeRisk(category="STALE_DOC", severity="MEDIUM", title="stub stale doc",
                                      description="stub", stale_claim_ids=t3[:1], truth_claim_ids=t1[:1],
                                      current_truth="code"))
        return LLMJudgeVerdict(risks=risks)
    return None


def test_full_llm_pipeline_with_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "structured", fake_structured)
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(config, "GRAPH_JSON", tmp_path / "g.json")
    monkeypatch.setattr(config, "AUDIT_REPORT", tmp_path / "audit.md")
    import knowledge_store.vector_db as V
    monkeypatch.setattr(V, "CHUNKS_JSON", tmp_path / "chunks.json")
    monkeypatch.setattr(V, "EMB_JSON", tmp_path / "emb.json")
    monkeypatch.setattr(config, "llm_enabled", lambda: False)  # no embeddings
    from etl_pipeline.run_etl import run
    g = run(config.REPO_ROOT, use_llm=True, run_judge=True, load=False)
    a = g.audit
    assert STATS["batch_calls"] > 0 and a["batching"]["batches"] < a["batching"]["documents"] / 3
    assert a["batching"]["individual_retries"] == 0
    assert a["integrity"]["passed"], a["integrity"]
    assert a["extraction"]["dropped_arrows"], "second-pass verification must drop the unconfirmed arrow"
    titles = {r.title for r in g.risks}
    assert "Off-by-one rejects ordering the last unit" in titles
    offby = next(r for r in g.risks if r.title.startswith("Off-by-one"))
    assert offby.category == "POLICY_VIOLATION" and {e.file_path for e in offby.evidence} >= {
        "apps/inventory-service/app.py", "api-specs/inventory-service.md"}
    # wiki claims dropped column -> deterministic candidate + judge
    assert any(c.object == "column:order-service.orders.total" and c.provenance.file_path == "wiki/orders-client.md"
               for c in g.claims)
    assert a["judge"]["clusters"] > 0
    # diagram dependency on redis is ASSERTED, never VERIFIED
    redis = [e for e in g.edges if e.target.startswith("datastore:redis") and e.source == "service:inventory-service"]
    assert redis and all(e.status != "VERIFIED" for e in redis)
    assert (tmp_path / "audit.md").read_text().startswith("# ETL Audit Report")
