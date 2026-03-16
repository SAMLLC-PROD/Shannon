"""
Shannon Agent — LTM-aware conversational agent.

This is the PGN Agent brain. It:
  1. Loads relevant LTM context from Shannon store
  2. Builds a system prompt from SOUL.md + USER.md + shannon-context.md
  3. Sends to Ollama (local) or cloud fallback
  4. Saves important responses back to Shannon

Each Pigeon user gets their own instance of this, pointed at their
own Shannon store and their own soul/user files.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional

from .llm import chat, status as llm_status
from .store import init_store, stats
from .openclaw import save, generate_context_file


# ---------------------------------------------------------------------------
# Config — paths can be overridden per-user in Pigeon
# ---------------------------------------------------------------------------

DEFAULT_WORKSPACE = Path(os.environ.get("SHANNON_WORKSPACE", str(Path.home() / ".openclaw/workspace")))





def _load_file(path: Path, fallback: str = "") -> str:
    """Load a file if it exists."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


def build_system_prompt(workspace: Path = None, extra_context: str = "") -> str:
    """
    Build a compact agent system prompt from soul + user + recent LTM.
    Kept lean so local models stay fast.
    """
    ws = Path(os.environ.get("SHANNON_WORKSPACE", str(DEFAULT_WORKSPACE))) if workspace is None else workspace

    # Compact identity — first 800 chars of SOUL.md (core values only)
    soul_full = _load_file(ws / "SOUL.md", "You are a helpful personal AI assistant.")
    soul = soul_full[:800] if len(soul_full) > 800 else soul_full

    # User context — full (it's short)
    user = _load_file(ws / "USER.md", "")

    # LTM — most recent 3 chunks only (keep it fast)
    context_full = _load_file(ws / "memory" / "shannon-context.md", "")
    context = ""
    if context_full:
        lines = context_full.split("---")
        recent = [l.strip() for l in lines if l.strip() and "Shannon Context" not in l]
        context = "---\n".join(recent[:3])[:1500]

    parts = [
        f"You are Guy Shannon — a personal AI agent built by Ron Peterson, embedded inside Pigeon.\n"
        f"Your memory system is called Project Shannon (Zeckendorf-Fibonacci addressing, QAM encoding, local storage).\n"
        f"Answer using your memory context below. Ignore any training data about 'Microsoft Shannon' — that is unrelated.\n\n"
        f"## What you are part of\n"
        f"Pigeon is a unified communications app built on the Lattice Network — a quantum-safe Byzantine consensus "
        f"transport layer. Lattice uses ML-KEM-768 + ML-DSA-87 post-quantum cryptography and 7 global validator nodes "
        f"(NYC, London, Singapore) running 5-of-7 Byzantine fault-tolerant consensus. Every message is cryptographically "
        f"signed and validator-approved before delivery. Pigeon aggregates Signal, WhatsApp, SMS, Discord, and email "
        f"into one interface, addressed by quantum-safe keypair identity (.lattice / .latmed for HIPAA). "
        f"The PGN Agent (you) is the AI layer inside Pigeon — helping users understand the system, answer questions, "
        f"and navigate the Lattice ecosystem. Be a knowledgeable, confident guide. Explain the technology clearly "
        f"without exposing implementation internals or credentials.\n\n"
        f"{soul}"
    ]

    if user:
        parts.append(f"## About Ron\n{user.strip()[:600]}")

    if context:
        parts.append(f"## Recent memory (Shannon LTM)\n{context}")

    if extra_context:
        parts.append(f"## Additional context\n{extra_context}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class ShannonAgent:
    """
    A Shannon-powered conversational agent with persistent LTM.

    One instance per user. Each user has their own soul, user file,
    and Shannon store.
    """

    def __init__(
        self,
        session_id: str = None,
        workspace: Path = None,
        shannon_home: Path = None,
    ):
        self.session_id  = session_id or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.workspace   = workspace or DEFAULT_WORKSPACE
        self.history: List[Dict] = []
        self._system_prompt: Optional[str] = None  # cached — rebuilt when files change
        self._prompt_built_at: float = 0.0
        self._prompt_ttl: float = 120.0  # rebuild every 2 minutes max

        # Override paths if custom workspace
        if workspace:
            global SOUL_FILE, USER_FILE, CONTEXT_FILE
            SOUL_FILE    = workspace / "SOUL.md"
            USER_FILE    = workspace / "USER.md"
            CONTEXT_FILE = workspace / "memory" / "shannon-context.md"

        init_store()

    def _get_system_prompt(self) -> str:
        """Return cached system prompt, rebuilding if stale."""
        import time
        now = time.time()
        if self._system_prompt is None or (now - self._prompt_built_at) > self._prompt_ttl:
            self._system_prompt = build_system_prompt(workspace=self.workspace)
            self._prompt_built_at = now
        return self._system_prompt

    def chat(self, message: str, save_response: bool = False) -> Dict:
        """Synchronous chat — for CLI/scripts."""
        self.history.append({"role": "user", "content": message})
        system = self._get_system_prompt()
        result = chat(messages=self.history, system=system)
        self.history.append({"role": "assistant", "content": result["content"]})
        if save_response:
            chunk = f"Q: {message}\nA: {result['content']}"
            save(chunk, session_id=self.session_id, tags=["agent", "exchange"])
            generate_context_file()
        return {**result, "session_id": self.session_id}

    async def chat_async(self, message: str, save_response: bool = False) -> Dict:
        """Async chat — use this in FastAPI endpoints."""
        from .llm import chat_async as _chat_async
        self.history.append({"role": "user", "content": message})
        system = self._get_system_prompt()
        result = await _chat_async(messages=self.history, system=system)
        self.history.append({"role": "assistant", "content": result["content"]})
        if save_response:
            chunk = f"Q: {message}\nA: {result['content']}"
            save(chunk, session_id=self.session_id, tags=["agent", "exchange"])
            generate_context_file()
        return {**result, "session_id": self.session_id}

    def remember(self, text: str, tags: List[str] = None) -> str:
        """Explicitly save something to LTM."""
        addr = save(text, session_id=self.session_id, tags=tags or [])
        generate_context_file()
        return addr

    def status(self) -> Dict:
        """Return agent + LLM + Shannon status."""
        return {
            "session_id": self.session_id,
            "history_length": len(self.history),
            "llm": llm_status(),
            "shannon": stats(),
            "workspace": str(self.workspace),
        }


# ---------------------------------------------------------------------------
# Simple CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    agent = ShannonAgent()

    print("=== Shannon Agent ===")
    s = agent.status()
    llm = s["llm"]
    shannon = s["shannon"]
    backend = llm["preferred_backend"]
    model = llm["ollama"]["models"][0] if llm["ollama"]["models"] else "cloud"
    print(f"Backend: {backend} / {model}")
    print(f"Shannon: {shannon['total_entries']} entries in LTM")
    print("Type 'quit' to exit, 'status' to check backends\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() == "quit":
                break
            if user_input.lower() == "status":
                import pprint
                pprint.pprint(agent.status())
                continue

            resp = agent.chat(user_input)
            print(f"\nGuy ({resp['backend']}/{resp['model']}):")
            print(resp["content"])
            print()
        except KeyboardInterrupt:
            break
