"""Unit tests: deterministic parsers, metadata parsing, grounding gate, resolver scope guard,
validator candidates and edge aggregation. No network / LLM / Neo4j required."""
from __future__ import annotations

import pytest

import config
from etl_pipeline import extractors as X
from etl_pipeline.grounding import ground
from etl_pipeline.parsers.common import ParseResult, build_repo_index, norm_path
from etl_pipeline.resolver import Resolver
from etl_pipeline.router import crawl, parse_metadata
from etl_pipeline.run_etl import deterministic_stage
from etl_pipeline.schema import EdgeType, LLMAlias, LLMClaim, LLMDocExtraction
from etl_pipeline.validator import aggregate_edges, find_candidates

pytestmark = pytest.mark.skipif(not config.REPO_ROOT.exists(), reason="corpus not available")


@pytest.fixture(scope="module")
def files():
    return crawl(config.REPO_ROOT)


@pytest.fixture(scope="module")
def idx():
    return build_repo_index(config.REPO_ROOT)


@pytest.fixture()
def base(files, idx):
    return deterministic_stage(files, idx)


# ---------------------------------------------------------------- router / metadata
def test_every_markdown_doc_gets_kind_and_most_get_dates(files):
    docs = [f.source for f in files if f.source.path.endswith(".md") and f.source.tier == 3
            and f.source.kind not in {"readme"}]
    assert len(docs) >= 60
    dated = [d for d in docs if d.date]
    assert len(dated) / len(docs) > 0.9


@pytest.mark.parametrize("rel,kind,text,expect", [
    ("wiki/accounts-http.md", "wiki", "# Accounts HTTP\n\nLast edited 2026-02-03 · Identity Platform\n",
     {"date": "2026-02-03", "owner": "Identity Platform"}),
    ("wiki/inventory-service.md", "wiki", "# Stock / inventory-service\n\nCore Platform handbook · 2026-02-17\n",
     {"date": "2026-02-17", "owner": "Core Platform"}),
    ("docs/adr/003-stock-cache.md", "adr", "# Remove cache\n\nDate: 2026-06-04\nStatus: Accepted\n",
     {"date": "2026-06-04", "status": "Accepted", "record_id": "ADR-003"}),
    ("issues/PAY-118.md", "issue", "# PAY-118: Duplicate charge\n\nInvestigating · 2026-08-20\n",
     {"date": "2026-08-20", "status": "Investigating", "record_id": "PAY-118", "owner": None}),
    ("incidents/2026-08-19-payment-retry.md", "incident", "# Two charges\n\nInvestigation in progress.\n",
     {"date": "2026-08-19", "record_id": "INC-2026-08-19"}),
    ("releases/2026-07-v1.4.md", "release", "# Handover 1.4\n\n2026-07 release record\n",
     {"date": "2026-07", "record_id": "v1.4"}),
])
def test_metadata_line_parsing(rel, kind, text, expect):
    meta = parse_metadata(rel, kind, text)
    for k, v in expect.items():
        assert meta[k] == v, (k, meta)


# ---------------------------------------------------------------- deterministic parsers
def test_sql_replay_final_state(base):
    users = base.entities["table:user-service.users"].properties
    assert "account_status" in users["current_columns"] and "customer_tier" in users["current_columns"]
    assert "status" not in users["current_columns"] and users["removed_columns"]["status"].startswith("renamed")
    orders = base.entities["table:order-service.orders"].properties
    assert "total_minor" in orders["current_columns"] and orders["removed_columns"] == {"total": "dropped"}
    assert "location" in base.entities["table:inventory-service.products"].properties["current_columns"]


def test_dropped_column_claims_are_deprecated(base):
    modes = {c.mode for c in base.claims if c.object == "column:order-service.orders.total"
             and c.predicate == EdgeType.HAS_COLUMN}
    assert modes == {"deprecated"}


