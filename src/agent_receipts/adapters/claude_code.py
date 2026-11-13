"""Claude Code: ~/.claude/projects/<project>/<session>.jsonl"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import COMMAND, EDIT, OTHER, READ, SEARCH, SUBAGENT, WEB, WRITE, Action, Session
from ..util import (
    as_dict,
    as_list,
    home_dir,
    iter_jsonl,
    parse_ts,
    squash,
    text_of,
)
from .base import Adapter, env_path, first_text, newest_first, summarize_args

_SKIP_PROMPT_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command",
    "<system-reminder>",
    "Caveat: The messages below",
    "This session is being continued from a previous conversation",
    "[Request interrupted by user",
)


def map_tool(name: str, inp: Dict[str, Any]) -> Action:
    if name == "Bash":
        return Action(name, COMMAND, first_text(inp, ["command"]))
    if name == "Read":
        return Action(name, READ, first_text(inp, ["file_path", "path"]))
    if name == "Write":
        return Action(name, WRITE, first_text(inp, ["file_path", "path"]))
    if name in ("Edit", "MultiEdit"):
        return Action(name, EDIT, first_text(inp, ["file_path", "path"]))
    if name == "NotebookEdit":
        return Action(name, EDIT, first_text(inp, ["notebook_path", "file_path"]))
    if name in ("Grep", "Glob"):
        return Action(name, SEARCH, first_text(inp, ["pattern", "query"]))
    if name in ("WebFetch", "WebSearch"):
        return Action(name, WEB, first_text(inp, ["url", "query"]))
    if name in ("Task", "Agent"):
        return Action(name, SUBAGENT, first_text(inp, ["description", "prompt"]))
    return Action(name, OTHER, summarize_args(inp))


class ClaudeCodeAdapter(Adapter):
    id = "claude-code"
    label = "Claude Code"
    aliases = ("claude", "claudecode")
    where = "~/.claude/projects/"

    def default_roots(self) -> List[Path]:
        roots: List[Path] = []
        env = env_path("CLAUDE_CONFIG_DIR")
        if env:
            roots.append(env / "projects")
        roots.append(home_dir() / ".claude" / "projects")
        roots.append(home_dir() / ".config" / "claude" / "projects")
        return roots

    def find(self, root: Optional[Path] = None) -> List[Path]:
        found: List[Path] = []
        for base in self.roots(root):
            if not base.is_dir():
                continue
            found.extend(base.glob("*/*.jsonl"))
            found.extend(base.glob("*.jsonl"))
        # agent-*.jsonl files are helper sub-agent logs from older versions, not sessions.
        return newest_first(p for p in found if not p.name.startswith("agent-"))

    def load(self, path: Path) -> Session:
        stats: Dict[str, int] = {}
        session = self.new_session(path)
        pending: Dict[str, Action] = {}
        usage: Dict[str, Dict[str, Any]] = {}
        named = False

        for entry in iter_jsonl(path, stats):
            ts = parse_ts(entry.get("timestamp"))
            session.note_time(ts)
            if session.cwd is None and isinstance(entry.get("cwd"), str):
                session.cwd = entry["cwd"]
            if not named and isinstance(entry.get("sessionId"), str):
                session.session_id = entry["sessionId"]
                named = True

            kind = entry.get("type")
            message = as_dict(entry.get("message"))
            content = message.get("content")

            if kind == "assistant":
                message_id = message.get("id")
                if isinstance(message_id, str) and isinstance(message.get("usage"), dict):
                    usage[message_id] = message["usage"]
                texts: List[str] = []
                for raw in as_list(content):
                    block = as_dict(raw)
                    block_type = block.get("type")
                    if block_type == "tool_use":
                        action = map_tool(str(block.get("name") or "tool"), as_dict(block.get("input")))
                        action.ts = ts
                        action.call_id = str(block.get("id") or "")
                        session.actions.append(action)
                        if action.call_id:
                            pending[action.call_id] = action
                    elif block_type == "text" and isinstance(block.get("text"), str):
                        texts.append(block["text"])
                joined = "\n".join(texts).strip()
                if joined and not entry.get("isSidechain"):
                    session.last_message = joined

            elif kind == "user":
                if isinstance(content, str):
                    self._prompt(session, entry, content, ts)
                else:
                    blocks = [as_dict(b) for b in as_list(content)]
                    results = [b for b in blocks if b.get("type") == "tool_result"]
                    for block in results:
                        action = pending.get(str(block.get("tool_use_id") or ""))
                        if action is None:
                            continue
                        body = text_of(block.get("content"))
                        if block.get("is_error"):
                            action.ok = False
                            action.detail = squash(body, 220)
                        else:
                            action.ok = True
                    if not results:
                        self._prompt(session, entry, text_of(content), ts)

        if usage:
            session.tokens = {
                "input": sum(int(u.get("input_tokens") or 0) for u in usage.values()),
                "output": sum(int(u.get("output_tokens") or 0) for u in usage.values()),
                "cache_read": sum(int(u.get("cache_read_input_tokens") or 0) for u in usage.values()),
                "cache_write": sum(int(u.get("cache_creation_input_tokens") or 0) for u in usage.values()),
            }
        return self.finish(session, stats)

    @staticmethod
    def _prompt(session: Session, entry: Dict[str, Any], text: str, ts: Any) -> None:
        text = (text or "").strip()
        if not text or entry.get("isMeta") or entry.get("isSidechain"):
            return
        if text.startswith(_SKIP_PROMPT_PREFIXES):
            return
        session.prompt_times.append(ts)
        if not session.title:
            session.title = squash(text, 110)
