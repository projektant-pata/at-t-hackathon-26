"""Entity resolution: alias table + lexical normalisation + fuzzy match, with a scope guard.

* Canonical ids come from Tier-1/2 parsers (services, endpoints, tables, columns, teams).
* Aliases ("Charge Ledger" -> service:payment-service) come from LLM extraction (with a grounded snippet)
  and from 'Nickname / canonical-name' document titles.
* Scope guard: anything extracted from archive/ lives in its own namespace and can never resolve to,
  or become an alias of, a live entity. Refused merges are logged as decoy candidates for the audit.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from etl_pipeline.parsers.common import ParseResult, norm_path, slug
from etl_pipeline.schema import Entity, NodeType

PREFIX_TYPES = {
    "service": NodeType.SERVICE, "app": NodeType.FRONTEND_APP, "lib": NodeType.LIBRARY, "team": NodeType.TEAM,
    "datastore": NodeType.DATASTORE, "endpoint": NodeType.API_ENDPOINT, "table": NodeType.TABLE,
    "column": NodeType.COLUMN, "env": NodeType.CONFIG_KEY, "decision": NodeType.DECISION, "issue": NodeType.ISSUE,
    "incident": NodeType.INCIDENT, "release": NodeType.RELEASE, "meeting": NodeType.MEETING, "doc": NodeType.DOCUMENT,
    "diagram": NodeType.DIAGRAM, "policy": NodeType.POLICY, "concept": NodeType.CONCEPT, "deploy": NodeType.DEPLOY_UNIT,
}
TYPE_PREFIX = {v: k for k, v in PREFIX_TYPES.items()}
COMPATIBLE = {NodeType.SERVICE: {NodeType.SERVICE, NodeType.FRONTEND_APP},
              NodeType.FRONTEND_APP: {NodeType.SERVICE, NodeType.FRONTEND_APP}}


def key(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text.lower())
    return re.sub(r"[^a-z0-9]", "", text)


class Resolver:
    def __init__(self, base: ParseResult):
        self.res = base
        self.alias: dict[str, str] = {}
        self.refused: list[dict] = []
        self.created: list[str] = []
        for e in base.entities.values():
            self._index(e)

    # ------------------------------------------------------------------ index
    def _index(self, e: Entity) -> None:
        if e.scope != "live":
            return
        names = {e.name, e.id.split(":", 1)[-1], *e.aliases}
        if e.type in (NodeType.SERVICE, NodeType.FRONTEND_APP):
            base = e.id.split(":", 1)[1]
            names |= {base.replace("-service", ""), base.replace("-", " ")}
        for n in names:
            k = key(n)
            if k and k not in self.alias:
                self.alias[k] = e.id

    def add_alias(self, alias: str, canonical: str, source_path: str, source_scope: str) -> bool:
        ent = self.res.entities.get(canonical)
        if ent is None:
            return False
        if source_scope == "archive" or ent.scope != "live":
            self.refused.append({"alias": alias, "canonical": canonical, "source": source_path,
                                 "reason": "scope guard: archive material cannot alias live entities"})
            return False
        k = key(alias)
        if not k or len(k) < 3:
            return False
        existing = self.alias.get(k)
        if existing and existing != canonical:
            self.refused.append({"alias": alias, "canonical": canonical, "source": source_path,
                                 "reason": f"alias already bound to {existing}"})
            return False
        self.alias[k] = canonical
        if alias not in ent.aliases:
            ent.aliases.append(alias)
            ent.properties.setdefault("alias_sources", {})[alias] = source_path
        return True

    # ----------------------------------------------------------------- resolve
    def resolve(self, raw: str, type_hint: str | None, scope: str = "live", scope_prefix: str = "",
                source_tier: int = 3) -> str:
        """Map an LLM-provided id/name to a canonical entity id (creating a new entity when needed)."""
        raw = (raw or "").strip()
        hint = self._type(type_hint, raw)
        if scope == "archive":
            return self._create(raw, hint, scope, scope_prefix, source_tier)
        if raw in self.res.entities and self.res.entities[raw].scope == "live":
            return raw
        prefix, _, rest = raw.partition(":") if ":" in raw and raw.split(":", 1)[0] in PREFIX_TYPES else ("", "", raw)
        rest = rest or raw
        if prefix == "endpoint" or hint == NodeType.API_ENDPOINT:
            return self._resolve_endpoint(rest, source_tier)
        if prefix == "column" or hint == NodeType.COLUMN:
            hit = self._resolve_suffix(rest, NodeType.COLUMN)
            if hit:
                return hit
        if prefix == "table" or hint == NodeType.TABLE:
            hit = self._resolve_suffix(rest, NodeType.TABLE)
            if hit:
                return hit
        # alias / lexical
        for candidate in (rest, raw):
            hit = self.alias.get(key(candidate))
            if hit and self._compatible(hit, hint):
                return hit
        # fuzzy within same type
        best, best_id = 0.0, None
        k = key(rest)
        for e in self.res.entities.values():
            if e.scope != "live" or not self._compatible(e.id, hint) or hint is None:
                continue
            for n in (e.name, e.id.split(":", 1)[-1], *e.aliases):
                s = SequenceMatcher(None, k, key(n)).ratio()
                if s > best:
                    best, best_id = s, e.id
        if best >= 0.92 and best_id:
            return best_id
        return self._create(raw, hint, scope, scope_prefix, source_tier)

    def _type(self, type_hint: str | None, raw: str) -> NodeType | None:
        if type_hint:
            try:
                return NodeType(type_hint.upper())
            except ValueError:
                pass
        p = raw.split(":", 1)[0] if ":" in raw else ""
        return PREFIX_TYPES.get(p)

    def _compatible(self, eid: str, hint: NodeType | None) -> bool:
        if hint is None:
            return True
        etype = self.res.entities[eid].type
        return etype == hint or etype in COMPATIBLE.get(hint, set())

    def _resolve_suffix(self, rest: str, t: NodeType) -> str | None:
        rest_k = rest.lower()
        hits = [e.id for e in self.res.entities.values()
                if e.type == t and e.scope == "live" and (e.id.split(":", 1)[1].lower() == rest_k or
                                                           e.id.lower().endswith("." + rest_k.split(".")[-1]) and
                                                           (rest_k.count(".") == 0 or e.id.lower().endswith(rest_k.split(".", 1)[-1])))]
        return hits[0] if len(hits) == 1 else None

    def _resolve_endpoint(self, rest: str, tier: int) -> str:
        # rest like "user-service:GET /v1/users/{id}" or "GET /v1/users/cus-001"
        svc, _, sig = rest.rpartition(":") if re.search(r":(GET|POST|PUT|PATCH|DELETE|GRAPHQL)\b", rest) else ("", "", rest)
        parts = sig.strip().split(None, 1)
        method, path = (parts[0].upper(), parts[1]) if len(parts) == 2 else ("GET", parts[0] if parts else "/")
        path = norm_path(path)
        svc_id = self.alias.get(key(svc)) if svc else None
        svc_name = svc_id.split(":", 1)[1] if svc_id else (svc or "unknown")
        eid = f"endpoint:{svc_name}:{method} {path}"
        if eid in self.res.entities:
            return eid
        # same method+path on exactly one service?
        hits = [e.id for e in self.res.entities.values() if e.type == NodeType.API_ENDPOINT and e.scope == "live"
                and e.id.endswith(f":{method} {path}")]
        if not svc_id and len(hits) == 1:
            return hits[0]
        self.res.entity(eid, NodeType.API_ENDPOINT, f"{method} {path}", tier=tier, service=svc_name, method=method,
                        path=path)
        self.created.append(eid)
        return eid

    def _create(self, raw: str, hint: NodeType | None, scope: str, scope_prefix: str, tier: int) -> str:
        t = hint or NodeType.CONCEPT
        if ":" in raw and raw.split(":", 1)[0] in PREFIX_TYPES:
            body = raw.split(":", 1)[1]
        else:
            body = raw
        body = body if t in (NodeType.API_ENDPOINT, NodeType.TABLE, NodeType.COLUMN, NodeType.DECISION,
                             NodeType.ISSUE, NodeType.INCIDENT, NodeType.RELEASE) else slug(body)
        eid = f"{scope_prefix}{TYPE_PREFIX.get(t, 'concept')}:{body}"
        if eid not in self.res.entities:
            self.res.entity(eid, t, raw.split(":", 1)[-1], tier=tier, scope=scope)
            self.created.append(eid)
            if scope == "live":
                self._index(self.res.entities[eid])
        else:
            ent = self.res.entities[eid]
            ent.grounded_tier = min(ent.grounded_tier, tier)
        return eid


def merge_entity(res: ParseResult, src: str, dst: str) -> int:
    """Rewrite every claim from `src` to `dst`, carry the name over as an alias, drop `src`."""
    n = 0
    for c in res.claims:
        changed = False
        if c.subject == src:
            c.subject, changed = dst, True
        if c.object == src:
            c.object, changed = dst, True
        if changed:
            c.compute_id()
            n += 1
    old = res.entities.pop(src, None)
    if old is not None:
        tgt = res.entities[dst]
        for a in [old.name, *old.aliases]:
            if a and a not in tgt.aliases and a != tgt.name:
                tgt.aliases.append(a)
        tgt.properties.setdefault("merged_from", []).append(src)
    res.claims = [c for c in res.claims if c.subject != c.object]
    return n
