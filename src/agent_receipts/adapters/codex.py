"""Codex CLI: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

Each line is {timestamp, type, payload}. Tool calls live in `response_item` lines.
The reader is deliberately forgiving: it accepts commands as text or as a list,
and results as plain text, JSON text or an object, because Codex has changed these
details between versions.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import COMMAND, DELETE, EDIT, OTHER, READ, SEARCH, WEB, WRITE, Action, Session
from ..util import as_dict, as_list, home_dir, iter_jsonl, parse_ts, squash, text_of
from .base import Adapter, env_path, first_text, newest_first, summarize_args

_SHELL_TOOLS = {
    "shell", "shell_command", "container.exec", "exec_command", "local_shell",
    "bash", "run_shell_command", "unified_exec",
}
_SHELLS = {"bash", "sh", "zsh", "dash", "fish", "ksh"}
_RESPONSE_TYPES = {
    "message", "function_call", "function_call_output", "local_shell_call",
    "custom_tool_call", "custom_tool_call_output", "reasoning",
}
_INJECTED_PREFIXES = ("<environment_context", "<user_instructions", "# AGENTS.md instructions", "<user_shell_command")


def command_text(value: Any) -> str:
    """Turn a command given as text or as a list into one readable string."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        parts = [str(v) for v in value]
        if len(parts) >= 3 and os.path.basename(parts[0]) in _SHELLS and parts[1] in ("-c", "-lc", "-ic", "-cl", "-l", "-i"):
            return parts[-1]
        return " ".join(shlex.quote(p) for p in parts)
    return ""


def parse_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return {"input": raw}
        return data if isinstance(data, dict) else {"input": raw}
    return {}


def patch_actions(patch: str) -> List[Action]:
    actions: List[Action] = []
    for line in (patch or "").splitlines():
        match = re.match(r"^\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$", line)
        if match:
            kind = {"Add": WRITE, "Update": EDIT, "Delete": DELETE}[match.group(1)]
            actions.append(Action("apply_patch", kind, match.group(2)))
            continue
        move = re.match(r"^\*\*\* Move to:\s*(.+?)\s*$", line)
        if move:
            actions.append(Action("apply_patch", EDIT, move.group(1)))
    if not actions:
        actions.append(Action("apply_patch", OTHER, "patch"))
    return actions


def call_actions(name: str, args: Dict[str, Any]) -> List[Action]:
    if name in _SHELL_TOOLS:
        cmd = command_text(args.get("command") if args.get("command") is not None else args.get("cmd"))
        return [Action(name, COMMAND, cmd)]
    if name == "apply_patch":
        return patch_actions(first_text(args, ["input", "patch"]))
    if name in ("read_file", "view_image", "list_dir", "read_mcp_resource"):
        return [Action(name, READ, first_text(args, ["file_path", "path", "dir_path", "uri"]))]
    if name in ("grep_files", "search"):
        return [Action(name, SEARCH, first_text(args, ["pattern", "query"]))]
    if name == "web_search":
        return [Action(name, WEB, first_text(args, ["query"]))]
    return [Action(name or "tool", OTHER, summarize_args(args))]


def read_result(output: Any) -> Tuple[Optional[bool], str]:
    """Return (ok, short error text). ok is None when the log gives no clear answer."""
    code: Optional[int] = None
    success: Optional[bool] = None
    text = ""
    data: Any = output
    if isinstance(output, str):
        stripped = output.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except ValueError:
                data = output
    if isinstance(data, dict):
        meta = as_dict(data.get("metadata"))
        if isinstance(meta.get("exit_code"), int):
            code = meta["exit_code"]
        if isinstance(data.get("success"), bool):
            success = data["success"]
        body = data.get("output")
        if body is None:
            body = data.get("content")
        text = text_of(body) if not isinstance(body, str) else body
    elif isinstance(data, str):
        text = data
    if code is None and text:
        match = re.search(r"(?:^|\n)\s*Exit code:\s*(-?\d+)", text)
        if match:
            code = int(match.group(1))
        else:
            match = re.search(r"process exited with code (-?\d+)", text)
            if match:
                code = int(match.group(1))
    if code is not None:
        ok: Optional[bool] = code == 0
    else:
        ok = success
    return ok, ("" if ok is not False else squash(text, 220))


