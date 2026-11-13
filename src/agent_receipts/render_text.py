"""Terminal and Markdown receipts."""

from __future__ import annotations

import textwrap
from typing import Any, Dict, List, Optional

from .util import human_duration, local_clock, local_stamp, squash

DEFAULT_COMMAND_LIMIT = 40


class Style:
    """ANSI colour helper that does nothing when colour is off."""

    CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, name: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{self.CODES[name]}m{text}\033[0m"


def _num(n: Optional[int]) -> str:
    return f"{n:,}" if isinstance(n, int) else "-"


def _tokens_line(tokens: Optional[Dict[str, int]]) -> str:
    if not tokens:
        return ""
    parts = [f"in {_num(tokens.get('input'))}", f"out {_num(tokens.get('output'))}"]
    if tokens.get("cache_read"):
        parts.append(f"cached {_num(tokens['cache_read'])}")
    return " | ".join(parts)


def _when(r: Dict[str, Any]) -> str:
    s = r["session"]
    if not s.get("start"):
        return "unknown"
    text = local_stamp(s["start"])
    if s.get("end") and s["end"] != s["start"]:
        text += " -> " + local_clock(s["end"])
    if s.get("duration_seconds") is not None:
        text += f"  ({human_duration(s['duration_seconds'])})"
    return text


def render_text(r: Dict[str, Any], style: Style, width: int = 80, limit: Optional[int] = None) -> str:
    width = max(60, min(width, 120))
    limit = DEFAULT_COMMAND_LIMIT if limit is None else limit
    s, sm, v = r["session"], r["summary"], r["verification"]
    out: List[str] = []
    rule = "=" * width

    def head(title: str) -> None:
        out.append("")
        out.append(style("bold", title))

    out.append(style("bold", f"AGENT RECEIPT  |  {r['agent']['label']}"))
    out.append(rule)
    out.append(f"{'Project':<10}{s.get('cwd') or 'unknown'}")
    if s.get("title"):
        out.append(f"{'Task':<10}\"{squash(s['title'], width - 14)}\"")
    out.append(f"{'Session':<10}{s['id'][:36]}")
    out.append(f"{'When':<10}{_when(r)}")
    out.append(f"{'Showing':<10}{r['window']}")

    head("WHAT HAPPENED")
    cmd_note = f"  ({sm['commands_failed']} failed)" if sm["commands_failed"] else ""
    out.append(f"  Commands run   {sm['commands']:>4}{cmd_note}")
    out.append(
        f"  Files          {sm['files_edited']} edited, {sm['files_written']} written, "
        f"{sm['files_deleted']} deleted, {sm['files_read']} read"
    )
    out.append(f"  Also           {sm['searches']} searches, {sm['web']} web lookups, {sm['subagents']} sub-agent tasks")
    tokens = _tokens_line(sm.get("tokens"))
    if tokens:
        out.append(f"  Tokens         {tokens}")

    attention: List[Dict[str, Any]] = r["attention"]
    head(f"NEEDS YOUR ATTENTION ({len(attention)})" if attention else "NEEDS YOUR ATTENTION")
    if not attention:
        out.append("  " + style("green", "Nothing risky was detected."))
    for item in attention:
        tag = {"high": style("red", "[HIGH]  "), "medium": style("yellow", "[MEDIUM]"), "info": style("dim", "[note]  ")}[item["level"]]
        count = f"  x{item['count']}" if item["count"] > 1 else ""
        clock = f"  {local_clock(item['time'])}" if item.get("time") else ""
        out.append(f"  {tag} {item['message']}{count}{style('dim', clock)}")
        if item["level"] != "info" and item.get("example") and item["example"] != item["message"]:
            out.append(style("dim", "           " + squash(item["example"], width - 14)))

    head("TESTS")
    mark = {
        "pass": style("green", "[PASS]"),
        "pass-but-stale": style("yellow", "[STALE]"),
        "fail": style("red", "[FAIL]"),
        "unknown": style("yellow", "[ ?? ]"),
        "none": style("yellow", "[NONE]"),
    }[v["verdict"]]
    out.append(f"  {mark} {v['message']}")
    if v.get("ran"):
        out.append(style("dim", f"         {v['runs']} test run(s). Last: {squash(v['last_command'], width - 30)}"))
        for path in v.get("edited_after", [])[:5]:
            out.append(style("dim", f"         changed after: {path}"))

    files = r["files"]
    changed = [("edited", x) for x in files["edited"]] + [("written", x) for x in files["written"]] + [("deleted", x) for x in files["deleted"]]
    head(f"FILES CHANGED ({len(changed)})")
    if not changed:
        out.append("  None through the agent's file tools. (Changes made by shell commands are not listed.)")
    for label, item in changed[:60]:
        times = f"  (x{item['count']})" if item["count"] > 1 else ""
        out.append(f"  {label:<8}{squash(item['path'], width - 20)}{times}")
    if len(changed) > 60:
        out.append(f"  ... and {len(changed) - 60} more (use --format json for the full list)")

    commands = r["commands"]
    head(f"COMMANDS ({len(commands)})")
    if not commands:
        out.append("  None.")
    shown = commands if limit == 0 else commands[:limit]
    for c in shown:
        status = {True: style("green", "[ok]  "), False: style("red", "[FAIL]"), None: style("dim", "[ ?  ]")}[c["ok"]]
        flag = ""
        if c["risk"] == "high":
            flag = style("red", " !")
        elif c["risk"] == "medium":
            flag = style("yellow", " !")
        out.append(f"  {local_clock(c['time']):<9}{status} {squash(c['command'], width - 22)}{flag}")
    if len(shown) < len(commands):
        out.append(f"  ... and {len(commands) - len(shown)} more (use --max-items 0 to show all)")

    failures = r["failures"]
    if failures:
        head(f"THINGS THAT FAILED ({len(failures)})")
        for f in failures[:10]:
            out.append(f"  {local_clock(f['time']):<9}{f['tool']}: {squash(f['target'], width - 24)}")
            if f.get("detail"):
                out.append(style("dim", "           " + squash(f["detail"], width - 14)))
        if len(failures) > 10:
            out.append(f"  ... and {len(failures) - 10} more")

    if r.get("last_message"):
        head("WHAT THE AGENT SAID LAST")
        for line in textwrap.wrap(squash(r["last_message"], 900), width - 4) or [""]:
            out.append("  " + line)

    if r["warnings"]:
        head("NOTES")
        for w in r["warnings"]:
            out.append("  - " + w)

    out.append("")
    out.append(style("dim", "Warnings are hints for a quick review, not a security guarantee. Secrets are hidden." if r["redacted"] else "Secret hiding is OFF for this receipt. Do not share it."))
    return "\n".join(out) + "\n"


_MD_VERDICT = {
    "pass": "✅",
    "pass-but-stale": "⚠️",
    "fail": "❌",
    "unknown": "❔",
    "none": "⚠️",
}


def _md_escape(text: str) -> str:
    return text.replace("`", "'").replace("|", "\\|")


def render_markdown(r: Dict[str, Any], limit: Optional[int] = None) -> str:
    limit = DEFAULT_COMMAND_LIMIT if limit is None else limit
    s, sm, v = r["session"], r["summary"], r["verification"]
    out: List[str] = [f"# Agent Receipt: {r['agent']['label']}", ""]
    out.append(f"- **Project:** `{_md_escape(s.get('cwd') or 'unknown')}`")
    if s.get("title"):
        out.append(f"- **Task:** {_md_escape(squash(s['title'], 140))}")
    out.append(f"- **When:** {_when(r)}")
    out.append(f"- **Showing:** {r['window']}")
    out.append("")
    out.append("## What happened")
    out.append("")
    out.append("| Commands | Failed | Edited | Written | Deleted | Read | Searches | Web |")
    out.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    out.append(
        f"| {sm['commands']} | {sm['commands_failed']} | {sm['files_edited']} | {sm['files_written']} | "
        f"{sm['files_deleted']} | {sm['files_read']} | {sm['searches']} | {sm['web']} |"
    )
    tokens = _tokens_line(sm.get("tokens"))
    if tokens:
        out += ["", f"Tokens: {tokens}"]

    out += ["", "## Needs your attention", ""]
    if not r["attention"]:
        out.append("Nothing risky was detected.")
    for item in r["attention"]:
        icon = {"high": "\U0001f534", "medium": "\U0001f7e1", "info": "ℹ️"}[item["level"]]
        count = f" (x{item['count']})" if item["count"] > 1 else ""
        out.append(f"- {icon} **{item['level'].upper()}**: {_md_escape(item['message'])}{count}")

    out += ["", "## Tests", "", f"{_MD_VERDICT[v['verdict']]} {v['message']}"]
    files = r["files"]
    changed = [("edited", x) for x in files["edited"]] + [("written", x) for x in files["written"]] + [("deleted", x) for x in files["deleted"]]
    out += ["", f"## Files changed ({len(changed)})", ""]
    if not changed:
        out.append("None through the agent's file tools.")
    for label, item in changed[:80]:
        times = f" (x{item['count']})" if item["count"] > 1 else ""
        out.append(f"- {label}: `{_md_escape(item['path'])}`{times}")
    if len(changed) > 80:
        out.append(f"- ... and {len(changed) - 80} more")

    commands = r["commands"]
    out += ["", f"## Commands ({len(commands)})", ""]
    shown = commands if limit == 0 else commands[:limit]
    for c in shown:
        icon = {True: "✅", False: "❌", None: "❔"}[c["ok"]]
        out.append(f"- {icon} `{local_clock(c['time'])}` `{_md_escape(squash(c['command'], 150))}`")
    if len(shown) < len(commands):
        out.append(f"- ... and {len(commands) - len(shown)} more")

    if r["failures"]:
        out += ["", f"## Things that failed ({len(r['failures'])})", ""]
        for f in r["failures"][:15]:
            detail = f" - {_md_escape(squash(f['detail'], 140))}" if f.get("detail") else ""
            out.append(f"- `{_md_escape(squash(f['target'], 100))}`{detail}")

    if r.get("last_message"):
        out += ["", "## What the agent said last", ""]
        out += ["> " + line for line in textwrap.wrap(squash(r["last_message"], 900), 90)]
    if r["warnings"]:
        out += ["", "## Notes", ""] + [f"- {w}" for w in r["warnings"]]
    out += ["", "---", f"*Made by Agent Receipts {r['tool_version']}. Warnings are hints, not a security guarantee.*"]
    return "\n".join(out) + "\n"
