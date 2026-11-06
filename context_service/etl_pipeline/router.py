"""Repository crawler, modality/authority router and metadata-line parser.

No frontmatter exists in the corpus; dates/owners/status live in the first body lines
("Last edited 2026-02-03 · Identity Platform", "Date: ... / Status: ...", filename dates).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import config
from etl_pipeline.schema import SourceDoc

CODE_EXT = {".ts", ".tsx", ".js", ".mjs", ".py", ".java"}
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
MONTH_RE = re.compile(r"\b(20\d\d-\d\d)\b")
RECORD_RE = re.compile(r"\b([A-Z]{2,8}-\d{1,4})\b")

DOC_DIR_KINDS = [
    ("docs/adr/", "adr"), ("docs/architecture/", "architecture"), ("docs/design/", "design"),
    ("docs/how-to/", "howto"), ("docs/reference/", "reference"), ("docs/runbooks/", "runbook"),
    ("wiki/", "wiki"), ("meeting-notes/", "meeting"), ("issues/", "issue"), ("incidents/", "incident"),
    ("releases/", "release"),
]


@dataclass
class RoutedFile:
    source: SourceDoc
    abs_path: Path
    text: str | None  # None for binary (diagrams)


def _classify(rel: str, name: str, ext: str) -> tuple[str, str, int] | None:
    """Return (kind, modality, tier) or None to skip."""
    if rel.startswith("archive/"):
        modality = "sql" if ext == ".sql" else "code" if ext in CODE_EXT | {".graphql"} else (
            "config" if ext == ".json" else "doc")
        return "archive", modality, 4
    if rel.startswith("api-specs/history/"):
        return "api_history", "api_spec", 3
    if rel.startswith("api-specs/"):
        if name.endswith(".openapi.json"):
            return "openapi", "api_spec", 2
        if ext == ".md":
            return "api_spec_md", "api_spec", 2
    if rel.startswith("diagrams/"):
        if ext in {".png", ".jpg", ".jpeg", ".svg"}:
            return "diagram", "diagram", 3
        return "readme", "doc", 3
    for prefix, kind in DOC_DIR_KINDS:
        if rel.startswith(prefix) and ext == ".md":
            return kind, "doc", 3
    if rel in {"README.md", "DISCLAIMER.md"} or (name == "README.md" and rel.startswith(("apps/", "packages/"))):
        return "readme", "doc", 3
    if rel == ".github/CODEOWNERS":
        return "codeowners", "config", 1
    if rel.startswith(".github/workflows/"):
        return "ci", "config", 1
    if rel in {"compose.yaml", "docker-compose.yml", "config/ports.json"} or rel.startswith("docker/"):
        return "deploy", "config", 1
    if rel.startswith(("apps/", "packages/")):
        if "/fixtures/" in rel:
            return ("config", "config", 1) if ext == ".properties" else None
        if "/migrations/" in rel and ext == ".sql":
            return "sql_migration", "sql", 1
        if name == ".env.example" or name == "package.json" or name.startswith("Dockerfile"):
            return "config", "config", 1
        if ext == ".graphql":
            return "graphql", "api_spec", 2
        if ext in CODE_EXT and "/test" not in rel and not name.startswith("test_") and ".test." not in name \
                and not name.endswith((".config.js", ".config.mjs", ".config.ts", ".d.ts")):
            return "code", "code", 1
    return None


def parse_metadata(rel: str, kind: str, text: str) -> dict:
    """Extract title/date/owner/status/record id from title + first body lines + filename."""
    lines = [ln.strip() for ln in text.splitlines()]
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("#")), "")
    body = [ln for ln in lines if ln and not ln.startswith("#")][:4]
    meta_line = body[0] if body else ""
    out: dict = {"title": title, "date": None, "owner": None, "status": None, "record_id": None}

    for ln in body[:3]:
        if m := re.match(r"(?i)date:\s*(\S+)", ln):
            out["date"] = m.group(1)
        if m := re.match(r"(?i)status:\s*(.+)", ln):
            out["status"] = m.group(1).strip()
    if not out["date"]:
        if m := DATE_RE.search(meta_line):
            out["date"] = m.group(1)
        elif m := DATE_RE.search(Path(rel).name):
            out["date"] = m.group(1)
        elif m := MONTH_RE.search(Path(rel).name) or MONTH_RE.search(meta_line):
            out["date"] = m.group(1)
        elif m := DATE_RE.search(" ".join(body[:3])):
            out["date"] = m.group(1)

    # owner: leading Capitalised Words of a metadata-line segment ("People Systems operations guide · 2026-08-14")
    if kind not in {"issue", "incident", "meeting", "api_spec_md", "release"} and meta_line:
        if m := re.search(r"(?i)owner:\s*([A-Z][\w ]+?)(?:\.|$)", meta_line):
            out["owner"] = m.group(1).strip()
        else:
            for seg in re.split(r"[·,]", meta_line):
                seg = re.sub(r"(?i)last edited", "", DATE_RE.sub("", seg)).strip(" .-—")
                m = re.match(r"^((?:[A-Z][A-Za-z]+)(?: [A-Z][A-Za-z]+){0,3})\b", seg)
                if m and m.group(1).split()[0] not in {"Date", "Status", "Interface", "Northstar", "Published",
                                                       "Default", "Investigation", "Accepted", "Proposed"}:
                    out["owner"] = m.group(1)
                    break
    if kind == "issue" and "·" in meta_line:
        out["status"] = meta_line.split("·")[0].strip()
    if kind == "incident" and meta_line and not out["status"]:
        out["status"] = meta_line.rstrip(".")

    name = Path(rel).stem
    if kind == "adr" and (m := re.match(r"(\d{3})-", name)):
        out["record_id"] = f"ADR-{m.group(1)}"
    elif kind == "issue" and (m := RECORD_RE.search(title or name)):
        out["record_id"] = m.group(1)
    elif kind == "incident" and (m := DATE_RE.search(name)):
        out["record_id"] = f"INC-{m.group(1)}"
    elif kind == "release" and (m := re.search(r"v(\d+\.\d+)", name)):
        out["record_id"] = f"v{m.group(1)}"
    return out


def crawl(repo_root: Path | None = None) -> list[RoutedFile]:
    root = (repo_root or config.REPO_ROOT).resolve()
    routed: list[RoutedFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in config.IGNORED_DIRS for part in Path(rel).parts[:-1]) or path.name in config.IGNORED_FILES:
            continue
        cls = _classify(rel, path.name, path.suffix.lower())
        if cls is None:
            continue
        kind, modality, tier = cls
        raw = path.read_bytes()
        if len(raw) > config.MAX_TEXT_BYTES and modality != "diagram":
            continue
        sha = hashlib.sha256(raw).hexdigest()
        text = None if modality == "diagram" else raw.decode("utf-8", errors="replace")
        meta = parse_metadata(rel, kind, text) if text is not None and path.suffix in {".md", ".txt"} else \
            {"title": path.stem, "date": None, "owner": None, "status": None, "record_id": None}
        if kind == "diagram":
            meta["record_id"] = path.stem
        src = SourceDoc(path=rel, kind=kind, modality=modality, tier=tier,
                        scope="archive" if tier == 4 else "live", sha256=sha, size=len(raw), **meta)
        routed.append(RoutedFile(source=src, abs_path=path, text=text))
    return routed


if __name__ == "__main__":
    from collections import Counter
    files = crawl()
    print(Counter((f.source.kind, f.source.tier) for f in files))
    for f in files:
        if f.source.modality == "doc":
            s = f.source
            print(f"{s.path:60} {s.kind:12} {s.date!s:11} {s.owner!s:22} {s.status!s:20} {s.record_id}")
