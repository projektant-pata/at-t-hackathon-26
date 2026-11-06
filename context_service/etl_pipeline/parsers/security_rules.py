"""Deterministic (Semgrep-style) security rules over Tier-1 code.

Rules are generic patterns - no file names are hard-coded. Each finding carries the exact
code line as evidence plus `guidance_for_codegen`, which the MCP server hands to coding
assistants so generated code does not repeat the pattern. The LLM code review
(extractors.review_code) complements these with semantic/contract-aware findings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from etl_pipeline.parsers.common import ParseResult, RepoIndex, app_of, line_of, line_text, package_of
from etl_pipeline.schema import EvidenceRef, Risk, SourceDoc

ROUTE_START = re.compile(r"^(?:app\.(get|post|put|patch|delete)\(\s*[\"'](/[^\"']*)[\"']|@app\.(get|post|put|patch|delete)\(\s*[\"'](/[^\"']*)[\"'])",
                         re.M)


@dataclass
class Handler:
    method: str
    path: str
    start: int
    end: int
    body: str


def handlers(text: str) -> list[Handler]:
    starts = list(ROUTE_START.finditer(text))
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        method = (m.group(1) or m.group(3)).upper()
        path = m.group(2) or m.group(4)
        out.append(Handler(method, path, m.start(), end, text[m.start():end]))
    return out


def _risk(res: ParseResult, src: SourceDoc, text: str, idx_pos: int, entity: str, *, category: str, severity: str,
          title: str, description: str, guidance: str, rule: str, extra_entities=()) -> None:
    ln = line_of(text, idx_pos)
    r = Risk(category=category, severity=severity, title=title, description=description,
             entities=[entity, *extra_entities],
             evidence=[EvidenceRef(file_path=src.path, snippet=line_text(text, ln), line_start=ln, tier=src.tier,
                                   date=src.date, side="truth")],
             guidance_for_codegen=guidance, detected_by=f"rule:{rule}", confidence=0.8)
    r.compute_id()
    res.risks.append(r)


def scan(src: SourceDoc, text: str, idx: RepoIndex) -> ParseResult:
    res = ParseResult()
    app = app_of(src.path)
    pkg = package_of(src.path)
    if app:
        ent = idx.app_eid(app)
    elif pkg:
        ent = f"lib:@repo/{pkg}"
    else:
        return res

    # 1. wildcard CORS ---------------------------------------------------------------
    for pat in (r"app\.use\(\s*cors\(\s*\)\s*\)", r"allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]",
                r"Access-Control-Allow-Origin\"\s*,\s*\"\*\""):
        if m := re.search(pat, text):
            _risk(res, src, text, m.start(), ent, category="SECURITY", severity="MEDIUM",
                  title=f"Wildcard CORS policy in {ent.split(':', 1)[1]}",
                  description="Any origin may call this API from a browser; combined with header-based identity this "
                              "exposes data to arbitrary web pages.",
                  guidance="Do not use wildcard CORS; configure an explicit allow-list of trusted origins per environment.",
                  rule="cors-wildcard")

    # 2. identity taken from a client-controlled header with a silent default ---------------
    hdr = re.search(r"[\"'](x-[\w-]*(?:user|customer|identity)[\w-]*)[\"']", text)
    default = re.search(r"\bdefault\w*(?:Id|User|Customer)\w*\s*=\s*[\"']([^\"']+)[\"']", text)
    if hdr and default and pkg:
        _risk(res, src, text, default.start(), ent, category="SECURITY", severity="HIGH",
              title=f"Spoofable identity header '{hdr.group(1)}' falls back to '{default.group(1)}'",
              description="Caller identity is read from a request header any client can set; invalid or missing values "
                          "silently become a default customer (fail-open). This is demo scaffolding, not authentication.",
              guidance=f"Never derive identity from '{hdr.group(1)}' in new code; authenticate the caller (session/JWT) "
                       f"and reject requests without a verified identity instead of defaulting.",
              rule="header-identity-fail-open")

    # 3. sensitive payloads written to logs ---------------------------------------------------
    for m in re.finditer(r"(?:System\.out\.println|console\.log|logger\.\w+|\blog)\(([^;\n]*)\)", text):
        args = m.group(1)
        if re.search(r"\+\s*(snapshot|employee|email|user|payload|body|token|password)\b", args, re.I) or \
                re.search(r"\$\{\s*(snapshot|employee|email|payload|body|token|password)", args, re.I):
            _risk(res, src, text, m.start(), ent, category="SECURITY", severity="HIGH",
                  title=f"Sensitive record logged in full by {ent.split(':', 1)[1]}",
                  description="A full record/payload is concatenated into a log line; logs typically leave the service "
                              "boundary (aggregators, support) so PII is exposed.",
                  guidance="Log identifiers and outcome only; mask emails and never log full records, request bodies or tokens.",
                  rule="pii-in-logs")

    # 4. idempotency key stored but never used for lookup -----------------------------------------
    if re.search(r"idempotency[-_]key", text, re.I) and app and "requestJson" not in text and "fetch(" not in text:
        lookup = re.search(r"idempotency_key\s*(===|==)|WHERE[^\"']*idempotency|\.find\([^)]*idempotency|"
                           r"\.filter\([^)]*idempotency", text, re.I)
        if not lookup:
            m = re.search(r"idempotency[-_]key", text, re.I)
            _risk(res, src, text, m.start(), ent, category="POLICY_VIOLATION", severity="HIGH",
                  title=f"Idempotency key recorded but never checked in {ent.split(':', 1)[1]}",
                  description="The handler persists the Idempotency-Key but never looks up an existing record with the "
                              "same key, so client retries create duplicate side effects (e.g. double charges).",
                  guidance="Before creating a resource, look up an existing record by Idempotency-Key (and request hash); "
                           "return the original on match, 409 on conflicting reuse.",
                  rule="idempotency-not-enforced")

    for h in handlers(text):
        # 5. by-id read without ownership scoping (IDOR) ---------------------------------------
        has_param = bool(re.search(r"[:{]\w+", h.path))
        by_id = re.search(r"SELECT[^\"']*WHERE\s+id\s*=\s*\?", h.body)
        scoped = re.search(r"(customer_id|owner_id|user_id)\s*=\s*\?.*\b(AND|WHERE)\b|"
                           r"identity\.\w+\s*!==|!==\s*identity\.\w+|\.customer_id\s*!==|"
                           r"(customer_id|owner_id)\s*=\s*\?", h.body)
        if h.method == "GET" and has_param and by_id and not scoped:
            _risk(res, src, text, h.start, f"endpoint:{ent.split(':', 1)[1]}:GET {re.sub(r':\w+', '{}', h.path)}",
                  category="SECURITY", severity="HIGH",
                  title=f"Object read by id without ownership check: GET {h.path} ({ent.split(':', 1)[1]})",
                  description="The handler fetches a record by its id but never compares the record owner with the "
                              "caller identity (IDOR / broken object-level authorization).",
                  guidance="When reading a resource by id, scope the query by the authenticated owner "
                           "(e.g. WHERE id = ? AND customer_id = ?) or return 403/404 on mismatch.",
                  rule="idor-by-id", extra_entities=[ent])
        # 6. unauthenticated state-changing endpoint -------------------------------------------
        mutates = re.search(r"\b(UPDATE|INSERT|DELETE)\b\s", h.body)
        authed = re.search(r"identity|auth|token|session|current_user|Depends\(", h.body, re.I)
        if h.method in {"POST", "PUT", "PATCH", "DELETE"} and mutates and not authed:
            _risk(res, src, text, h.start, ent, category="SECURITY", severity="MEDIUM",
                  title=f"Unauthenticated state change: {h.method} {h.path} ({ent.split(':', 1)[1]})",
                  description="The endpoint modifies persisted state without any caller authentication or authorization.",
                  guidance="Require an authenticated, authorized operator for state-changing endpoints and audit-log the change.",
                  rule="unauthenticated-mutation")

    # 7. GraphQL IDE/introspection left at defaults --------------------------------------------
    if (m := re.search(r"createYoga\(", text)) and not re.search(r"graphiql\s*:\s*false|maskedErrors|useDisableIntrospection", text):
        _risk(res, src, text, m.start(), ent, category="SECURITY", severity="LOW",
              title="GraphQL IDE and introspection enabled by default",
              description="graphql-yoga serves GraphiQL and introspection unless disabled, exposing the schema publicly.",
              guidance="Disable GraphiQL/introspection outside development (graphiql: false) and add depth/complexity limits.",
              rule="graphql-defaults")

    # 8. raw internal error messages returned to clients ----------------------------------------
    if m := re.search(r"error\s*:\s*[^,\n]*error\.message", text):
        _risk(res, src, text, m.start(), ent, category="SECURITY", severity="LOW",
              title=f"Internal error messages returned to clients ({ent.split(':', 1)[1]})",
              description="Raw downstream/internal error messages are echoed in API responses (information disclosure).",
              guidance="Return generic error messages with a request id; log details server-side only.",
              rule="error-message-leak")
    return res
