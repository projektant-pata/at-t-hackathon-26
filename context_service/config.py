"""Central configuration. Everything is overridable through environment variables (see ../.env.example)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

REPO_ROOT = Path(os.environ.get("REPO_ROOT", BASE_DIR.parent / "att-hackathon-task")).resolve()
OUT_DIR = Path(os.environ.get("CS_OUT_DIR", BASE_DIR / "out")).resolve()
CACHE_DIR = Path(os.environ.get("CS_CACHE_DIR", BASE_DIR / ".cache")).resolve()

# --- LLM / VLM (litellm model strings) -------------------------------------
EXTRACT_MODEL = os.environ.get("CS_EXTRACT_MODEL", "gemini/gemini-3.5-flash")
JUDGE_MODEL = os.environ.get("CS_JUDGE_MODEL", "gemini/gemini-3.5-flash")
VISION_MODEL = os.environ.get("CS_VISION_MODEL", EXTRACT_MODEL)
EMBED_MODEL = os.environ.get("CS_EMBED_MODEL", "gemini/gemini-embedding-001")
EMBED_DIM = int(os.environ.get("CS_EMBED_DIM", "768"))
LLM_CONCURRENCY = int(os.environ.get("CS_LLM_CONCURRENCY", "4"))
LLM_TIMEOUT_S = int(os.environ.get("CS_LLM_TIMEOUT", "180"))


def llm_enabled() -> bool:
    """LLM stages run only when a key is present and not explicitly disabled (ablation / offline mode)."""
    if os.environ.get("CS_DISABLE_LLM", "").lower() in {"1", "true", "yes"}:
        return False
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("CS_ALLOW_KEYLESS_LLM"))


# --- Storage -----------------------------------------------------------------
GRAPH_BACKEND = os.environ.get("CS_GRAPH_BACKEND", "neo4j")  # "neo4j" | "memory"
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

GRAPH_JSON = OUT_DIR / "validated_context_graph.json"
AUDIT_REPORT = OUT_DIR / "etl_audit_report.md"

# --- Crawl --------------------------------------------------------------------
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".runtime", ".turbo", "dist", "build",
                ".agents", ".next", ".expo", "public"}
IGNORED_FILES = {"pnpm-lock.yaml", "skills-lock.json", "LICENSE", ".npmrc"}
MAX_TEXT_BYTES = 200_000

# --- Retrieval ------------------------------------------------------------------
MAX_CONTEXT_EDGES = int(os.environ.get("CS_MAX_CONTEXT_EDGES", "30"))
SEED_K = int(os.environ.get("CS_SEED_K", "6"))
