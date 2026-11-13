"""Readers for each coding agent's session logs."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..util import AdapterError, as_dict, iter_jsonl, read_json
from .base import Adapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .opencode import OpenCodeAdapter

ALL: List[Adapter] = [ClaudeCodeAdapter(), CodexAdapter(), GeminiAdapter(), OpenCodeAdapter()]


def get(name: str) -> Optional[Adapter]:
    wanted = (name or "").strip().lower()
    for adapter in ALL:
        if wanted == adapter.id or wanted in adapter.aliases:
            return adapter
    return None


def names() -> List[str]:
    return [a.id for a in ALL]


def sniff(path: Path) -> Optional[Adapter]:
    """Guess which agent wrote a file, so `--file` works without `--agent`."""
    name = path.name
    try:
        if name.startswith("rollout-"):
            return get("codex")
        if name.endswith((".jsonl", ".jsonl.zst")):
            stats: dict = {}
            for count, line in enumerate(iter_jsonl(path, stats)):
                if line.get("type") == "session_meta":
                    return get("codex")
                if "uuid" in line and "message" in line:
                    return get("claude-code")
                if count >= 12:
                    break
        elif name.endswith(".json"):
            data = as_dict(read_json(path))
            if isinstance(data.get("messages"), list) and "sessionId" in data:
                return get("gemini")
            if str(data.get("id", "")).startswith("ses_"):
                return get("opencode")
    except AdapterError:
        return None
    return None
