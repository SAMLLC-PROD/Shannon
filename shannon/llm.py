"""
Shannon LLM interface — Ollama first, cloud fallback.

Priority:
  1. Ollama (local, private, free after hardware)
  2. Anthropic Claude (cloud fallback)

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


# ---------------------------------------------------------------------------
# Sync helpers (CLI / scripts)
# ---------------------------------------------------------------------------

def ollama_available() -> bool:
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return req.status == 200
    except Exception:
        return False


def ollama_models() -> List[str]:
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        data = json.loads(req.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _resolve_model(model: str, available: List[str]) -> str:
    """Pick best available model, falling back gracefully."""
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
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


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
    Async unified chat — Ollama first, Anthropic fallback.
    Use this in FastAPI endpoints.
    """
    if await ollama_available_async():
        try:
            available = await ollama_models_async()
            used_model = _resolve_model(model or DEFAULT_MODEL, available)
            content = await ollama_chat_async(messages, model=used_model, system=system)
            return {"content": content, "backend": "ollama", "model": used_model}
        except Exception:
            pass  # fall through to cloud

    # Cloud fallback — always hit Anthropic directly, never via lattice-proxy
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


# Sync unified chat (scripts/CLI)
def chat(messages: List[Dict], system: str = None, model: str = None, prefer_local: bool = True) -> Dict:
    if prefer_local and ollama_available():
        try:
            available = ollama_models()
            used_model = _resolve_model(model or DEFAULT_MODEL, available)
            content = ollama_chat(messages, model=used_model, system=system)
            return {"content": content, "backend": "ollama", "model": used_model}
        except Exception:
            pass
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
