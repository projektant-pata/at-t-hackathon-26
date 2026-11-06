"""Deterministic parsers for contracts (OpenAPI, GraphQL) and deployment/ownership config
(compose, .env.example, CODEOWNERS, package.json, port registry)."""
from __future__ import annotations

import json
import re

import yaml

from etl_pipeline.parsers.common import (ParseResult, RepoIndex, app_of, endpoint_id, line_of, line_text, norm_path,
                                         slug)
from etl_pipeline.schema import EdgeType, NodeType, SourceDoc

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _find_line(text: str, needle: str) -> int | None:
    i = text.find(needle)
    return line_of(text, i) if i >= 0 else None


def parse_openapi(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        return res
    servers = spec.get("servers") or []
    target = idx.resolve_url(servers[0]["url"]) if servers else None
    if not target:
        stem = src.path.split("/")[-1].split(".")[0]
        target = stem if stem in idx.apps else None
    if not target:
        return res
    mode = "deprecated" if src.kind == "api_history" else "asserted"
    ex = "parser:openapi"
    svc = res.entity(idx.app_eid(target), NodeType.SERVICE, target)
    info = spec.get("info", {})
    res.entities[svc].properties.setdefault("contract_versions", [])
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in ops.items():
            if method not in HTTP_METHODS:
                continue
            fields: list[str] = []
            for resp in (op.get("responses") or {}).values():
                for media in (resp.get("content") or {}).values():
                    schema = media.get("schema") or {}
                    schema = schema.get("items", schema)
                    fields.extend((schema.get("properties") or {}).keys())
            ep = res.entity(endpoint_id(svc, method, path), NodeType.API_ENDPOINT, f"{method.upper()} {norm_path(path)}",
                            tier=src.tier, service=target, method=method.upper(), path=norm_path(path))
            ln = _find_line(text, f'"{path}"')
            res.claim(src, svc, EdgeType.EXPOSES, ep, f'{method.upper()} {path}: {op.get("summary", "")}', ln, ex,
                      mode=mode, response_fields=sorted(set(fields)), contract_version=info.get("version"))
    return res


GQL_TYPE = re.compile(r"type\s+(\w+)\s*\{([^}]*)\}")


def parse_graphql(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    app = app_of(src.path)
    if not app:
        return res
    owner = res.entity(idx.app_eid(app), NodeType.SERVICE, app)
    for m in GQL_TYPE.finditer(text):
        name, body = m.groups()
        fields = [f.split(":")[0].split("(")[0].strip() for f in body.split(",") if f.strip()]
        ln = line_of(text, m.start())
        if name == "Query":
            for f in fields:
                ep = res.entity(f"endpoint:{app}:GRAPHQL Query.{f}", NodeType.API_ENDPOINT, f"GraphQL Query.{f}",
                                tier=2, service=app, method="GRAPHQL", path=f"Query.{f}")
                res.claim(src, owner, EdgeType.EXPOSES, ep, line_text(text, ln), ln, "parser:graphql", mode="asserted")
        else:
            t = res.entity(f"concept:graphql.{app}.{name}", NodeType.CONCEPT, f"GraphQL type {name}", tier=2,
                           fields=fields)
            res.claim(src, owner, EdgeType.EXPOSES, t, line_text(text, ln), ln, "parser:graphql", mode="asserted",
                      fields=fields)
    return res


def parse_codeowners(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    effective = None
    if m := re.search(r"(?i)effective\s+(20\d\d-\d\d-\d\d)", text):
        effective = m.group(1)
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        path, owners = parts[0], parts[1:]
        segs = path.strip("/").split("/")
        if segs[0] == "apps" and len(segs) > 1:
            subj = res.entity(idx.app_eid(segs[1]), NodeType.SERVICE if idx.apps.get(segs[1]) != "FRONTEND_APP"
                              else NodeType.FRONTEND_APP, segs[1])
        elif segs[0] == "archive" and len(segs) > 1:
            subj = res.entity(f"archive/{segs[1]}::service:{segs[1]}", NodeType.CONCEPT, f"{segs[1]} (archived)",
                              scope="archive", tier=4)
        else:
            continue
        for o in owners:
            team_name = o.split("/")[-1].replace("-", " ").title()
            team = res.entity(f"team:{slug(team_name)}", NodeType.TEAM, team_name, tier=1, handle=o)
            res.claim(src, subj, EdgeType.OWNED_BY, team, line, ln, "parser:codeowners", effective_from=effective)
    return res


def parse_compose(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return res
    ex = "parser:compose"
    for name, svc in (doc.get("services") or {}).items():
        if name not in idx.apps:
            continue
        owner = res.entity(idx.app_eid(name), NodeType.SERVICE if idx.apps[name] == "SERVICE" else NodeType.FRONTEND_APP, name)
        du = res.entity(f"deploy:compose.{name}", NodeType.DEPLOY_UNIT, f"compose service {name}", tier=1,
                        profiles=svc.get("profiles"), ports=svc.get("ports"))
        ln = _find_line(text, f"\n  {name}:")
        ln = ln + 1 if ln else None
        res.claim(src, owner, EdgeType.DEPLOYED_AS, du, f"{name}: profiles={svc.get('profiles')} ports={svc.get('ports')}",
                  ln, ex)
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        for key, val in env.items():
            envid = res.entity(f"env:{key}", NodeType.CONFIG_KEY, key, tier=1)
            eln = _find_line(text, f"{key}: {val}") or _find_line(text, key)
            res.claim(src, owner, EdgeType.CONFIGURED_BY, envid, f"{key}: {val}", eln, ex, value=str(val))
            target = idx.resolve_url(str(val))
            if target:
                tgt = res.entity(idx.app_eid(target), NodeType.SERVICE, target)
                res.claim(src, owner, EdgeType.CALLS, tgt, f"{key}: {val}", eln, ex, via_env=key,
                          evidence_kind="deployment_wiring")
        deps = svc.get("depends_on") or []
        for dep in (deps.keys() if isinstance(deps, dict) else deps):
            if dep in idx.apps:
                tgt = res.entity(idx.app_eid(dep), NodeType.SERVICE, dep)
                res.claim(src, owner, EdgeType.DEPENDS_ON, tgt, f"{name} depends_on {dep}", ln, ex,
                          evidence_kind="startup_order")
    # record which infra the deployment does NOT contain (useful negative evidence, e.g. no redis)
    res.entity("deploy:compose", NodeType.DEPLOY_UNIT, "compose.yaml", tier=1,
               services=sorted((doc.get("services") or {}).keys()))
    return res


def parse_env_example(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    app = app_of(src.path)
    if not app:
        return res
    owner = res.entity(idx.app_eid(app), NodeType.FRONTEND_APP if idx.apps.get(app) == "FRONTEND_APP" else NodeType.SERVICE, app)
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key in {"PORT", "HOST"}:
            continue
        envid = res.entity(f"env:{key}", NodeType.CONFIG_KEY, key, tier=1)
        res.claim(src, owner, EdgeType.CONFIGURED_BY, envid, line, ln, "parser:dotenv", value=val)
        target = idx.resolve_url(val)
        if target and target != app:
            tgt = res.entity(idx.app_eid(target), NodeType.SERVICE, target)
            res.claim(src, owner, EdgeType.CALLS, tgt, line, ln, "parser:dotenv", via_env=key,
                      evidence_kind="runtime_configuration")
    return res


def parse_package_json(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    try:
        pkg = json.loads(text)
    except json.JSONDecodeError:
        return res
    app = app_of(src.path)
    if app:
        owner = res.entity(idx.app_eid(app), NodeType.FRONTEND_APP if idx.apps.get(app) == "FRONTEND_APP" else NodeType.SERVICE, app)
    elif src.path.startswith("packages/"):
        owner = res.entity(f"lib:{pkg.get('name')}", NodeType.LIBRARY, pkg.get("name"))
    else:
        return res
    for dep, ver in (pkg.get("dependencies") or {}).items():
        lib = res.entity(f"lib:{dep}", NodeType.LIBRARY, dep, tier=1, shared=dep.startswith("@repo/"))
        res.claim(src, owner, EdgeType.DEPENDS_ON, lib, f'"{dep}": "{ver}"', _find_line(text, f'"{dep}"'),
                  "parser:package-json", version=ver)
    return res


def parse_ports(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    for name, port in json.loads(text).items():
        if name in idx.apps:
            res.entity(idx.app_eid(name), NodeType.FRONTEND_APP if idx.apps[name] == "FRONTEND_APP" else NodeType.SERVICE,
                       name, tier=1, port=port)
    return res


def parse_spec_or_config(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    name = src.path.split("/")[-1]
    if src.kind in {"openapi", "api_history"} and name.endswith(".json"):
        return parse_openapi(src, text, idx)
    if src.kind == "graphql":
        return parse_graphql(src, text, idx)
    if src.kind == "codeowners":
        return parse_codeowners(src, text, idx)
    if name in {"compose.yaml", "docker-compose.yml"}:
        return parse_compose(src, text, idx)
    if name == ".env.example":
        return parse_env_example(src, text, idx)
    if name == "package.json":
        return parse_package_json(src, text, idx)
    if src.path == "config/ports.json":
        return parse_ports(src, text, idx)
    return ParseResult()
