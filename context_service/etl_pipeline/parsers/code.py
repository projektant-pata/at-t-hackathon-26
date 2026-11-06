"""Deterministic Tier-1 code parsers (TS/JS Express & frontends, Python FastAPI, Java HttpServer).

Lightweight regex/AST extraction of: exposed routes, outbound calls (env-var base URLs resolved
through the port registry / compose host names), SQL tables touched, shared-library imports.
Production would swap these for tree-sitter queries; the output contract (Claims) stays identical.
"""
from __future__ import annotations

import ast
import re

from etl_pipeline.parsers.common import (ParseResult, RepoIndex, app_of, endpoint_id, line_of, line_text,
                                         norm_path, package_of)
from etl_pipeline.schema import EdgeType, NodeType, SourceDoc

ROUTE_TS = re.compile(r"\bapp\.(get|post|put|patch|delete)\(\s*[\"'`](/[^\"'`]*)[\"'`]")
ROUTE_USE = re.compile(r"\bapp\.use\(\s*[\"'`](/[^\"'`]*)[\"'`]")
ROUTE_PY = re.compile(r"@app\.(get|post|put|patch|delete)\(\s*[\"'](/[^\"']*)[\"']")
BASE_VAR = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*(?:process\.env|import\.meta\.env)\.(\w+)\s*\?\?\s*[\"'`]([^\"'`]+)[\"'`]", re.S)
CALL_TPL = re.compile(r"`\$\{(\w+)\}(/[^`]*)`")
SQL_STR = re.compile(r"[\"']((?:SELECT|INSERT|UPDATE|DELETE)\b[^\"']*)[\"']")
SQL_TABLE = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Za-z_]\w*)")
IMPORT_REPO = re.compile(r"from\s+[\"'](@repo/[\w-]+)[\"']")
JAVA_ENV_CALL = re.compile(r"env\(\"(\w+)\",\s*\"([^\"]+)\"\)\s*\+\s*\"(/[^\"]*)\"")
JAVA_PATH_EQ = re.compile(r"path\.equals\(\"(/[^\"]+)\"\)")
JAVA_PART = re.compile(r"parts\[(\d)\]\.equals\(\"([\w-]+)\"\)")
ENV_PY = re.compile(r"os\.environ\.get\(\s*[\"'](\w+)[\"']")
ENV_ANY = re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]+)")


def _owner_entity(res: ParseResult, src: SourceDoc, idx: RepoIndex) -> str | None:
    app = app_of(src.path)
    if app:
        kind = NodeType.FRONTEND_APP if idx.apps.get(app) == "FRONTEND_APP" else NodeType.SERVICE
        return res.entity(idx.app_eid(app), kind, app, tier=1)
    pkg = package_of(src.path)
    if pkg:
        return res.entity(f"lib:@repo/{pkg}", NodeType.LIBRARY, f"@repo/{pkg}", tier=1)
    return None


def _sql_tables(res: ParseResult, src: SourceDoc, text: str, owner: str, ex: str) -> None:
    svc = owner.split(":", 1)[1]
    for m in SQL_STR.finditer(text):
        stmt = m.group(1)
        for t in SQL_TABLE.findall(stmt):
            if t.lower() in {"migrations", "sqlite_master"}:
                continue
            tid = res.entity(f"table:{svc}.{t}", NodeType.TABLE, f"{svc}.{t}", tier=1)
            ln = line_of(text, m.start())
            res.claim(src, owner, EdgeType.READS_WRITES, tid, line_text(text, ln), ln, ex,
                      op=stmt.split()[0].upper())


