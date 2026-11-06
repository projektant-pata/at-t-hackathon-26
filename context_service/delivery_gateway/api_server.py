"""REST gateway for humans, CI/CD gates and dashboards (OpenAPI docs at /docs).

  uvicorn delivery_gateway.api_server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import config
from knowledge_store.hybrid_retriever import ContextService

app = FastAPI(title="Engineering Context Service", version="0.1.0",
              description="Evidence-backed engineering context: relations, owners, contradictions, security risks.")


@lru_cache(maxsize=1)
def svc() -> ContextService:
    return ContextService()


def _check(obj):
    if isinstance(obj, dict) and "error" in obj:
        raise HTTPException(404, obj["error"])
    return obj


class QueryIn(BaseModel):
    query: str
    include_unverified: bool = False
    format: str = "json"  # json | text


class PlanIn(BaseModel):
    service: str
    proposed_items: list[str]


@app.get("/health")
def health():
    ok = config.GRAPH_JSON.exists() if config.GRAPH_BACKEND == "memory" else True
    try:
        n = len(svc().repo.entity_index())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"graph backend unavailable: {e}") from e
    return {"status": "ok" if ok and n else "empty", "backend": config.GRAPH_BACKEND, "entities": n}


@app.post("/context/query")
def query(body: QueryIn):
    ctx = svc().retrieve(body.query, body.include_unverified)
    if body.format == "text":
        return PlainTextResponse(svc().serialize(ctx))
    return {**ctx, "serialized": svc().serialize(ctx)}


@app.get("/context/entity/{name:path}")
def entity(name: str):
    return _check(svc().get_entity(name))


@app.get("/context/blast-radius/{name:path}")
def blast_radius(name: str, max_hops: int = Query(3, ge=1, le=5)):
    return _check(svc().change_impact(name, max_hops))


@app.get("/context/owner/{name:path}")
def owner(name: str):
    return _check(svc().who_owns(name))


@app.get("/context/security/{target:path}")
def security(target: str):
    return _check(svc().security_guidance(target))


@app.get("/context/contradictions")
def contradictions(entity: str | None = None):
    return svc().contradictions(entity)


@app.get("/context/path")
def path(source: str, target: str):
    return _check(svc().dependency_path(source, target))


@app.post("/context/validate-plan")
def validate_plan(body: PlanIn):
    return svc().validate_plan(body.service, body.proposed_items)


@app.get("/risks")
def risks(category: str | None = None, severity: str | None = None):
    return svc().list_risks(category, severity)


@app.get("/audit/report", response_class=PlainTextResponse)
def audit_report():
    if not config.AUDIT_REPORT.exists():
        raise HTTPException(404, "run the ETL first")
    return config.AUDIT_REPORT.read_text()


@app.get("/graph/export")
def graph_export():
    """Full graph (entities, aggregated edges with status/confidence, risks) as JSON."""
    return svc().repo.export()