class CodexAdapter(Adapter):
    id = "codex"
    label = "Codex CLI"
    aliases = ("codex-cli", "openai-codex")
    where = "~/.codex/sessions/"

    def default_roots(self) -> List[Path]:
        roots: List[Path] = []
        env = env_path("CODEX_HOME")
        if env:
            roots.append(env / "sessions")
        roots.append(home_dir() / ".codex" / "sessions")
        return roots

    def find(self, root: Optional[Path] = None) -> List[Path]:
        found: List[Path] = []
        for base in self.roots(root):
            if base.is_dir():
                found.extend(base.glob("**/rollout-*.jsonl"))
                found.extend(base.glob("**/rollout-*.jsonl.zst"))
        return newest_first(found)

    def load(self, path: Path) -> Session:
        stats: Dict[str, int] = {}
        session = self.new_session(path, re.sub(r"\.jsonl(\.zst)?$", "", path.name))
        pending: Dict[str, List[Action]] = {}
        first = True

        for line in iter_jsonl(path, stats):
            ts = parse_ts(line.get("timestamp"))
            kind = line.get("type")
            payload = line.get("payload")
            if not isinstance(payload, dict):
                # Older files have no envelope: the line is the payload itself.
                payload = line
                if first and kind is None and "id" in line:
                    session.session_id = str(line.get("id"))
                    if isinstance(line.get("cwd"), str):
                        session.cwd = line["cwd"]
                    session.note_time(parse_ts(line.get("timestamp")))
                    first = False
                    continue
                kind = "response_item" if line.get("type") in _RESPONSE_TYPES else kind
            first = False
            session.note_time(ts)

            if kind == "session_meta":
                if payload.get("id"):
                    session.session_id = str(payload["id"])
                if isinstance(payload.get("cwd"), str):
                    session.cwd = payload["cwd"]
                session.note_time(parse_ts(payload.get("timestamp")))
            elif kind == "turn_context":
                if session.cwd is None and isinstance(payload.get("cwd"), str):
                    session.cwd = payload["cwd"]
            elif kind == "event_msg":
                self._event(session, payload, ts)
            elif kind == "response_item":
                self._item(session, payload, ts, pending)

        return self.finish(session, stats)

    # -- pieces ------------------------------------------------------------------

    @staticmethod
    def _event(session: Session, payload: Dict[str, Any], ts: Any) -> None:
        etype = payload.get("type")
        if etype == "user_message":
            text = str(payload.get("message") or payload.get("text") or "").strip()
            if text and not text.startswith(_INJECTED_PREFIXES):
                session.prompt_times.append(ts)
                if not session.title:
                    session.title = squash(text, 110)
        elif etype == "agent_message":
            text = str(payload.get("message") or payload.get("text") or "").strip()
            if text:
                session.last_message = text
        elif etype == "token_count":
            info = as_dict(payload.get("info"))
            total = as_dict(info.get("total_token_usage")) or payload
            mapping = (("input_tokens", "input"), ("output_tokens", "output"), ("cached_input_tokens", "cache_read"))
            for source_key, target_key in mapping:
                value = total.get(source_key)
                if isinstance(value, int):
                    session.tokens[target_key] = max(session.tokens.get(target_key, 0), value)

    @staticmethod
    def _item(session: Session, payload: Dict[str, Any], ts: Any, pending: Dict[str, List[Action]]) -> None:
        ptype = payload.get("type")
        if ptype == "message":
            if payload.get("role") == "assistant":
                text = text_of(payload.get("content")).strip()
                if text:
                    session.last_message = text
        elif ptype in ("function_call", "custom_tool_call", "local_shell_call"):
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if ptype == "local_shell_call":
                acts = [Action("local_shell", COMMAND, command_text(as_dict(payload.get("action")).get("command")))]
            elif ptype == "custom_tool_call":
                name = str(payload.get("name") or "tool")
                raw = payload.get("input")
                args = {"input": raw} if isinstance(raw, str) else parse_arguments(raw)
                acts = call_actions(name, args)
            else:
                acts = call_actions(str(payload.get("name") or "tool"), parse_arguments(payload.get("arguments")))
            for action in acts:
                action.ts = ts
                action.call_id = call_id
                session.actions.append(action)
            if call_id:
                pending[call_id] = acts
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            acts = pending.get(str(payload.get("call_id") or ""))
            if not acts:
                return
            ok, detail = read_result(payload.get("output"))
            for action in acts:
                action.ok = ok
                action.detail = detail
