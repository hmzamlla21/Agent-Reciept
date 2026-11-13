"""Turn a parsed Session into a receipt: plain data that every output format shares."""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .model import (
    COMMAND,
    DELETE,
    EDIT,
    HIGH,
    LEVEL_ORDER,
    MEDIUM,
    OTHER,
    READ,
    SEARCH,
    SUBAGENT,
    WEB,
    WRITE,
    Action,
    Session,
)
from .redact import redact
from .risk import is_test_command
from .util import iso, nice_path, squash

SCHEMA = "agent-receipt/1"


def _identity(text: str) -> str:
    return text


def _filter_window(
    session: Session, since_seconds: Optional[float], turn_only: bool
) -> Tuple[List[Action], str]:
    actions = session.actions
    if turn_only:
        stamps = [t for t in session.prompt_times if t is not None]
        if stamps:
            cutoff = stamps[-1]
            kept = [a for a in actions if a.ts is not None and a.ts >= cutoff]
            return kept, "Latest request only (since your last message)"
        return actions, "Whole session (the log has no message times, so the latest request could not be isolated)"
    if since_seconds is not None:
        now = datetime.now(timezone.utc)
        kept = [a for a in actions if a.ts is not None and (now - a.ts).total_seconds() <= since_seconds]
        return kept, "Actions from the last " + _words(since_seconds)
    return actions, "Whole session"


