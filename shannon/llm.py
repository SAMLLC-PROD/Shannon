"""
Shannon LLM interface — Ollama first, cloud fallback.

Priority:
  1. Ollama (local, private, free after hardware)
  2. Anthropic Claude (cloud fallback — only when SHANNON_ALLOW_CLOUD is set)

SHANNON_ALLOWED_MODELS (comma-separated) restricts which model ids may be
requested. Unset = unrestricted, with graceful fallback between local models.

Both sync and async interfaces provided.
FastAPI should use the async versions to avoid blocking the event loop.
"""

import os
import json
import urllib.request
from typing import Optional, List, Dict

OLLAMA_BASE   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("SHANNON_MODEL", "qwen2.5:7b")
FAST_MODEL    = os.environ.get("SHANNON_FAST_MODEL", "mistral:7b")

# Cloud is opt-in. Without this flag the Anthropic fallback never runs.
SHANNON_ALLOW_CLOUD = os.environ.get("SHANNON_ALLOW_CLOUD", "").lower() in ("1", "true", "yes")
_CLOUD_REFUSED = "Ollama unavailable and SHANNON_ALLOW_CLOUD not set — refusing cloud fallback"

SHANNON_ALLOWED_MODELS_ENV = os.environ.get("SHANNON_ALLOWED_MODELS", "").strip()


def allowed_models() -> List[str]:
    """
    When SHANNON_ALLOWED_MODELS is set, only those model ids are accepted.
    When unset (our default), any available Ollama model is fine — we're
    experimenting locally. CE operators set an explicit list.
    """
    if SHANNON_ALLOWED_MODELS_ENV:
        return [x.strip() for x in SHANNON_ALLOWED_MODELS_ENV.split(",") if x.strip()]
    return []  # empty = unrestricted


def _check_allowed(model: str) -> None:
    """Raise if an allow-list is set and `model` is not on it."""
    allowed = allowed_models()
    if allowed and model not in allowed:
        raise RuntimeError(
            f"Model {model!r} is not in SHANNON_ALLOWED_MODELS — refusing to substitute"
        )

# Ollama availability cache — avoid checking on every request
import time as _time
_ollama_cache: dict = {"available": None, "checked_at": 0, "ttl": 30}  # 30s TTL


# ---------------------------------------------------------------------------
# Sync helpers (CLI / scripts)
# ---------------------------------------------------------------------------

def ollama_available() -> bool:
    now = _time.time()
    if _ollama_cache["available"] is not None and (now - _ollama_cache["checked_at"]) < _ollama_cache["ttl"]:
        return _ollama_cache["available"]
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        result = req.status == 200
    except Exception:
        result = False
    _ollama_cache["available"] = result
    _ollama_cache["checked_at"] = now
    return result


