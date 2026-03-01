"""Tests for Shannon Agent."""

from shannon.agent import ShannonAgent, build_system_prompt


def test_system_prompt_builds():
    prompt = build_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 10


def test_system_prompt_with_extra_context():
    prompt = build_system_prompt(extra_context="Ron is building Lattice Network")
    assert "Lattice Network" in prompt


def test_agent_initializes():
    agent = ShannonAgent(session_id="test-session")
    assert agent.session_id == "test-session"
    assert agent.history == []


def test_agent_status_structure():
    agent = ShannonAgent(session_id="test-session")
    s = agent.status()
    assert "session_id" in s
    assert "history_length" in s
    assert "llm" in s
    assert "shannon" in s


def test_agent_remember():
    agent = ShannonAgent(session_id="test-agent-remember")
    addr = agent.remember("Test memory from agent tests", tags=["test"])
    assert addr.startswith("F(")