def _calls(res):
    return {(c.subject, c.object) for c in res.claims if c.predicate == EdgeType.CALLS and c.provenance.tier == 1
            and c.object.startswith(("service:", "app:"))}


def test_call_graph_matches_code(base):
    calls = _calls(base)
    for pair in [("service:api-gateway", "service:user-service"), ("service:api-gateway", "service:order-service"),
                 ("service:api-gateway", "service:payment-service"), ("service:api-gateway", "service:inventory-service"),
                 ("service:order-service", "service:user-service"), ("service:order-service", "service:inventory-service"),
                 ("service:order-service", "service:payment-service"), ("service:hr-service", "service:user-service"),
                 ("app:admin", "service:inventory-service"), ("app:storefront", "service:api-gateway")]:
        assert pair in calls, pair
    assert not any(s == "app:blog" for s, _ in calls)
    assert not any(s == "service:payment-service" for s, _ in calls)


def test_routes_per_language(base):
    eps = {e for e in base.entities if e.startswith("endpoint:")}
    assert "endpoint:order-service:GET /v1/orders/{}" in eps                 # TS express
    assert "endpoint:inventory-service:POST /v1/availability" in eps        # python ast
    assert "endpoint:hr-service:GET /v1/employees/{}/customer-account" in eps  # java
    assert norm_path("/v2/users/${encodeURIComponent(id)}") == "/v2/users/{}"


def test_env_resolution_through_port_registry(idx):
    assert idx.resolve_url("http://127.0.0.1:5005") == "inventory-service"
    assert idx.resolve_url("http://user-service:5002") == "user-service"
    assert idx.apps["admin"] == "FRONTEND_APP" and idx.apps["inventory-service"] == "SERVICE"


def test_codeowners_ownership(base):
    owners = {(c.subject, c.object) for c in base.claims if c.predicate == EdgeType.OWNED_BY}
    assert ("service:inventory-service", "team:supply-operations") in owners


def test_security_rules(base):
    titles = " | ".join(r.title for r in base.risks)
    assert "GET /v1/orders/:id" in titles                 # IDOR
    assert "Idempotency key recorded but never checked" in titles
    assert "Sensitive record logged" in titles             # hr-service PII log line
    assert "x-demo-user-id" in titles
    idor = next(r for r in base.risks if "GET /v1/orders/:id" in r.title)
    assert idor.evidence[0].file_path == "apps/order-service/src/index.ts" and idor.evidence[0].line_start == 40


# ---------------------------------------------------------------- grounding gate
def test_grounding_exact_fuzzy_and_reject():
    text = "Line one.\nCore Platform owns Stock and its Redis read cache. Escalate\navailability incidents.\n"
    ok, l1, l2, s = ground("Core Platform owns Stock and its **Redis** read cache.", text)
    assert ok and l1 == 2 and s == 1.0
    ok, *_ = ground("Core Platform own Stock and it's Redis read cache.", text)
    assert ok  # fuzzy >= 0.9
    ok, *_ = ground("Supply Operations owns the Redis cluster", text)
    assert not ok


# ---------------------------------------------------------------- LLM output conversion (fabricated outputs)
def _routed(files, path):
    return next(f for f in files if f.source.path == path)