def ollama_models() -> List[str]:
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        data = json.loads(req.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _resolve_model(model: str, available: List[str]) -> str:
    """Pick best available model, falling back gracefully.

    With SHANNON_ALLOWED_MODELS set there is no substitution: the requested
    model must be on the list and present in Ollama.
    """
    if allowed_models():
        _check_allowed(model)
        if model in available:
            return model
        raise RuntimeError(
            f"Model {model!r} is allowed but not available in Ollama. Run: ollama pull {model}"
        )
    if model in available:
        return model
    if DEFAULT_MODEL in available:
        return DEFAULT_MODEL
    if FAST_MODEL in available:
        return FAST_MODEL
    if available:
        return available[0]
    raise RuntimeError("No Ollama models available. Run: ollama pull qwen2.5:7b")


def ollama_chat(messages: List[Dict], model: str = None, system: str = None) -> str:
    """Synchronous Ollama chat — for CLI/scripts only."""
    available = ollama_models()
    model = _resolve_model(model or DEFAULT_MODEL, available)
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    payload = json.dumps({"model": model, "messages": msgs, "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())["message"]["content"]


# ---------------------------------------------------------------------------
# Async interface (FastAPI / Pigeon API)
# ---------------------------------------------------------------------------

async def ollama_available_async() -> bool:
    now = _time.time()
    if _ollama_cache["available"] is not None and (now - _ollama_cache["checked_at"]) < _ollama_cache["ttl"]:
        return _ollama_cache["available"]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            result = resp.status_code == 200
    except Exception:
        result = False
    _ollama_cache["available"] = result
    _ollama_cache["checked_at"] = now
    return result


async def ollama_models_async() -> List[str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


async def ollama_chat_async(
    messages: List[Dict],
    model: str = None,
    system: str = None,
) -> str:
    """Async Ollama chat — use this inside FastAPI endpoints."""
    import httpx
    available = await ollama_models_async()
    model = _resolve_model(model or DEFAULT_MODEL, available)
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    payload = {"model": model, "messages": msgs, "stream": False}

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"]


async def chat_async(
    messages: List[Dict],
    system: str = None,
    model: str = None,
) -> Dict:
    """
    Async unified chat — Ollama first (unless SHANNON_PREFER_CLOUD=true), Anthropic fallback.
    Use this in FastAPI endpoints.
    """
    _check_allowed(model or DEFAULT_MODEL)
    # SHANNON_PREFER_CLOUD only counts when cloud is allowed at all
    prefer_cloud = SHANNON_ALLOW_CLOUD and os.environ.get("SHANNON_PREFER_CLOUD", "").lower() in ("1", "true", "yes")
    ollama_err = None

    if not prefer_cloud and await ollama_available_async():
        try:
            available = await ollama_models_async()
            used_model = _resolve_model(model or DEFAULT_MODEL, available)
            content = await ollama_chat_async(messages, model=used_model, system=system)
            return {"content": content, "backend": "ollama", "model": used_model}
        except Exception as e:
            ollama_err = e  # fall through to cloud

    if not SHANNON_ALLOW_CLOUD:
        raise RuntimeError(_CLOUD_REFUSED) from ollama_err

    # Cloud — AsyncAnthropic to avoid blocking the event loop
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            base_url="https://api.anthropic.com",  # explicit — ignore ANTHROPIC_BASE_URL
        )
        kwargs = {"model": "claude-haiku-4-5", "max_tokens": 1024, "messages": messages}
        if system:
            kwargs["system"] = system
        response = await client.messages.create(**kwargs)
        return {"content": response.content[0].text, "backend": "anthropic", "model": "claude-haiku-4-5"}
    except Exception as e:
        raise RuntimeError(f"All backends failed: {e}")


async def chat_stream_async(
    messages: List[Dict],
    system: str = None,
    model: str = None,
):
    """
    Async streaming chat — yields text chunks as they arrive.
    Ollama skipped if SHANNON_PREFER_CLOUD=true.
    Use with FastAPI StreamingResponse for real-time UI updates.
    """
    _check_allowed(model or DEFAULT_MODEL)
    # SHANNON_PREFER_CLOUD only counts when cloud is allowed at all
    prefer_cloud = SHANNON_ALLOW_CLOUD and os.environ.get("SHANNON_PREFER_CLOUD", "").lower() in ("1", "true", "yes")
    ollama_err = None

    if not prefer_cloud and await ollama_available_async():
        try:
            import httpx
            available = await ollama_models_async()
            used_model = _resolve_model(model or DEFAULT_MODEL, available)
            msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
            payload = {"model": used_model, "messages": msgs, "stream": True}
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            chunk = json.loads(line)
                            delta = chunk.get("message", {}).get("content", "")
                            if delta:
                                yield delta
                            if chunk.get("done"):
                                return
        except Exception as e:
            ollama_err = e  # fall through to cloud

    if not SHANNON_ALLOW_CLOUD:
        raise RuntimeError(_CLOUD_REFUSED) from ollama_err

    # Cloud streaming — AsyncAnthropic
    import anthropic
    client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        base_url="https://api.anthropic.com",
    )
    kwargs = {"model": "claude-haiku-4-5", "max_tokens": 1024, "messages": messages}
    if system:
        kwargs["system"] = system
    async with client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


# Sync unified chat (scripts/CLI)
def chat(messages: List[Dict], system: str = None, model: str = None, prefer_local: bool = True) -> Dict:
    _check_allowed(model or DEFAULT_MODEL)
    # prefer_local=False only skips Ollama when cloud is allowed at all
    ollama_err = None
    if (prefer_local or not SHANNON_ALLOW_CLOUD) and ollama_available():
        try:
            available = ollama_models()
            used_model = _resolve_model(model or DEFAULT_MODEL, available)
            content = ollama_chat(messages, model=used_model, system=system)
            return {"content": content, "backend": "ollama", "model": used_model}
        except Exception as e:
            ollama_err = e
    if not SHANNON_ALLOW_CLOUD:
        raise RuntimeError(_CLOUD_REFUSED) from ollama_err
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            base_url="https://api.anthropic.com",  # explicit — ignore ANTHROPIC_BASE_URL
        )
        kwargs = {"model": "claude-haiku-4-5", "max_tokens": 2048, "messages": messages}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return {"content": response.content[0].text, "backend": "anthropic", "model": "claude-haiku-4-5"}
    except Exception as e:
        raise RuntimeError(f"All backends failed: {e}")


def status() -> Dict:
    available = ollama_models() if ollama_available() else []
    return {
        "ollama": {
            "running": ollama_available(),
            "models": available,
            "default_model": DEFAULT_MODEL,
            "fast_model": FAST_MODEL,
            "default_ready": DEFAULT_MODEL in available,
            "fast_ready": FAST_MODEL in available,
        },
        "allowed_models": allowed_models(),
        "cloud_allowed": SHANNON_ALLOW_CLOUD,
        "preferred_backend": "ollama" if ollama_available() and available else "cloud",
    }


if __name__ == "__main__":
    import pprint
    print("=== Shannon LLM Status ===")
    pprint.pprint(status())
    available = ollama_models()
    if available:
        print(f"\n=== Test chat with {available[0]} ===")
        resp = chat([{"role": "user", "content": "Say hello in one sentence."}])
        print(f"Response: {resp['content']}")
        print(f"Backend:  {resp['backend']} / {resp['model']}")
