"""Gemini CLI: ~/.gemini/tmp/<project-hash>/chats/session-*.json"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import COMMAND, EDIT, OTHER, READ, SEARCH, WEB, WRITE, Action, Session
from ..util import as_dict, as_list, home_dir, parse_ts, read_json, squash, text_of
from .base import Adapter, first_text, newest_first, summarize_args

_PATH_KEYS = ["file_path", "absolute_path", "path", "filepath"]


def map_tool(name: str, args: Dict[str, Any]) -> Action:
    if name in ("run_shell_command", "shell"):
        return Action(name, COMMAND, first_text(args, ["command"]))
    if name == "read_file":
        return Action(name, READ, first_text(args, _PATH_KEYS))
    if name == "read_many_files":
        paths = args.get("paths") or args.get("include")
        shown = ", ".join(str(p) for p in paths[:3]) if isinstance(paths, list) else ""
        return Action(name, READ, shown)
    if name in ("list_directory", "ls"):
        return Action(name, READ, first_text(args, ["dir_path", "path"]))
    if name == "write_file":
        return Action(name, WRITE, first_text(args, _PATH_KEYS))
    if name in ("replace", "edit"):
        return Action(name, EDIT, first_text(args, _PATH_KEYS))
    if name in ("glob", "search_file_content", "grep", "grep_search"):
        return Action(name, SEARCH, first_text(args, ["pattern", "query"]))
    if name in ("web_fetch", "google_web_search"):
        return Action(name, WEB, first_text(args, ["url", "query", "prompt"]))
    return Action(name or "tool", OTHER, summarize_args(args))


class GeminiAdapter(Adapter):
    id = "gemini"
    label = "Gemini CLI"
    aliases = ("gemini-cli",)
    where = "~/.gemini/tmp/<project>/chats/"

    def default_roots(self) -> List[Path]:
        return [home_dir() / ".gemini" / "tmp"]

    def find(self, root: Optional[Path] = None) -> List[Path]:
        found: List[Path] = []
        for base in self.roots(root):
            if base.is_dir():
                found.extend(base.glob("*/chats/session-*.json"))
                found.extend(base.glob("chats/session-*.json"))
                found.extend(base.glob("session-*.json"))
        return newest_first(found)

    def load(self, path: Path) -> Session:
        data = as_dict(read_json(path))
        session = self.new_session(path, str(data.get("sessionId") or path.stem))
        session.note_time(parse_ts(data.get("startTime")))
        session.note_time(parse_ts(data.get("lastUpdated")))
        totals = {"input": 0, "output": 0, "cache_read": 0}
        saw_tokens = False

        for raw in as_list(data.get("messages")):
            message = as_dict(raw)
            ts = parse_ts(message.get("timestamp"))
            session.note_time(ts)
            kind = message.get("type")
            if kind == "user":
                text = text_of(message.get("content")).strip()
                if text:
                    session.prompt_times.append(ts)
                    if not session.title:
                        session.title = squash(text, 110)
            elif kind == "gemini":
                text = text_of(message.get("content")).strip()
                if text:
                    session.last_message = text
                tokens = as_dict(message.get("tokens"))
                if tokens:
                    saw_tokens = True
                    totals["input"] += int(tokens.get("input") or 0)
                    totals["output"] += int(tokens.get("output") or 0)
                    totals["cache_read"] += int(tokens.get("cached") or 0)
                for raw_call in as_list(message.get("toolCalls")):
                    call = as_dict(raw_call)
                    action = map_tool(str(call.get("name") or "tool"), as_dict(call.get("args")))
                    action.ts = parse_ts(call.get("timestamp")) or ts
                    action.call_id = str(call.get("id") or "")
                    session.note_time(action.ts)
                    status = call.get("status")
                    if status in ("success", "error"):
                        action.ok = status == "success"
                    elif status in ("cancelled", "canceled"):
                        action.ok = False
                        action.detail = "Cancelled"
                    if action.ok is False and not action.detail:
                        action.detail = self._error_text(call)
                    session.actions.append(action)
        if saw_tokens:
            session.tokens = totals
        self._guess_project(session)
        return self.finish(session)

    @staticmethod
    def _guess_project(session: Session) -> None:
        """Gemini logs do not record the project folder, so guess it from the files it touched."""
        if session.cwd:
            return
        folders = [
            posixpath.dirname(a.target)
            for a in session.actions
            if a.kind in (READ, WRITE, EDIT) and a.target.startswith("/")
        ]
        if not folders:
            return
        try:
            common = posixpath.commonpath(folders)
        except ValueError:
            return
        home = str(home_dir()).replace("\\", "/").rstrip("/")
        if len([p for p in common.split("/") if p]) < 2 or common.rstrip("/") == home:
            return
        session.cwd = common
        session.warnings.append("Gemini CLI does not save the project folder, so it was guessed from the files the agent touched.")

    @staticmethod
    def _error_text(call: Dict[str, Any]) -> str:
        for result in as_list(call.get("result")):
            response = as_dict(as_dict(as_dict(result).get("functionResponse")).get("response"))
            for key in ("error", "output"):
                if isinstance(response.get(key), str) and response[key]:
                    return squash(response[key], 220)
        display = call.get("resultDisplay")
        return squash(display, 220) if isinstance(display, str) else ""