def test_doc_extraction_grounds_resolves_and_flags_drift(files, base):
    resolver = Resolver(base)
    audit = X.ExtractionAudit()
    wiki = _routed(files, "wiki/inventory-service.md")
    ext = LLMDocExtraction(
        summary="Stock wiki",
        claims=[
            LLMClaim(subject="Stock", subject_type="SERVICE", predicate="OWNED_BY", object="Core Platform",
                     object_type="TEAM", mode="asserted", snippet="Core Platform owns Stock and its Redis read cache."),
            LLMClaim(subject="service:inventory-service", subject_type="SERVICE", predicate="DEPENDS_ON",
                     object="datastore:redis", object_type="DATASTORE", mode="asserted",
                     snippet="Core Platform owns Stock and its Redis read cache."),
            LLMClaim(subject="service:inventory-service", subject_type="SERVICE", predicate="CALLS",
                     object="service:payment-service", object_type="SERVICE", mode="asserted",
                     snippet="Stock calls the Charge Ledger for refunds."),  # hallucinated -> must be rejected
        ],
        aliases=[LLMAlias(alias="Stock", canonical="service:inventory-service",
                          snippet="Stock runs on port 5005 in the development environment.")])
    X.apply_aliases(ext, wiki, resolver, audit)
    X.apply_doc_extraction(ext, wiki, resolver, base, audit)
    assert audit.grounded == 2 and len(audit.rejected_ungrounded) == 1
    own = [c for c in base.claims if c.predicate == EdgeType.OWNED_BY and c.provenance.file_path == wiki.source.path]
    assert own and own[0].subject == "service:inventory-service" and own[0].object == "team:core-platform"
    kinds = {k.kind for k in find_candidates(base)}
    assert {"ownership_drift", "unverified_dependency"} <= kinds
    edges = {(e.source, e.type.value, e.target): e for e in aggregate_edges(base)}
    assert edges[("service:inventory-service", "DEPENDS_ON", "datastore:redis")].status == "ASSERTED"
    assert edges[("app:admin", "CALLS", "service:inventory-service")].status == "VERIFIED"


def test_scope_guard_blocks_archive_alias(files, base):
    resolver = Resolver(base)
    ok = resolver.add_alias("PaymentService", "service:payment-service",
                            "archive/conference-registration/README.md", "archive")
    assert not ok and resolver.refused
    eid = resolver.resolve("service:payment-service", "SERVICE", scope="archive",
                           scope_prefix="archive/conference-registration::")
    assert eid.startswith("archive/conference-registration::")


def test_proposed_adr_claims_are_proposed(files, base):
    resolver = Resolver(base)
    audit = X.ExtractionAudit()
    adr = _routed(files, "docs/adr/004-people-sso.md")
    first_sentence = next(ln for ln in adr.text.splitlines() if len(ln) > 40)[:120]
    ext = LLMDocExtraction(summary="sso", claims=[
        LLMClaim(subject="service:hr-service", subject_type="SERVICE", predicate="DEPENDS_ON",
                 object="service:user-service", object_type="SERVICE", mode="asserted", snippet=first_sentence)])
    X.apply_doc_extraction(ext, adr, resolver, base, audit)
    c = [c for c in base.claims if c.provenance.file_path == adr.source.path and c.predicate == EdgeType.DEPENDS_ON]
    assert c and c[0].mode == "proposed"


def test_ontology_domain_range():
    from etl_pipeline.schema import type_check
    assert type_check("CALLS", "SERVICE", "SERVICE")
    assert not type_check("CALLS", "SERVICE", "TABLE")
    assert not type_check("HAS_COLUMN", "SERVICE", "COLUMN")
    assert type_check("OWNED_BY", "SERVICE", "TEAM") and not type_check("OWNED_BY", "SERVICE", "SERVICE")


def test_name_resolution_prefix_and_generic_words():
    from knowledge_store.graph_db import resolve_name
    idx = {"datastore:redis": {"id": "datastore:redis", "name": "redis", "type": "DATASTORE", "aliases": [], "scope": "live"},
           "table:user-service.users": {"id": "table:user-service.users", "name": "user-service.users", "type": "TABLE",
                                        "aliases": [], "scope": "live"},
           "service:inventory-service": {"id": "service:inventory-service", "name": "inventory-service",
                                         "type": "SERVICE", "aliases": ["Stock"], "scope": "live"}}
    assert resolve_name("redis cache", idx) == "datastore:redis"
    assert resolve_name("users table", idx) == "table:user-service.users"
    assert resolve_name("Stock", idx) == "service:inventory-service"