def _calls_from_base_vars(res: ParseResult, src: SourceDoc, text: str, owner: str, idx: RepoIndex, ex: str) -> None:
    bases: dict[str, tuple[str, str, int]] = {}
    for m in BASE_VAR.finditer(text):
        var, env, default = m.groups()
        target = idx.resolve_url(default)
        ln = line_of(text, m.start())
        env_id = res.entity(f"env:{env}", NodeType.CONFIG_KEY, env, tier=1)
        res.claim(src, owner, EdgeType.CONFIGURED_BY, env_id, line_text(text, ln), ln, ex, default=default)
        if target:
            bases[var] = (target, env, ln)
    called: set[str] = set()
    for m in CALL_TPL.finditer(text):
        var, path = m.groups()
        if var not in bases:
            continue
        target, env, _ = bases[var]
        window = text[m.end(): m.end() + 300]
        mm = re.search(r"method:\s*[\"'](\w+)[\"']", window)
        method = mm.group(1).upper() if mm and not re.search(r"`\$\{", window[: mm.start()]) else "GET"
        if path.startswith("/graphql"):
            method = "POST"
        tgt_eid = res.entity(idx.app_eid(target), NodeType.SERVICE, target, tier=1)
        ep = res.entity(endpoint_id(tgt_eid, method, path), NodeType.API_ENDPOINT, f"{method} {norm_path(path)}",
                        tier=1, service=target, method=method, path=norm_path(path))
        ln = line_of(text, m.start())
        snippet = line_text(text, ln)
        res.claim(src, owner, EdgeType.CALLS, tgt_eid, snippet, ln, ex, via_env=env, path=norm_path(path), method=method)
        res.claim(src, owner, EdgeType.CALLS, ep, snippet, ln, ex, via_env=env)
        called.add(target)
    # base URL configured but call composed elsewhere (e.g. fetch(url) helper) -> still a configured dependency
    for var, (target, env, ln) in bases.items():
        if target not in called:
            tgt_eid = res.entity(idx.app_eid(target), NodeType.SERVICE, target, tier=1)
            res.claim(src, owner, EdgeType.CALLS, tgt_eid, line_text(text, ln), ln, ex, via_env=env,
                      evidence_kind="configured_base_url")


