# Engineering Context Service

This service builds an evidence-backed knowledge graph over `../att-hackathon-task` and serves it through MCP and REST. It does this in three layers:

1. Agentic ETL (hybrid deterministic + Gemini through litellm)
2. Neo4j as the graph, vector and full-text store
3. MCP and REST delivery

`DESIGN.md` covers the architecture, ontology, validation, trade-offs and forward view.

## Run with Docker (repo root)

```bash
cp .env.example .env          # set NEO4J_PASSWORD and GEMINI_API_KEY
docker compose up -d neo4j
docker compose --profile etl run --rm context-etl                                   # build graph -> Neo4j + out/
docker compose up -d context-api context-mcp                                        # REST :8000, MCP :8765/mcp
docker compose --profile etl run --rm context-etl python -m eval.run_eval --ablation   # out/eval_report.md
docker compose --profile etl run --rm context-etl pytest -q
```

Connect a coding agent:

```bash
claude mcp add --transport http context-service http://127.0.0.1:8765/mcp
```

## Outputs (`out/`)

| File | Contents |
|---|---|
| `validated_context_graph.json` | Sources, entities, claims, aggregated edges, risks and the audit block |
| `etl_audit_report.md` | Every risk with both-sided evidence, plus the validation-gate statistics |
| `eval_report.md` | Golden-set recall, decoys, positive controls, probes, and the ablation |

You can also browse the graph in Neo4j Browser at http://127.0.0.1:7474, for example with `MATCH (r:Risk)-[:CONCERNS]->(e) RETURN r,e`.

## REST examples

```bash
curl -s localhost:8000/context/owner/Stock
curl -s localhost:8000/context/blast-radius/users%20table
curl -s -XPOST localhost:8000/context/query -H 'content-type: application/json' \
     -d '{"query":"Why were customers charged twice?","format":"text"}'
curl -s -XPOST localhost:8000/context/validate-plan -H 'content-type: application/json' \
     -d '{"service":"order-service","proposed_items":["orders.total","redis"]}'
```

## Local development without Docker

```bash
uv sync
PYTHONPATH=. uv run python -m etl_pipeline.run_etl --no-load    # add --no-llm for deterministic-only
CS_GRAPH_BACKEND=memory PYTHONPATH=. uv run python -m eval.run_eval
```
