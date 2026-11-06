"""Evidence grounding (anti-hallucination gate).

Every LLM-produced claim must quote its source. We verify the quote really exists in the cited
file (whitespace/markdown-insensitive exact match, else fuzzy >= 0.9 over line windows) and
recompute line numbers from the file itself - the model's own line numbers are never trusted.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9


def _norm(s: str) -> str:
    s = re.sub(r"[`*_>#|]", "", s)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def ground(snippet: str, text: str) -> tuple[bool, int | None, int | None, float]:
    """Return (grounded, line_start, line_end, score)."""
    if not snippet or not text:
        return False, None, None, 0.0
    snippet = re.sub(r"^\s*\d+\s*[|:]\s?", "", snippet)  # strip "12 | " prefixes the model may copy
    target = _norm(snippet)
    if len(target) < 4:
        return False, None, None, 0.0
    lines = text.splitlines()
    norm_lines = [_norm(ln) for ln in lines]
    # exact match on the smallest window (1..6 lines) that contains the quote
    for size in range(1, 7):
        for i in range(0, max(0, len(lines) - size + 1)):
            window = " ".join(n for n in norm_lines[i:i + size] if n)
            if target in window:
                return True, i + 1, i + size, 1.0
    # fuzzy match: best window of similar length
    best = (0.0, None, None)
    for i in range(len(lines)):
        acc = ""
        for j in range(i, min(i + 6, len(lines))):
            acc = (acc + " " + norm_lines[j]).strip()
            if not acc:
                continue
            if len(acc) >= len(target) * 0.6:
                # compare against the best-aligned substring of similar length
                sm = SequenceMatcher(None, target, acc, autojunk=False)
                m = sm.find_longest_match(0, len(target), 0, len(acc))
                start = max(0, m.b - m.a)
                window = acc[start:start + len(target)]
                score = SequenceMatcher(None, target, window, autojunk=False).ratio()
                if score > best[0]:
                    best = (score, i + 1, j + 1)
            if len(acc) > len(target) * 2 + 100:
                break
    if best[0] >= FUZZY_THRESHOLD:
        return True, best[1], best[2], best[0]
    return False, None, None, best[0]
