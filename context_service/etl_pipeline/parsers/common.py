"""Shared helpers for deterministic parsers: canonical ids, claim builders, line lookup."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from etl_pipeline.schema import Claim, EdgeType, Entity, NodeType, Provenance, Risk, SourceDoc


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def norm_path(path: str) -> str:
    """'/v1/orders/:id' / '/v1/orders/${encodeURIComponent(id)}' / '/v1/orders/{id}' -> '/v1/orders/{}'"""
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    path = re.sub(r":[A-Za-z_]\w*", "{}", path)
    path = re.sub(r"(?<=/)(cus|emp|ord|ch)-[0-9]{3}\b", "{}", path)  # concrete example ids in prose
    return path.rstrip("/") or "/"


def scope_prefix(src: SourceDoc) -> str:
    if src.scope == "archive":
        parts = src.path.split("/")
        return f"archive/{parts[1]}::" if len(parts) > 1 else "archive::"
    return ""


def eid(kind: str, name: str, prefix: str = "") -> str:
    return f"{prefix}{kind}:{name}"


def endpoint_id(service_eid: str, method: str, path: str) -> str:
    svc = service_eid.split(":", 1)[1] if ":" in service_eid else service_eid
    prefix = service_eid.split("service:")[0] if "::" in service_eid else ""
    return f"{prefix}endpoint:{svc}:{method.upper()} {norm_path(path)}"


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def line_text(text: str, line_no: int) -> str:
    lines = text.splitlines()
    return lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""


@dataclass
class ParseResult:
    entities: dict[str, Entity] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)

    def entity(self, id: str, type: NodeType, name: str | None = None, tier: int = 9, scope: str = "live",
               **props) -> str:
        ent = self.entities.get(id)
        if ent is None:
            ent = Entity(id=id, type=type, name=name or id.split(":", 1)[-1], scope=scope, grounded_tier=tier)
            self.entities[id] = ent
        ent.grounded_tier = min(ent.grounded_tier, tier)
        for k, v in props.items():
            if v is not None:
                ent.properties[k] = v
        return id

    def claim(self, src: SourceDoc, subject: str, pred: EdgeType, obj: str, snippet: str, line: int | None,
              extractor: str, mode: str = "observed", line_end: int | None = None, **props) -> Claim:
        c = Claim(subject=subject, predicate=pred, object=obj, mode=mode, properties=props,
                  provenance=Provenance(file_path=src.path, modality=src.modality, tier=src.tier,
                                        snippet=snippet[:400], line_start=line, line_end=line_end or line,
                                        effective_date=src.date, doc_kind=src.kind, extractor=extractor))
        c.compute_id()
        self.claims.append(c)
        return c

    def merge(self, other: "ParseResult") -> None:
        for id, ent in other.entities.items():
            if id in self.entities:
                cur = self.entities[id]
                cur.grounded_tier = min(cur.grounded_tier, ent.grounded_tier)
                cur.properties.update({k: v for k, v in ent.properties.items() if k not in cur.properties})
                cur.aliases = sorted(set(cur.aliases) | set(ent.aliases))
                if ent.type != NodeType.CONCEPT and cur.type == NodeType.CONCEPT:
                    cur.type = ent.type
            else:
                self.entities[id] = ent
        self.claims.extend(other.claims)
        self.risks.extend(other.risks)


@dataclass
class RepoIndex:
    """Facts every parser needs: which apps exist, which are backends, port -> app map."""
    root: Path
    apps: dict[str, str]          # app name -> "SERVICE" | "FRONTEND_APP"
    port_to_app: dict[int, str]
    host_to_app: dict[str, str]

    def app_eid(self, app: str) -> str:
        kind = self.apps.get(app, "SERVICE")
        return f"{'app' if kind == 'FRONTEND_APP' else 'service'}:{app}"

    def resolve_url(self, url: str) -> str | None:
        m = re.match(r"https?://([^:/]+)(?::(\d+))?", url)
        if not m:
            return None
        host, port = m.group(1), m.group(2)
        if host in self.host_to_app:
            return self.host_to_app[host]
        if port and int(port) in self.port_to_app:
            return self.port_to_app[int(port)]
        return None


FRONTEND_MARKERS = {"react", "react-dom", "next", "expo", "vite", "@remix-run/react", "react-native"}


def build_repo_index(root: Path) -> RepoIndex:
    apps: dict[str, str] = {}
    for pkg in sorted((root / "apps").glob("*/package.json")):
        deps = json.loads(pkg.read_text()).get("dependencies", {}) | json.loads(pkg.read_text()).get("devDependencies", {})
        apps[pkg.parent.name] = "FRONTEND_APP" if FRONTEND_MARKERS & set(deps) else "SERVICE"
    for d in sorted((root / "apps").iterdir()) if (root / "apps").exists() else []:
        if d.is_dir() and d.name not in apps:
            apps[d.name] = "SERVICE"  # python / java services have no package.json
    port_to_app: dict[int, str] = {}
    ports_file = root / "config" / "ports.json"
    if ports_file.exists():
        port_to_app = {int(p): name for name, p in json.loads(ports_file.read_text()).items()}
    host_to_app = {name: name for name in apps}
    return RepoIndex(root=root, apps=apps, port_to_app=port_to_app, host_to_app=host_to_app)


def app_of(path: str) -> str | None:
    parts = path.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "apps" else None


def package_of(path: str) -> str | None:
    parts = path.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "packages" else None
