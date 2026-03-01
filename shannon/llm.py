"""
Shannon LLM interface — Ollama first, cloud fallback.

Priority:
  1. Ollama (local, private, free after hardware)
  2. Anthropic Claude (cloud fallback)
  3. OpenAI (cloud fallback)

The agent should always try local first. Cloud is the escape hatch,
not the default.
"""

import os
import json
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Generator


OLLAMA_BASE    = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL  = os.environ.get("SHANNON_MODEL", "qwen2.5:32b")
FAST_MODEL     = os.environ.get("SHANNON_FAST_MODEL", "mistral:7b")


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def ollama_available() -> bool:
    """Check if Ollama is running and reachable."""
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return req.status == 200
    except Exception:
        return False


def ollama_models() -> List[str]:
    """List available local models."""
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        data = json.loads(req.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def ollama_chat(
    messages: List[Dict],
    model: str = None,
    stream: bool = False,
    system: str = None,
) -> str:
    """
    Send a chat request to Ollama.
    Returns the assistant response as a string.
    """
    model = model or DEFAULT_MODEL
    available = ollama_models()

    # Fallback to fast model if default not pulled yet
    if model not in available:
        if FAST_MODEL in available:
            model = FAST_MODEL
        elif available:
            model = available[0]
        else:
            raise RuntimeError("No Ollama models available. Run: ollama pull qwen2.5:32b")

    # Prepend system prompt as a system-role message (most reliable across models)
    msgs = messages
    if system:
        msgs = [{"role": "system", "content": system}] + list(messages)

    payload = {
        "model": model,
        "messages": msgs,
        "stream": False,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
        return result["message"]["content"]


# ---------------------------------------------------------------------------
# Cloud fallbacks
# ---------------------------------------------------------------------------

def anthropic_chat(messages: List[Dict], system: str = None) -> str:
    """Anthropic Claude fallback."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        kwargs = {
            "model": "claude-haiku-4-5",
            "max_tokens": 2048,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text
    except ImportError:
        raise RuntimeError("anthropic package not installed: pip install anthropic")


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------

def chat(
    messages: List[Dict],
    system: str = None,
    model: str = None,
    prefer_local: bool = True,
) -> Dict:
    """
    Send a chat, trying local Ollama first then cloud fallback.

    Returns dict with:
      - content: the response text
      - backend: which backend was used ("ollama" | "anthropic" | "openai")
      - model: which model responded
    """
    if prefer_local and ollama_available():
        try:
            available = ollama_models()
            used_model = model or DEFAULT_MODEL
            if used_model not in available and available:
                used_model = available[0]
            content = ollama_chat(messages, model=used_model, system=system)
            return {"content": content, "backend": "ollama", "model": used_model}
        except Exception as e:
            pass  # fall through to cloud

    # Cloud fallback
    try:
        content = anthropic_chat(messages, system=system)
        return {"content": content, "backend": "anthropic", "model": "claude-haiku-4-5"}
    except Exception as e:
        raise RuntimeError(f"All backends failed. Last error: {e}")


def status() -> Dict:
    """Return LLM backend status."""
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

    if ollama_available():
        models = ollama_models()
        if models:
            print(f"\n=== Test chat with {models[0]} ===")
            resp = chat([{"role": "user", "content": "Say hello in one sentence."}])
            print(f"Response: {resp['content']}")
            print(f"Backend:  {resp['backend']} / {resp['model']}")
        else:
            print("\nOllama running but no models yet. Still pulling?")
    else:
        print("\nOllama not running.")