def _words(seconds: float) -> str:
    if seconds % 86400 == 0:
        n, unit = int(seconds // 86400), "day"
    elif seconds % 3600 == 0:
        n, unit = int(seconds // 3600), "hour"
    elif seconds % 60 == 0:
        n, unit = int(seconds // 60), "minute"
    else:
        n, unit = int(seconds), "second"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def _counted(actions: List[Action], kinds: tuple, cwd: Optional[str]) -> List[Dict[str, Any]]:
    seen: "OrderedDict[str, int]" = OrderedDict()
    for action in actions:
        if action.kind in kinds and action.target:
            shown = nice_path(action.target, cwd)
            seen[shown] = seen.get(shown, 0) + 1
    return [{"path": path, "count": count} for path, count in seen.items()]


def _verification(actions: List[Action], clean: Callable[[str], str], cwd: Optional[str]) -> Dict[str, Any]:
    tests = [(i, a) for i, a in enumerate(actions) if a.kind == COMMAND and is_test_command(a.target)]
    if not tests:
        return {
            "ran": False,
            "verdict": "none",
            "message": "No test command was run, so nothing here proves the work is correct.",
        }
    last_index, last = tests[-1]
    after: "OrderedDict[str, None]" = OrderedDict()
    for later in actions[last_index + 1:]:
        if later.kind in (EDIT, WRITE, DELETE) and later.target:
            after[nice_path(later.target, cwd)] = None
    edited_after = list(after)
    tail = ""
    if edited_after:
        n = len(edited_after)
        tail = f" {n} file{'s were' if n != 1 else ' was'} changed after that run."
    if last.ok is True:
        verdict, message = ("pass-but-stale" if edited_after else "pass"), "The last test run passed."
        if edited_after:
            message = "The last test run passed, but" + tail
    elif last.ok is False:
        verdict, message = "fail", "The last test run failed." + tail
    else:
        verdict, message = "unknown", "Tests were run, but the log does not say whether they passed." + tail
    return {
        "ran": True,
        "verdict": verdict,
        "message": message.strip(),
        "runs": len(tests),
        "last_time": iso(last.ts),
        "last_command": clean(last.target),
        "last_ok": last.ok,
        "edited_after": edited_after,
    }


_PASS_CLAIM = re.compile(
    r"\b(?:all|every|the)?\s*tests?\s+(?:are\s+|now\s+|all\s+)*(?:pass(?:ing|ed|es)?|green)\b"
    r"|\beverything\s+(?:now\s+)?(?:pass(?:es|ing)?|works|is\s+(?:working|green|passing))\b"
    r"|\b(?:build|ci|suite)\s+(?:is\s+)?(?:now\s+)?(?:green|passing)\b"
    r"|\ball\s+green\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\b(?:not|n't|no|never|without|until|still|unless|fail\w*)\b[^.\n]{0,25}$", re.IGNORECASE)


def _claim_mismatch(message: str, verification: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag when the agent says tests pass but the log does not back that up."""
    verdict = verification["verdict"]
    if not message or verdict == "pass":
        return None
    hit = None
    for match in _PASS_CLAIM.finditer(message):
        before = message[max(0, match.start() - 40): match.start()]
        if not _NEGATED.search(before):
            hit = match
            break
    if hit is None:
        return None
    reason = {
        "fail": "but the last test run failed",
        "pass-but-stale": "but files were changed after the last passing run",
        "none": "but no test command was run at all",
        "unknown": "but the log does not show the tests passing",
    }[verdict]
    return {
        "level": HIGH,
        "code": "claim-mismatch",
        "message": f"The agent said the tests pass, {reason}",
        "count": 1,
        "time": None,
        "tool": "",
        "example": squash(hit.group(0), 80),
    }


def build(
    session: Session,
    *,
    redact_secrets: bool = True,
    since_seconds: Optional[float] = None,
    turn_only: bool = False,
) -> Dict[str, Any]:
    clean = redact if redact_secrets else _identity
    actions, window = _filter_window(session, since_seconds, turn_only)
    cwd = session.cwd

    commands = [a for a in actions if a.kind == COMMAND]
    failed = [a for a in actions if a.ok is False]

    # Group the same warning so ten `npm install`s read as one line.
    groups: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for action in actions:
        for risk in action.risks:
            key = risk.code + "|" + risk.message
            entry = groups.get(key)
            if entry is None:
                groups[key] = {
                    "level": risk.level,
                    "code": risk.code,
                    "message": clean(risk.message),
                    "count": 1,
                    "time": iso(action.ts),
                    "tool": action.tool,
                    "example": clean(action.target),
                }
            else:
                entry["count"] += 1
    verification = _verification(actions, clean, cwd)
    # With a time filter that leaves nothing, the agent's last words fall outside the window.
    skip_claims = since_seconds is not None and not actions
    mismatch = None if skip_claims else _claim_mismatch(session.last_message, verification)
    if mismatch:
        groups["claim-mismatch"] = mismatch
    attention = sorted(groups.values(), key=lambda e: LEVEL_ORDER.get(e["level"], 9))

    def top_level(action: Action) -> Optional[str]:
        if not action.risks:
            return None
        return min((r.level for r in action.risks), key=lambda lv: LEVEL_ORDER.get(lv, 9))

    duration = None
    if session.start and session.end:
        duration = max((session.end - session.start).total_seconds(), 0.0)

    summary = {
        "actions": len(actions),
        "commands": len(commands),
        "commands_failed": sum(1 for a in commands if a.ok is False),
        "failed": len(failed),
        "files_edited": len({a.target for a in actions if a.kind == EDIT and a.target}),
        "files_written": len({a.target for a in actions if a.kind == WRITE and a.target}),
        "files_deleted": len({a.target for a in actions if a.kind == DELETE and a.target}),
        "files_read": len({a.target for a in actions if a.kind == READ and a.target}),
        "searches": sum(1 for a in actions if a.kind == SEARCH),
        "web": sum(1 for a in actions if a.kind == WEB),
        "subagents": sum(1 for a in actions if a.kind == SUBAGENT),
        "other": sum(1 for a in actions if a.kind == OTHER),
        "attention_high": sum(1 for e in attention if e["level"] == HIGH),
        "attention_medium": sum(1 for e in attention if e["level"] == MEDIUM),
        "tokens": dict(session.tokens) if session.tokens else None,
    }

    return {
        "schema": SCHEMA,
        "tool_version": __version__,
        "generated_at": iso(datetime.now(timezone.utc)),
        "redacted": bool(redact_secrets),
        "agent": {"id": session.agent_id, "label": session.agent_label},
        "session": {
            "id": session.session_id,
            "title": clean(session.title),
            "cwd": nice_path(cwd, None) if cwd else None,
            "start": iso(session.start),
            "end": iso(session.end),
            "duration_seconds": duration,
            "source": nice_path(str(session.source), None),
            "prompts": len(session.prompt_times),
        },
        "window": window,
        "summary": summary,
        "attention": attention,
        "verification": verification,
        "files": {
            "edited": _counted(actions, (EDIT,), cwd),
            "written": _counted(actions, (WRITE,), cwd),
            "deleted": _counted(actions, (DELETE,), cwd),
            "read": _counted(actions, (READ,), cwd),
        },
        "commands": [
            {
                "time": iso(a.ts),
                "command": clean(a.target),
                "ok": a.ok,
                "detail": clean(a.detail),
                "risk": top_level(a),
            }
            for a in commands
        ],
        "failures": [
            {
                "time": iso(a.ts),
                "tool": a.tool,
                "kind": a.kind,
                "target": clean(nice_path(a.target, cwd) if a.kind != COMMAND else a.target),
                "detail": clean(a.detail),
            }
            for a in failed
        ],
        "last_message": clean(session.last_message) if session.last_message else None,
        "warnings": list(session.warnings),
    }
