"""OpenCode (file storage layout).

~/.local/share/opencode/storage/
    session/<project>/ses_*.json     one file per session
    message/<session id>/msg_*.json  one file per message
    part/<message id>/prt_*.json     one file per piece of a message (text, tool call...)

Newer OpenCode versions can keep sessions in a database instead. This reader does not
open that database; `doctor` says so when it finds one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import COMMAND, EDIT, OTHER, READ, SEARCH, SUBAGENT, WEB, WRITE, Action, Session
from ..util import AdapterError, as_dict, home_dir, parse_ts, read_json, squash
from .base import Adapter, env_path, first_text, newest_first, summarize_args

_PATH_KEYS = ["filePath", "file_path", "path"]


def map_tool(name: str, args: Dict[str, Any]) -> Action:
    if name == "bash":
        return Action(name, COMMAND, first_text(args, ["command"]))
    if name == "read":
        return Action(name, READ, first_text(args, _PATH_KEYS))
    if name == "list":
        return Action(name, READ, first_text(args, ["path"]))
    if name == "write":
        return Action(name, WRITE, first_text(args, _PATH_KEYS))
    if name in ("edit", "multiedit"):
        return Action(name, EDIT, first_text(args, _PATH_KEYS))
    if name in ("glob", "grep"):
        return Action(name, SEARCH, first_text(args, ["pattern", "query"]))
    if name in ("webfetch", "websearch"):
        return Action(name, WEB, first_text(args, ["url", "query"]))
    if name == "task":
        return Action(name, SUBAGENT, first_text(args, ["description", "prompt"]))
    return Action(name or "tool", OTHER, summarize_args(args))


class OpenCodeAdapter(Adapter):
    id = "opencode"
    label = "OpenCode"
    aliases = ("open-code",)
    where = "~/.local/share/opencode/storage/"

    def data_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        xdg = env_path("XDG_DATA_HOME")
        if xdg:
            dirs.append(xdg / "opencode")
        dirs.append(home_dir() / ".local" / "share" / "opencode")
        return dirs

    def default_roots(self) -> List[Path]:
        return [d / "storage" for d in self.data_dirs()]

    def find(self, root: Optional[Path] = None) -> List[Path]:
        found: List[Path] = []
        for base in self.roots(root):
            if not base.is_dir():
                continue
            for path in base.glob("session/*/ses_*.json"):
                try:
                    if as_dict(read_json(path)).get("parentID"):
                        continue  # a helper session started by another session
                except AdapterError:
                    continue
                found.append(path)
        return newest_first(found)

    def problem(self, root: Optional[Path] = None) -> Optional[str]:
        if root is not None:
            return None
        for data_dir in self.data_dirs():
            if data_dir.is_dir() and any(data_dir.glob("*.db")):
                return (
                    "OpenCode seems to keep its sessions in a database (a .db file), "
                    "which Agent Receipts cannot read yet."
                )
        return None

    def load(self, path: Path) -> Session:
        info = as_dict(read_json(path))
        session = self.new_session(path, str(info.get("id") or path.stem))
        times = as_dict(info.get("time"))
        session.note_time(parse_ts(times.get("created")))
        session.note_time(parse_ts(times.get("updated")))
        if isinstance(info.get("directory"), str):
            session.cwd = info["directory"]
        title = info.get("title")
        stats: Dict[str, int] = {}

        try:
            storage = path.parents[2]
        except IndexError:
            storage = path.parent
        message_dir = storage / "message" / session.session_id

        messages: List[Dict[str, Any]] = []
        if message_dir.is_dir():
            for file in sorted(message_dir.glob("*.json")):
                try:
                    data = as_dict(read_json(file))
                except AdapterError:
                    stats["bad"] = stats.get("bad", 0) + 1
                    continue
                if data:
                    data.setdefault("id", file.stem)
                    messages.append(data)
        messages.sort(key=lambda m: (as_dict(m.get("time")).get("created") or 0, str(m.get("id"))))

        for message in messages:
            created = parse_ts(as_dict(message.get("time")).get("created"))
            session.note_time(created)
            role = message.get("role")
            part_dir = storage / "part" / str(message.get("id"))
            if not part_dir.is_dir():
                continue
            for file in sorted(part_dir.glob("*.json")):
                try:
                    part = as_dict(read_json(file))
                except AdapterError:
                    stats["bad"] = stats.get("bad", 0) + 1
                    continue
                ptype = part.get("type")
                if ptype == "text" and not part.get("synthetic"):
                    text = str(part.get("text") or "").strip()
                    if not text:
                        continue
                    if role == "user":
                        session.prompt_times.append(created)
                        if not session.title and not title:
                            session.title = squash(text, 110)
                    elif role == "assistant":
                        session.last_message = text
                elif ptype == "tool":
                    self._tool(session, part, created)

        if isinstance(title, str) and title and not session.title:
            session.title = squash(title, 110)
        return self.finish(session, stats)

    @staticmethod
    def _tool(session: Session, part: Dict[str, Any], fallback_ts: Any) -> None:
        state = as_dict(part.get("state"))
        action = map_tool(str(part.get("tool") or "tool"), as_dict(state.get("input")))
        action.call_id = str(part.get("callID") or part.get("id") or "")
        started = parse_ts(as_dict(state.get("time")).get("start"))
        action.ts = started or fallback_ts
        session.note_time(action.ts)
        status = state.get("status")
        if status == "completed":
            action.ok = True
        elif status == "error":
            action.ok = False
            action.detail = squash(state.get("error") or state.get("output") or "", 220)
        session.actions.append(action)
