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

WORKSPACE    = Path(os.environ.get("SHANNON_WORKSPACE", Path.home() / ".openclaw/workspace"))
SOUL_FILE    = WORKSPACE / "SOUL.md"
USER_FILE    = WORKSPACE / "USER.md"
CONTEXT_FILE = WORKSPACE / "memory" / "shannon-context.md"


def _load_file(path: Path, fallback: str = "") -> str:
    """Load a file if it exists."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


def build_system_prompt(extra_context: str = "") -> str:
    """
    Build the agent system prompt from soul + user + LTM context.
    This is what makes each user's agent uniquely theirs.
    """
    soul    = _load_file(SOUL_FILE, "You are a helpful assistant.")
    user    = _load_file(USER_FILE, "")
    context = _load_file(CONTEXT_FILE, "")

    parts = [soul]

    if user:
        parts.append(f"## About the person you're helping\n{user}")

    if context:
        # Trim context if very long — keep most recent
        if len(context) > 6000:
            context = context[:6000] + "\n\n_[context truncated]_"
        parts.append(f"## Your long-term memory (Shannon)\n{context}")

    if extra_context:
        parts.append(f"## Additional context\n{extra_context}")

    parts.append(
        "## Important\n"
        "You have access to persistent long-term memory via Shannon. "
        "When the conversation produces something worth remembering — decisions, insights, "
        "milestones — note it. Be concise. Be direct. Be genuinely helpful."
    )

    return "\n\n---\n\n".join(parts)


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
        self.workspace   = workspace or WORKSPACE
        self.history: List[Dict] = []

        # Override paths if custom workspace
        if workspace:
            global SOUL_FILE, USER_FILE, CONTEXT_FILE
            SOUL_FILE    = workspace / "SOUL.md"
            USER_FILE    = workspace / "USER.md"
            CONTEXT_FILE = workspace / "memory" / "shannon-context.md"

        init_store()

    def chat(self, message: str, save_response: bool = False) -> Dict:
        """
        Send a message and get a response.

        Args:
            message: User's message
            save_response: If True, save this exchange to Shannon LTM

        Returns dict with content, backend, model, session_id
        """
        self.history.append({"role": "user", "content": message})

        system = build_system_prompt()

        result = chat(
            messages=self.history,
            system=system,
        )

        self.history.append({"role": "assistant", "content": result["content"]})

        if save_response:
            chunk = f"Q: {message}\nA: {result['content']}"
            save(chunk, session_id=self.session_id, tags=["agent", "exchange"])
            generate_context_file()

        return {
            "content": result["content"],
            "backend": result["backend"],
            "model": result["model"],
            "session_id": self.session_id,
        }

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
