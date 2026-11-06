"""Replay SQL migrations in order -> final schema state + column history (renames / drops).

The *final* state is Tier-1 runtime truth; history lets the validator say *why* a doc is stale
("orders.total was dropped by 002-minor-units.sql") instead of just "column missing".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from etl_pipeline.parsers.common import ParseResult, app_of, line_of, line_text, scope_prefix
from etl_pipeline.schema import EdgeType, NodeType, SourceDoc

CREATE_RE = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\);", re.S | re.I)
ADD_RE = re.compile(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)\s+([^;]*);", re.I)
RENAME_RE = re.compile(r"ALTER TABLE\s+(\w+)\s+RENAME COLUMN\s+(\w+)\s+TO\s+(\w+)\s*;", re.I)
DROP_RE = re.compile(r"ALTER TABLE\s+(\w+)\s+DROP COLUMN\s+(\w+)\s*;", re.I)
CONSTRAINT_PREFIX = ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")


def split_top_level(body: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


@dataclass
class ColumnState:
    name: str
    type: str
    status: str = "current"          # current | dropped | renamed
    history: list[dict] = field(default_factory=list)


@dataclass
class TableState:
    name: str
    columns: dict[str, ColumnState] = field(default_factory=dict)


def replay(migrations: list[tuple[SourceDoc, str]], res: ParseResult, owner_eid: str | None, svc: str,
           prefix: str = "") -> dict[str, TableState]:
    tables: dict[str, TableState] = {}
    ex = "parser:sql-replay"

    def tid(t: str) -> str:
        return f"{prefix}table:{svc}.{t}"

    def cid(t: str, c: str) -> str:
        return f"{prefix}column:{svc}.{t}.{c}"

    for src, text in sorted(migrations, key=lambda x: x[0].path):
        tier, scope = src.tier, src.scope
        for m in CREATE_RE.finditer(text):
            t = m.group(1)
            ln = line_of(text, m.start())
            table = tables.setdefault(t, TableState(t))
            res.entity(tid(t), NodeType.TABLE, f"{svc}.{t}", tier=tier, scope=scope, service=svc,
                       defined_in=src.path)
            if owner_eid:
                res.claim(src, owner_eid, EdgeType.READS_WRITES, tid(t), line_text(text, ln), ln, ex, op="OWNS")
            for col_def in split_top_level(m.group(2)):
                if col_def.upper().startswith(CONSTRAINT_PREFIX):
                    continue
                parts = col_def.split()
                name, ctype = parts[0], parts[1] if len(parts) > 1 else ""
                cl = line_of(text, m.start() + m.group(0).find(col_def))
                table.columns[name] = ColumnState(name, ctype, history=[{"action": "create", "file": src.path, "line": cl}])
                res.entity(cid(t, name), NodeType.COLUMN, f"{t}.{name}", tier=tier, scope=scope, data_type=ctype,
                           table=f"{svc}.{t}")
                res.claim(src, tid(t), EdgeType.HAS_COLUMN, cid(t, name), line_text(text, cl), cl, ex, data_type=ctype)
        for m in ADD_RE.finditer(text):
            t, name, rest = m.groups()
            ln = line_of(text, m.start())
            table = tables.setdefault(t, TableState(t))
            table.columns[name] = ColumnState(name, rest.split()[0], history=[{"action": "add", "file": src.path, "line": ln}])
            res.entity(cid(t, name), NodeType.COLUMN, f"{t}.{name}", tier=tier, scope=scope, data_type=rest.split()[0],
                       table=f"{svc}.{t}")
            res.claim(src, tid(t), EdgeType.HAS_COLUMN, cid(t, name), line_text(text, ln), ln, ex,
                      data_type=rest.split()[0])
        for m in RENAME_RE.finditer(text):
            t, old, new = m.groups()
            ln = line_of(text, m.start())
            table = tables.setdefault(t, TableState(t))
            col = table.columns.pop(old, ColumnState(old, ""))
            col.status = "renamed"
            col.history.append({"action": f"renamed to {new}", "file": src.path, "line": ln})
            table.columns[f"__old__{old}"] = col
            table.columns[new] = ColumnState(new, col.type, history=[{"action": f"renamed from {old}", "file": src.path, "line": ln}])
            res.entity(cid(t, new), NodeType.COLUMN, f"{t}.{new}", tier=tier, scope=scope, data_type=col.type,
                       table=f"{svc}.{t}")
            res.claim(src, tid(t), EdgeType.HAS_COLUMN, cid(t, new), line_text(text, ln), ln, ex)
            res.claim(src, cid(t, old), EdgeType.DEPRECATED_BY, cid(t, new), line_text(text, ln), ln, ex,
                      reason="renamed")
        for m in DROP_RE.finditer(text):
            t, name = m.groups()
            ln = line_of(text, m.start())
            table = tables.setdefault(t, TableState(t))
            if name in table.columns:
                col = table.columns.pop(name)
                col.status = "dropped"
                col.history.append({"action": "dropped", "file": src.path, "line": ln})
                table.columns[f"__old__{name}"] = col
            res.claim(src, tid(t), EdgeType.HAS_COLUMN, cid(t, name), line_text(text, ln), ln, ex, mode="deprecated",
                      reason="dropped")

    # stamp final state onto entities so retrieval can answer "what does the table look like *now*"
    for t, table in tables.items():
        current = [c.name for k, c in table.columns.items() if not k.startswith("__old__")]
        removed = {c.name: c.history[-1]["action"] for k, c in table.columns.items() if k.startswith("__old__")}
        res.entities[tid(t)].properties.update(current_columns=current, removed_columns=removed)
        for k, c in table.columns.items():
            e = res.entities.get(cid(t, c.name))
            if e:
                if k.startswith("__old__"):
                    e.properties.update(status=c.status, history=c.history)
                else:
                    e.properties.update(status="current", history=c.history)
    # create-claims of columns that were later dropped/renamed are deprecated, not current truth
    gone = {cid(t, c.name) for t, tb in tables.items() for k, c in tb.columns.items() if k.startswith("__old__")}
    for c in res.claims:
        if c.predicate == EdgeType.HAS_COLUMN and c.object in gone and c.mode == "observed":
            c.mode = "deprecated"
            c.properties["superseded_in_schema"] = True
            c.compute_id()
    return tables


def parse_migrations(files: list[tuple[SourceDoc, str]], idx) -> ParseResult:
    """Group migration files per app (or archive project) and replay each group."""
    res = ParseResult()
    groups: dict[str, list[tuple[SourceDoc, str]]] = {}
    for src, text in files:
        key = app_of(src.path) or scope_prefix(src) or src.path
        groups.setdefault(key, []).append((src, text))
    for key, group in groups.items():
        src0 = group[0][0]
        if src0.scope == "archive":
            prefix = scope_prefix(src0)
            project = src0.path.split("/")[1]
            owner = res.entity(f"{prefix}service:{project}", NodeType.CONCEPT, f"{project} (archived)", tier=4,
                               scope="archive", retired=True)
            replay(group, res, owner, project, prefix)
        else:
            owner = idx.app_eid(key)
            res.entity(owner, NodeType.SERVICE, key, tier=1)
            replay(group, res, owner, key)
    return res
