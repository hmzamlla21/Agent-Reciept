"""`agent-receipts install`: add a /receipt command inside each coding agent.

Each agent has its own folder for custom commands. This copies one small file into it.
Nothing is changed that was not created by this tool: an existing file with different
content is left alone unless --force is given.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .util import home_dir

try:  # Python 3.9+
    from importlib.resources import files as _res_files
except ImportError:  # pragma: no cover
    _res_files = None  # type: ignore[assignment]


@dataclass
class Target:
    id: str
    label: str
    config_dir: Callable[[], Path]
    dest: Callable[[], Path]
    template: str
    how_to_use: str


def _env_dir(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else fallback


def _claude_dir() -> Path:
    return _env_dir("CLAUDE_CONFIG_DIR", home_dir() / ".claude")


def _codex_dir() -> Path:
    return _env_dir("CODEX_HOME", home_dir() / ".codex")


def _gemini_dir() -> Path:
    return home_dir() / ".gemini"


def _opencode_dir() -> Path:
    return _env_dir("XDG_CONFIG_HOME", home_dir() / ".config") / "opencode"


def _opencode_dest() -> Path:
    base = _opencode_dir()
    folder = "commands" if (base / "commands").is_dir() and not (base / "command").is_dir() else "command"
    return base / folder / "receipt.md"


TARGETS: List[Target] = [
    Target("claude-code", "Claude Code", _claude_dir, lambda: _claude_dir() / "commands" / "receipt.md",
           "claude-code/receipt.md", "type /receipt in a Claude Code session"),
    Target("codex", "Codex CLI", _codex_dir, lambda: _codex_dir() / "prompts" / "receipt.md",
           "codex/receipt.md", "type /prompts:receipt in a Codex session"),
    Target("gemini", "Gemini CLI", _gemini_dir, lambda: _gemini_dir() / "commands" / "receipt.toml",
           "gemini/receipt.toml", "type /receipt in a Gemini CLI session"),
    Target("opencode", "OpenCode", _opencode_dir, _opencode_dest,
           "opencode/receipt.md", "type /receipt in an OpenCode session"),
]


def names() -> List[str]:
    return [t.id for t in TARGETS]


def _template(name: str) -> str:
    if _res_files is not None:
        return (_res_files("agent_receipts") / "integrations" / name).read_text(encoding="utf-8")
    return (Path(__file__).parent / "integrations" / name).read_text(encoding="utf-8")  # pragma: no cover


def _same(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n") == text
    except OSError:
        return False


def run(which: str, dry_run: bool, force: bool, uninstall: bool, out=None) -> int:
    """Returns 0 when everything asked for worked, 1 on a problem, 3 when nothing was found to do."""
    out = out or sys.stdout
    explicit = which != "all"
    chosen = [t for t in TARGETS if which in ("all", t.id)]
    problems = 0
    done = 0
    for target in chosen:
        dest = target.dest()
        if not explicit and not target.config_dir().is_dir():
            print(f"  skipped  {target.label:<12} not found on this computer (looked for {target.config_dir()})", file=out)
            continue
        text = _template(target.template)
        if uninstall:
            if not dest.exists():
                print(f"  nothing  {target.label:<12} no /receipt command installed", file=out)
            elif _same(dest, text) or force:
                if not dry_run:
                    try:
                        dest.unlink()
                    except OSError as exc:
                        print(f"  FAILED   {target.label:<12} could not remove {dest}: {exc.strerror or exc}", file=out)
                        problems += 1
                        continue
                print(f"  {'would remove' if dry_run else 'removed'}  {target.label:<12} {dest}", file=out)
                done += 1
            else:
                print(f"  kept     {target.label:<12} {dest} was changed by you. Use --force to remove it anyway.", file=out)
            continue
        if dest.exists() and _same(dest, text):
            print(f"  ok       {target.label:<12} already installed ({dest})", file=out)
            done += 1
            continue
        if dest.exists() and not force:
            print(f"  kept     {target.label:<12} {dest} already exists with different content. Use --force to replace it.", file=out)
            problems += 1
            continue
        if dry_run:
            print(f"  would install  {target.label:<12} {dest}", file=out)
            done += 1
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(str(dest), "w", encoding="utf-8", newline="\n") as handle:  # Path.write_text(newline=) needs 3.10
                handle.write(text)
        except OSError as exc:
            print(f"  FAILED   {target.label:<12} could not write {dest}: {exc.strerror or exc}", file=out)
            problems += 1
            continue
        print(f"  installed {target.label:<11} {dest}", file=out)
        print(f"           To use it: {target.how_to_use}", file=out)
        done += 1
    if problems:
        return 1
    if not done:
        print("\nNo supported coding agents were found. Name one to install anyway, for example:", file=out)
        print("  agent-receipts install claude-code", file=out)
        return 3
    if not uninstall and not dry_run:
        print("\nDone. If an agent was already open, restart it so it sees the new command.", file=out)
    return 0
