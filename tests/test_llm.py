"""Tests for Shannon LLM interface."""

from shannon.llm import ollama_available, ollama_models, status


def test_status_structure():
    s = status()
    assert "ollama" in s
    assert "preferred_backend" in s
    assert "running" in s["ollama"]
    assert "models" in s["ollama"]
    assert "default_model" in s["ollama"]


def test_ollama_availability_returns_bool():
    result = ollama_available()
    assert isinstance(result, bool)


def test_ollama_models_returns_list():
    result = ollama_models()
    assert isinstance(result, list)


def test_preferred_backend_is_valid():
    s = status()
    assert s["preferred_backend"] in ("ollama", "cloud")


# ---------------------------------------------------------------------------
# Cloud gate + model allow-list
# ---------------------------------------------------------------------------

import asyncio
import pytest
from shannon import llm


def test_resolve_model_unrestricted_falls_back(monkeypatch):
    monkeypatch.setattr(llm, "SHANNON_ALLOWED_MODELS_ENV", "")
    assert llm.allowed_models() == []
    assert llm._resolve_model("nope:1b", ["other:7b"]) == "other:7b"


def test_resolve_model_allow_list_rejects_unknown(monkeypatch):
    monkeypatch.setattr(llm, "SHANNON_ALLOWED_MODELS_ENV", "a:7b, b:7b")
    assert llm.allowed_models() == ["a:7b", "b:7b"]
    assert llm._resolve_model("a:7b", ["a:7b", "zzz"]) == "a:7b"
    with pytest.raises(RuntimeError, match="SHANNON_ALLOWED_MODELS"):
        llm._resolve_model("zzz", ["a:7b", "zzz"])
    # allowed but not pulled — no substitution either
    with pytest.raises(RuntimeError, match="not available"):
        llm._resolve_model("b:7b", ["a:7b"])


def test_allow_list_error_not_swallowed_by_chat(monkeypatch):
    monkeypatch.setattr(llm, "SHANNON_ALLOWED_MODELS_ENV", "a:7b")
    monkeypatch.setattr(llm, "SHANNON_ALLOW_CLOUD", True)
    with pytest.raises(RuntimeError, match="SHANNON_ALLOWED_MODELS"):
        llm.chat([{"role": "user", "content": "hi"}], model="zzz")
    with pytest.raises(RuntimeError, match="SHANNON_ALLOWED_MODELS"):
        asyncio.run(llm.chat_async([{"role": "user", "content": "hi"}], model="zzz"))


def test_cloud_fallback_refused_when_not_allowed(monkeypatch):
    monkeypatch.setattr(llm, "SHANNON_ALLOWED_MODELS_ENV", "")
    monkeypatch.setattr(llm, "SHANNON_ALLOW_CLOUD", False)
    monkeypatch.setattr(llm, "ollama_available", lambda: False)

    async def _down():
        return False
    monkeypatch.setattr(llm, "ollama_available_async", _down)
    msgs = [{"role": "user", "content": "hi"}]

    with pytest.raises(RuntimeError, match="refusing cloud fallback"):
        llm.chat(msgs)
    with pytest.raises(RuntimeError, match="refusing cloud fallback"):
        llm.chat(msgs, prefer_local=False)
    with pytest.raises(RuntimeError, match="refusing cloud fallback"):
        asyncio.run(llm.chat_async(msgs))

    async def _drain():
        async for _ in llm.chat_stream_async(msgs):
            pass
    with pytest.raises(RuntimeError, match="refusing cloud fallback"):
        asyncio.run(_drain())
