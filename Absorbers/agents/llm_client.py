"""Single choke point for every LLM call in the PoC.

Design goals:
  * Backend-agnostic. One ``LLMConfig`` selects model + routing via the OpenAI
    SDK, which speaks to any OpenAI-compatible endpoint: the ETH LiteLLM proxy,
    DeepSeek, or OpenAI itself. Switching backend is one flag, so the same code
    runs the main sweep and a different-model robustness subset.
  * Reproducible. Every call is content-addressed from its complete public call
    specification. Successful responses and provider provenance are written to
    ``results/raw/<cache_id>.json``; failed calls are quarantined separately.
  * Non-hanging. ``max_retries=0`` and a hard timeout. (litellm was dropped
    during bring-up because it silently retried a dead key with backoff and
    hung for minutes; the OpenAI SDK with max_retries=0 fails fast instead.)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CACHE_SCHEMA_VERSION = 2
CALL_SCHEMA = "openai.chat.completions/json-prompt/v2"


class OfflineCacheMiss(RuntimeError):
    pass


class CachedCallError(RuntimeError):
    pass


class InvalidCacheRecord(RuntimeError):
    pass


@dataclass
class LLMConfig:
    model: str                       # model id at the endpoint, e.g. "deepseek-chat"
    label: str                       # short tag for filenames/plots, e.g. "deepseek", "flash"
    base_url: Optional[str] = None   # OpenAI-compatible endpoint incl. /v1; None -> OpenAI default
    api_key: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 700
    timeout: int = 60


def _client(cfg: LLMConfig):
    from openai import OpenAI
    kwargs = dict(api_key=cfg.api_key or "missing", max_retries=0, timeout=cfg.timeout)
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAI(**kwargs)


def _safe_component(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in value)
    return cleaned or "unnamed"


def call_spec(cfg: LLMConfig, tag: str, salt: str, system: str, user: str) -> dict:
    """Public, secret-free specification whose identity defines a cached call."""
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "call_schema": CALL_SCHEMA,
        "endpoint": cfg.base_url or "openai-default",
        "label": cfg.label,
        "model": cfg.model,
        "temperature_hex": float(cfg.temperature).hex(),
        "max_tokens": int(cfg.max_tokens),
        "timeout_seconds": int(cfg.timeout),
        "tag": tag,
        "salt": salt,
        "system": system,
        "user": user,
    }


def call_id(cfg: LLMConfig, tag: str, salt: str, system: str, user: str) -> str:
    """V2 deterministic id over the complete public call specification.

    ``salt`` distinguishes repeated stochastic samples of the SAME input (e.g.
    run index 0..N-1). Endpoint, exact temperature, output limit, timeout, call
    schema, and full prompts are all identity-bearing.
    """
    payload = json.dumps(
        call_spec(cfg, tag, salt, system, user),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return (
        f"{_safe_component(cfg.label)}_{_safe_component(tag)}_v2_"
        f"{digest[:32]}"
    )


def legacy_call_id(
    cfg: LLMConfig, tag: str, salt: str, system: str, user: str,
) -> str:
    """The pre-v2 cache ID, used only for the frozen default DeepSeek run."""
    h = hashlib.sha256()
    for part in (
        cfg.label, cfg.model, f"{cfg.temperature:.3f}", tag, salt, system, user,
    ):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return f"{cfg.label}_{tag}_{h.hexdigest()[:20]}"


def _legacy_eligible(cfg: LLMConfig) -> bool:
    return (
        cfg.label == "deepseek"
        and cfg.model == "deepseek-chat"
        and (cfg.base_url or "").rstrip("/") == "https://api.deepseek.com/v1"
        and float(cfg.temperature) in (0.0, 0.7)
        and int(cfg.max_tokens) == 700
        and int(cfg.timeout) == 60
    )


def _load_cache(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidCacheRecord(f"invalid cache record {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidCacheRecord(f"invalid cache record {path.name}: not an object")
    return value


def _atomic_write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            tmp_name = fh.name
            json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def _failed_path(cache_dir: Path, filename: str) -> Path:
    failed_dir = cache_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    target = failed_dir / filename
    if target.exists():
        target = failed_dir / f"{Path(filename).stem}.{time.time_ns()}.json"
    return target


def _quarantine(path: Path, cache_dir: Path):
    if path.exists():
        os.replace(path, _failed_path(cache_dir, path.name))


def complete(
    cfg: LLMConfig,
    system: str,
    user: str,
    *,
    tag: str,
    salt: str,
    cache_dir: Path,
    offline: bool = False,
) -> dict:
    """Return a successful response or a newly observed live-call error record.

    Offline misses, malformed records, and cached provider errors raise. Live
    mode quarantines bad/error cache entries and retries once through the normal
    provider call. Newly observed errors are written only under ``raw/failed``.
    """
    cid = call_id(cfg, tag, salt, system, user)
    path = cache_dir / f"{cid}.json"
    candidates: list[tuple[Path, str]] = [(path, "v2")]
    candidate_ids = [cid]
    if _legacy_eligible(cfg):
        legacy = legacy_call_id(cfg, tag, salt, system, user)
        candidate_ids.append(legacy)
        candidates.append((cache_dir / f"{legacy}.json", "legacy_v1"))

    for candidate, source in candidates:
        if not candidate.exists():
            continue
        try:
            rec = _load_cache(candidate)
        except InvalidCacheRecord:
            if offline:
                raise
            _quarantine(candidate, cache_dir)
            continue
        if rec.get("error"):
            message = f"cached provider error in {candidate.name}: {rec['error']}"
            if offline:
                raise CachedCallError(message)
            _quarantine(candidate, cache_dir)
            continue
        rec = dict(rec)
        rec["cached"] = True
        rec["cache_source"] = source
        rec["requested_cache_id"] = cid
        return rec

    if offline:
        failed_dir = cache_dir / "failed"
        if failed_dir.exists():
            for candidate_id in candidate_ids:
                failed = sorted(
                    entry for entry in failed_dir.glob("*.json")
                    if entry.name.startswith(candidate_id)
                )
                if failed:
                    rec = _load_cache(failed[-1])
                    raise CachedCallError(
                        f"cached provider error in {failed[-1].name}: "
                        f"{rec.get('error', 'unknown failure')}"
                    )
        raise OfflineCacheMiss(f"offline cache miss: {cid}")

    spec = call_spec(cfg, tag, salt, system, user)
    t0 = time.perf_counter()
    try:
        client = _client(cfg)
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
        dt = (time.perf_counter() - t0) * 1000.0
        choice = resp.choices[0]
        text = choice.message.content
        usage = getattr(resp, "usage", None)
        ti = int(getattr(usage, "prompt_tokens", 0) or 0)
        to = int(getattr(usage, "completion_tokens", 0) or 0)
        rec = {
            "text": text, "tokens_in": ti, "tokens_out": to,
            "latency_ms": round(dt, 1), "error": None, "cache_id": cid,
            "model": cfg.model,
            "resolved_model": getattr(resp, "model", None),
            "response_id": getattr(resp, "id", None),
            "finish_reason": getattr(choice, "finish_reason", None),
            "created": getattr(resp, "created", None),
            "system_fingerprint": getattr(resp, "system_fingerprint", None),
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "call_spec": spec,
        }
    except Exception as e:
        dt = (time.perf_counter() - t0) * 1000.0
        rec = {
            "text": None, "tokens_in": 0, "tokens_out": 0,
            "latency_ms": round(dt, 1), "error": f"{type(e).__name__}: {str(e)[:300]}",
            "cache_id": cid, "model": cfg.model,
            "resolved_model": None, "response_id": None, "finish_reason": None,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "call_spec": spec,
        }

    rec["cached"] = False
    rec["cache_source"] = "live_v2"
    rec["requested_cache_id"] = cid
    if rec.get("error"):
        _atomic_write_json(_failed_path(cache_dir, f"{cid}.json"), rec)
    else:
        _atomic_write_json(path, rec)
    return rec


# ---- convenience config builders -------------------------------------------- #

def _read_env(name: str) -> Optional[str]:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    return os.getenv(name)


def _keychain(name: str) -> Optional[str]:
    import subprocess
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", name, "-w"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def config_for(
    backend: str,
    temperature: float = 0.0,
    *,
    offline: bool = False,
) -> LLMConfig:
    """Build an LLMConfig for a named backend (all OpenAI-compatible).

    ``offline=True`` is deliberately stronger than merely omitting a provider
    call: it does not read dotenv files, process-environment secrets, or the
    macOS Keychain.  The public fields for the two published DeepSeek
    configurations remain identical so their frozen cache IDs do not change.

    Backends:
      * ``deepseek`` / ``deepseek-reasoner``: historical endpoint aliases kept
        to identify and replay the frozen DeepSeek V4 Flash cache.  Both caches
        resolved to the same provider model; ``deepseek-reasoner`` selected its
        thinking mode, where the provider ignored temperature.  DeepSeek
        announced retirement of both aliases for 24 July 2026, so they must not
        be reused as identities for a future collection.
      * ``litellm-flash`` / ``litellm-gpt4o`` : ETH LiteLLM proxy (needs a valid key)
      * ``openai-gpt4o``        : direct OpenAI (OPENAI_API_KEY)
      * ``ollama:<model>``      : local Ollama server (http://localhost:11434/v1),
        e.g. ``ollama:gemma3:4b``.  No credential is read in either mode; the
        public fields (and therefore the cache IDs) are identical offline and
        live, so a collected local sweep replays byte-identically.
    """
    def credential(name: str) -> Optional[str]:
        if offline:
            return None
        return _read_env(name)

    if backend in ("deepseek", "deepseek-reasoner"):
        reasoner = backend == "deepseek-reasoner"
        return LLMConfig(
            model="deepseek-reasoner" if reasoner else "deepseek-chat",
            label="dsr" if reasoner else "deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key=(
                None
                if offline
                else credential("DEEPSEEK_API_KEY") or _keychain("DEEPSEEK_API_KEY")
            ),
            temperature=temperature,
            # The reasoner spends output budget on hidden reasoning before the
            # answer; at the chat default (700) it truncates with empty content
            # on ~half the tasks. max_tokens is identity-bearing in the cache
            # id, so this bound is part of the sweep's specification.
            max_tokens=4000 if reasoner else 700,
        )
    if backend in ("litellm-flash", "litellm-gpt4o"):
        model = "gemini/gemini-2.5-flash" if backend.endswith("flash") else "openai/gpt-4o"
        label = "flash" if backend.endswith("flash") else "gpt4o"
        base = (credential("LITELLM_API_BASE") or "").rstrip("/")
        if base and not base.endswith("/v1"):
            base += "/v1"
        return LLMConfig(
            model=model, label=label,
            base_url=base or None,
            api_key=credential("LITELLM_API_KEY"),
            temperature=temperature,
        )
    if backend == "openai-gpt4o":
        return LLMConfig(
            model="gpt-4o", label="gpt4o", base_url=None,
            api_key=credential("OPENAI_API_KEY"), temperature=temperature,
        )
    if backend.startswith("ollama:"):
        model = backend.split(":", 1)[1]
        if not model:
            raise ValueError("ollama backend needs a model id, e.g. ollama:gemma3:4b")
        return LLMConfig(
            model=model,
            label="local-" + _safe_component(model),
            base_url="http://localhost:11434/v1",
            api_key=None,
            temperature=temperature,
            # max_tokens matches the flagship non-thinking spec; timeout is
            # generous because the first call pays the model load. Both are
            # identity-bearing in the cache id: fixed for the whole sweep.
            max_tokens=700,
            timeout=180,
        )
    raise ValueError(f"unknown backend: {backend}")