def parse_ts(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    ex = "parser:ts"
    owner = _owner_entity(res, src, idx)
    if not owner:
        return res
    for m in ROUTE_TS.finditer(text):
        method, path = m.group(1).upper(), m.group(2)
        ep = res.entity(endpoint_id(owner, method, path), NodeType.API_ENDPOINT, f"{method} {norm_path(path)}",
                        tier=1, service=owner.split(":", 1)[1], method=method, path=norm_path(path), raw_path=path)
        ln = line_of(text, m.start())
        res.claim(src, owner, EdgeType.EXPOSES, ep, line_text(text, ln), ln, ex)
    for m in ROUTE_USE.finditer(text):
        path = m.group(1)
        ep = res.entity(endpoint_id(owner, "POST", path), NodeType.API_ENDPOINT, f"POST {path}", tier=1,
                        service=owner.split(":", 1)[1], method="POST", path=path)
        ln = line_of(text, m.start())
        res.claim(src, owner, EdgeType.EXPOSES, ep, line_text(text, ln), ln, ex)
    _calls_from_base_vars(res, src, text, owner, idx, ex)
    _sql_tables(res, src, text, owner, ex)
    for m in IMPORT_REPO.finditer(text):
        lib = res.entity(f"lib:{m.group(1)}", NodeType.LIBRARY, m.group(1), tier=1)
        if lib != owner:
            ln = line_of(text, m.start())
            res.claim(src, owner, EdgeType.DEPENDS_ON, lib, line_text(text, ln), ln, ex)
    for m in ENV_ANY.finditer(text):
        env = m.group(1)
        if env in {"PORT", "HOST", "NODE_ENV"} or f"env:{env}" in res.entities:
            continue
        envid = res.entity(f"env:{env}", NodeType.CONFIG_KEY, env, tier=1)
        ln = line_of(text, m.start())
        res.claim(src, owner, EdgeType.CONFIGURED_BY, envid, line_text(text, ln), ln, ex)
    return res


def parse_python(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    ex = "parser:python-ast"
    owner = _owner_entity(res, src, idx)
    if not owner:
        return res
    try:  # AST pass: decorators are authoritative for routes
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and \
                            dec.func.attr in {"get", "post", "put", "patch", "delete"} and dec.args and \
                            isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                        method, path = dec.func.attr.upper(), dec.args[0].value
                        ep = res.entity(endpoint_id(owner, method, path), NodeType.API_ENDPOINT,
                                        f"{method} {norm_path(path)}", tier=1, service=owner.split(":", 1)[1],
                                        method=method, path=norm_path(path), handler=node.name, raw_path=path)
                        res.claim(src, owner, EdgeType.EXPOSES, ep, line_text(text, dec.lineno), dec.lineno, ex,
                                  line_end=node.end_lineno)
    except SyntaxError:
        for m in ROUTE_PY.finditer(text):
            method, path = m.group(1).upper(), m.group(2)
            ep = res.entity(endpoint_id(owner, method, path), NodeType.API_ENDPOINT, f"{method} {norm_path(path)}",
                            tier=1)
            ln = line_of(text, m.start())
            res.claim(src, owner, EdgeType.EXPOSES, ep, line_text(text, ln), ln, "parser:python-regex")
    _sql_tables(res, src, text, owner, ex)
    for m in ENV_PY.finditer(text):
        envid = res.entity(f"env:{m.group(1)}", NodeType.CONFIG_KEY, m.group(1), tier=1)
        ln = line_of(text, m.start())
        res.claim(src, owner, EdgeType.CONFIGURED_BY, envid, line_text(text, ln), ln, ex)
    if "sqlite3" in text:
        ds = res.entity("datastore:sqlite", NodeType.DATASTORE, "SQLite", tier=1)
        ln = line_of(text, text.index("sqlite3"))
        res.claim(src, owner, EdgeType.DEPENDS_ON, ds, line_text(text, ln), ln, ex)
    return res


def parse_java(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    ex = "parser:java"
    owner = _owner_entity(res, src, idx)
    if not owner:
        return res

    def expose(path: str, at: int):
        ep = res.entity(endpoint_id(owner, "GET", path), NodeType.API_ENDPOINT, f"GET {norm_path(path)}", tier=1,
                        service=owner.split(":", 1)[1], method="GET", path=norm_path(path))
        ln = line_of(text, at)
        res.claim(src, owner, EdgeType.EXPOSES, ep, line_text(text, ln), ln, ex)

    for m in JAVA_PATH_EQ.finditer(text):
        expose(m.group(1), m.start())
    parts = {}
    for m in JAVA_PART.finditer(text):
        parts.setdefault(int(m.group(1)), []).append((m.group(2), m.start()))
    if 1 in parts and 2 in parts:  # split-path router: /<p1>/<p2>/{id}[/<p4>]
        base = f"/{parts[1][0][0]}/{parts[2][0][0]}/{{}}"
        expose(base, parts[2][0][1])
        for seg, at in parts.get(4, []):
            expose(f"{base}/{seg}", at)
    for m in JAVA_ENV_CALL.finditer(text):
        env, default, path = m.groups()
        target = idx.resolve_url(default)
        ln = line_of(text, m.start())
        envid = res.entity(f"env:{env}", NodeType.CONFIG_KEY, env, tier=1)
        res.claim(src, owner, EdgeType.CONFIGURED_BY, envid, line_text(text, ln), ln, ex, default=default)
        if target:
            tgt = res.entity(idx.app_eid(target), NodeType.SERVICE, target, tier=1)
            ep = res.entity(endpoint_id(tgt, "GET", path + "{}"), NodeType.API_ENDPOINT, f"GET {norm_path(path + '{}')}",
                            tier=1, service=target, method="GET", path=norm_path(path + "{}"))
            snippet = line_text(text, ln)
            res.claim(src, owner, EdgeType.CALLS, tgt, snippet, ln, ex, via_env=env, path=norm_path(path + "{}"))
            res.claim(src, owner, EdgeType.CALLS, ep, snippet, ln, ex, via_env=env)
    return res


def parse_code(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    if src.path.endswith(".py"):
        return parse_python(src, text, idx)
    if src.path.endswith(".java"):
        return parse_java(src, text, idx)
    return parse_ts(src, text, idx)
