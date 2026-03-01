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
