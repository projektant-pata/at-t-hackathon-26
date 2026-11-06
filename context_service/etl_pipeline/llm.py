"""litellm wrapper: structured outputs, vision, embeddings, bounded concurrency, on-disk cache.

Cache key = sha256(model + messages [+ image hashes] + schema name). Re-running the ETL on an
unchanged repo therefore makes zero LLM calls (reproducible + cheap), and changing one file
re-extracts only that file.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

import config

log = logging.getLogger("llm")
T = TypeVar("T", bound=BaseModel)

STATS = {"calls": 0, "cache_hits": 0, "failures": 0, "repairs": 0, "embed_calls": 0, "embed_cache_hits": 0}
_sem: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(config.LLM_CONCURRENCY)
    return _sem


def _cache_path(kind: str, key: str) -> Path:
    p = config.CACHE_DIR / kind / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def image_part(path: Path) -> dict:
    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


def _messages_fingerprint(messages: list[dict]) -> str:
    def strip(obj):
        if isinstance(obj, dict):
            if obj.get("type") == "image_url":
                return {"img": hashlib.sha256(obj["image_url"]["url"].encode()).hexdigest()}
            return {k: strip(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj
    return json.dumps(strip(messages), sort_keys=True)


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text


async def structured(model: str, system: str, user: str | list, schema: type[T], *, temperature: float = 0.0,
                     images: list[Path] | None = None) -> T | None:
    """Call `model` and parse the answer into `schema`. Returns None after one failed repair attempt."""
    content: list | str = user
    if images:
        content = [{"type": "text", "text": user}] + [image_part(p) for p in images]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    key = _key(model, schema.__name__, json.dumps(schema.model_json_schema(), sort_keys=True), _messages_fingerprint(messages))
    cp = _cache_path("llm", key)
    if cp.exists():
        STATS["cache_hits"] += 1
        try:
            return schema.model_validate_json(cp.read_text())
        except ValidationError:
            cp.unlink()

    import litellm  # lazy: keeps deterministic-only runs import-light
    litellm.drop_params = True
    async with _semaphore():
        repairs, transient = 0, 0
        raw = ""
        while repairs < 2 and transient < 6:
            try:
                STATS["calls"] += 1
                extra = {} if "gemini-3" in model else {"temperature": temperature}  # Gemini 3: keep default 1.0
                resp = await litellm.acompletion(model=model, messages=messages, response_format=schema,
                                                 timeout=config.LLM_TIMEOUT_S, num_retries=0, **extra)
                raw = _extract_json(resp.choices[0].message.content or "")
                parsed = schema.model_validate_json(raw)
                cp.write_text(parsed.model_dump_json())
                return parsed
            except ValidationError as e:  # schema gate: one repair round-trip, then drop
                repairs += 1
                STATS["repairs"] += 1
                messages = messages + [{"role": "assistant", "content": raw},
                                       {"role": "user", "content": f"Your JSON did not validate: {str(e)[:800]}. "
                                                                   f"Return only corrected JSON for the schema."}]
            except Exception as e:  # quota (429) / overload (503) / timeouts -> exponential backoff
                transient += 1
                STATS["retries"] = STATS.get("retries", 0) + 1
                msg = str(e)
                quota = "RateLimit" in type(e).__name__ or "429" in msg or "RESOURCE_EXHAUSTED" in msg
                delay = min(90, (15 if quota else 4) * 2 ** (transient - 1))
                log.warning("LLM call failed (%s, try %d, sleeping %ss): %s", model, transient, delay, msg[:200])
                await asyncio.sleep(delay)
    STATS["failures"] += 1
    return None


async def embed(texts: list[str]) -> list[list[float]] | None:
    """Embed texts with the configured embedding model (cached per text). None if LLM disabled/failing."""
    if not texts:
        return []
    if not config.llm_enabled():
        return None
    out: list[list[float] | None] = [None] * len(texts)
    missing = []
    for i, t in enumerate(texts):
        cp = _cache_path("emb", _key(config.EMBED_MODEL, str(config.EMBED_DIM), t))
        if cp.exists():
            out[i] = json.loads(cp.read_text())
            STATS["embed_cache_hits"] += 1
        else:
            missing.append(i)
    import litellm
    for start in range(0, len(missing), 64):
        batch = missing[start:start + 64]
        try:
            async with _semaphore():
                STATS["embed_calls"] += 1
                resp = await litellm.aembedding(model=config.EMBED_MODEL, input=[texts[i] for i in batch],
                                                dimensions=config.EMBED_DIM, timeout=config.LLM_TIMEOUT_S)
            for i, item in zip(batch, resp.data):
                vec = item["embedding"] if isinstance(item, dict) else item.embedding
                out[i] = vec
                _cache_path("emb", _key(config.EMBED_MODEL, str(config.EMBED_DIM), texts[i])).write_text(json.dumps(vec))
        except Exception as e:
            log.warning("embedding failed: %s", str(e)[:300])
            return None
    return out  # type: ignore[return-value]


def embed_sync(texts: list[str]) -> list[list[float]] | None:
    return asyncio.run(embed(texts))
