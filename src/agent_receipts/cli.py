"""The `agent-receipts` command."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, adapters
from . import install as installer
from .adapters.base import Adapter
from .model import Session
from .receipt import build
from .render_html import render_html
from .render_text import Style, render_markdown, render_text
from .util import AdapterError, human_duration, local_stamp, nice_path, parse_duration, squash, iso

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOTHING = 3

FORMATS = ("text", "md", "html", "json")


def install_names() -> List[str]:
    return installer.names()


def _err(message: str) -> None:
    print(f"agent-receipts: {message}", file=sys.stderr)


def _use_colour(args: argparse.Namespace, stream) -> bool:
    if args.no_color or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _fix_windows_output() -> None:
    """Make sure printing never crashes on an old Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-receipts",
        description="Get a plain-English receipt of what your AI coding agent just did: "
        "commands run, files changed, risky actions, and whether tests were run.",
        epilog="Examples:\n"
        "  agent-receipts                        receipt for your newest session\n"
        "  agent-receipts --turn                 only the agent's latest turn\n"
        "  agent-receipts --since 2h             only the last two hours\n"
        "  agent-receipts --format html --open   make a web page and open it\n"
        "  agent-receipts list                   see recent sessions\n"
        "  agent-receipts doctor                 check that your setup works\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"agent-receipts {__version__}")
    sub = p.add_subparsers(dest="command", metavar="{show,list,install,doctor}")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--agent", "-a", metavar="NAME", help="Which agent: " + ", ".join(adapters.names()) + ". Default: whichever was used most recently.")
        sp.add_argument("--dir", metavar="FOLDER", help="Read session logs from this folder instead of the default place.")
        sp.add_argument("--project", "-p", metavar="TEXT", help="Only sessions whose project folder contains this text. Use '.' for the current folder.")
        sp.add_argument("--no-color", action="store_true", help="Turn off colours.")

    show = sub.add_parser("show", help="Make a receipt (this is the default).", description="Make a receipt for one session.")
    common(show)
    show.add_argument("--file", "-f", metavar="PATH", help="Use this exact session file.")
    show.add_argument("--session", "-s", metavar="ID", help="Use the session whose id or file name starts with this.")
    show.add_argument("--nth", "-n", type=int, default=1, metavar="N", help="Use the Nth newest session (1 = newest). Default: 1.")
    show.add_argument("--turn", action="store_true", help="Only what happened since your last message.")
    show.add_argument("--since", metavar="TIME", help="Only the last stretch of time, like 30m, 2h or 1d.")
    show.add_argument("--format", choices=FORMATS, default=None, help="text (default), md, html or json.")
    show.add_argument("--output", "-o", metavar="PATH", help="Save to this file instead of printing. Use '-' for the screen.")
    show.add_argument("--open", action="store_true", help="Open the result in your web browser (html only).")
    show.add_argument("--max-items", type=int, default=None, metavar="N", help="Most commands to list (0 = all).")
    show.add_argument("--no-redact", action="store_true", help="Do not hide secrets. Only for your own screen.")
    show.add_argument("--fail-on", choices=("high", "medium", "tests"), help="Exit with code 4 when this is found (for scripts and CI).")
    show.add_argument("--width", type=int, default=0, metavar="N", help="Line width for text output.")

    ls = sub.add_parser("list", help="List recent sessions.", description="List recent sessions, newest first.")
    common(ls)
    ls.add_argument("--limit", "-l", type=int, default=10, metavar="N", help="How many to show. Default: 10.")

    ins = sub.add_parser(
        "install",
        help="Add a /receipt command inside your coding agents.",
        description="Add a /receipt command inside your coding agents so you can type it mid-session. "
        "With no name, every agent found on this computer is set up.",
    )
    ins.add_argument("agent", nargs="?", default="all", choices=["all"] + install_names(), help="Which agent (default: all that are found).")
    ins.add_argument("--dry-run", action="store_true", help="Show what would happen without changing anything.")
    ins.add_argument("--force", action="store_true", help="Replace or remove a file even if you edited it.")
    ins.add_argument("--uninstall", action="store_true", help="Remove the /receipt command again.")

    doc = sub.add_parser("doctor", help="Check your setup.", description="Check which agents are found and whether logs can be read.")
    doc.add_argument("--no-color", action="store_true", help="Turn off colours.")
    doc.add_argument("--dir", metavar="FOLDER", help=argparse.SUPPRESS)
    return p


# -- finding sessions -------------------------------------------------------------


def _project_filter(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return os.getcwd() if text == "." else text


def _peek_cwd(adapter: Adapter, path: Path) -> Optional[str]:
    try:
        return adapter.load(path).cwd
    except AdapterError:
        return None


def _matches(adapter: Adapter, path: Path, needle: Optional[str]) -> bool:
    if not needle:
        return True
    cwd = _peek_cwd(adapter, path) or ""
    flat = needle.replace("\\", "/").lower()
    return flat in cwd.replace("\\", "/").lower() or flat in str(path).replace("\\", "/").lower()


def _candidates(args: argparse.Namespace) -> List[Tuple[Adapter, Path]]:
    """All session files for the chosen agent(s), newest first."""
    root = Path(args.dir).expanduser() if getattr(args, "dir", None) else None
    chosen: List[Adapter]
    if getattr(args, "agent", None):
        found = adapters.get(args.agent)
        if found is None:
            raise AdapterError(f"I don't know an agent called '{args.agent}'. Choose one of: {', '.join(adapters.names())}.")
        chosen = [found]
    else:
        chosen = list(adapters.ALL)
    pairs: List[Tuple[Adapter, Path]] = []
    for adapter in chosen:
        for path in adapter.find(root):
            pairs.append((adapter, path))

    def mtime(item: Tuple[Adapter, Path]) -> float:
        try:
            return item[1].stat().st_mtime
        except OSError:
            return 0.0

    pairs.sort(key=mtime, reverse=True)
    return pairs


def _nothing_found_message(args: argparse.Namespace) -> str:
    lines = ["No coding-agent sessions were found."]
    if getattr(args, "agent", None):
        adapter = adapters.get(args.agent)
        if adapter:
            lines.append(f"I looked for {adapter.label} logs in {adapter.where}")
            reason = adapter.problem(Path(args.dir) if getattr(args, "dir", None) else None)
            if reason:
                lines.append(reason)
    else:
        lines.append("I looked in the usual places for: " + ", ".join(a.label for a in adapters.ALL) + ".")
        for adapter in adapters.ALL:
            reason = adapter.problem(None)
            if reason:
                lines.append(reason)
    lines.append("Run `agent-receipts doctor` to see exactly where I looked.")
    lines.append("If your logs are somewhere else, use --dir FOLDER or --file PATH.")
    return "\n".join(lines)


def _pick(args: argparse.Namespace) -> Tuple[Adapter, Path]:
    if args.file:
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise AdapterError(f"File not found: {path}")
        adapter = adapters.get(args.agent) if args.agent else adapters.sniff(path)
        if adapter is None:
            raise AdapterError("I could not tell which agent wrote that file. Add --agent " + "/".join(adapters.names()) + ".")
        return adapter, path

    pairs = _candidates(args)
    needle = _project_filter(args.project)
    if args.session:
        prefix = args.session.lower()
        hits = [(a, p) for a, p in pairs if p.stem.lower().startswith(prefix) or prefix in p.stem.lower()]
        if not hits:
            raise AdapterError(f"No session matches '{args.session}'. Try `agent-receipts list`.")
        if len(hits) > 1:
            names = ", ".join(p.stem[:20] for _, p in hits[:5])
            raise AdapterError(f"'{args.session}' matches several sessions ({names}). Type a few more characters.")
        return hits[0]
    if needle:
        pairs = [(a, p) for a, p in pairs if _matches(a, p, needle)]
    if not pairs:
        raise LookupError(_nothing_found_message(args))
    if args.nth < 1 or args.nth > len(pairs):
        raise AdapterError(f"You asked for session number {args.nth}, but only {len(pairs)} were found.")
    return pairs[args.nth - 1]


# -- commands ---------------------------------------------------------------------


def cmd_show(args: argparse.Namespace) -> int:
    since = None
    if args.since:
        since = parse_duration(args.since)
        if since is None or since <= 0:
            _err("--since needs a number and a unit, like 30m, 2h or 1d.")
            return EXIT_USAGE
    if args.turn and since is not None:
        _err("Use either --turn or --since, not both.")
        return EXIT_USAGE
    if args.max_items is not None and args.max_items < 0:
        _err("--max-items cannot be negative.")
        return EXIT_USAGE

    try:
        adapter, path = _pick(args)
        session: Session = adapter.load(path)
    except LookupError as exc:
        _err(str(exc))
        return EXIT_NOTHING
    except AdapterError as exc:
        _err(str(exc))
        return EXIT_ERROR

    receipt = build(session, redact_secrets=not args.no_redact, since_seconds=since, turn_only=args.turn)

    fmt = args.format
    if fmt is None:
        ext = (os.path.splitext(args.output)[1].lower() if args.output and args.output != "-" else "")
        fmt = {".html": "html", ".htm": "html", ".md": "md", ".json": "json"}.get(ext, "html" if args.open else "text")
    if args.open and fmt != "html":
        _err("--open only works with --format html.")
        return EXIT_USAGE

    to_screen = args.output == "-" or (not args.output and not args.open)
    stream = sys.stdout
    if fmt == "text":
        width = args.width or shutil.get_terminal_size((100, 24)).columns
        content = render_text(receipt, Style(_use_colour(args, stream) and to_screen), width, args.max_items)
    elif fmt == "md":
        content = render_markdown(receipt, args.max_items)
    elif fmt == "html":
        content = render_html(receipt, args.max_items)
    else:
        content = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"

    if to_screen:
        try:
            stream.write(content)
        except BrokenPipeError:  # for example `agent-receipts | head`
            try:
                sys.stdout = open(os.devnull, "w")
            except OSError:
                pass
    else:
        target: Path
        if args.output:
            target = Path(args.output).expanduser()
        else:
            handle, name = tempfile.mkstemp(prefix="agent-receipt-", suffix=".html")
            os.close(handle)
            target = Path(name)
        try:
            if target.parent and not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
            # Always UTF-8 with plain "\n" line endings, so a receipt reads the same on every computer.
            with open(str(target), "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except OSError as exc:
            _err(f"Could not save to {target}: {exc.strerror or exc}")
            return EXIT_ERROR
        print(f"Saved receipt to {target}", file=sys.stderr)
        if args.open:
            if not webbrowser.open(target.resolve().as_uri()):
                _err(f"Could not open a browser. Open this file yourself: {target}")

    return _fail_check(args, receipt)


def _fail_check(args: argparse.Namespace, receipt: dict) -> int:
    rule = getattr(args, "fail_on", None)
    if not rule:
        return EXIT_OK
    sm = receipt["summary"]
    if rule == "high" and sm["attention_high"]:
        return 4
    if rule == "medium" and (sm["attention_high"] or sm["attention_medium"]):
        return 4
    if rule == "tests" and receipt["verification"]["verdict"] in ("none", "fail", "pass-but-stale", "unknown"):
        return 4
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    try:
        pairs = _candidates(args)
        needle = _project_filter(args.project)
        if needle:
            pairs = [(a, p) for a, p in pairs if _matches(a, p, needle)]
    except AdapterError as exc:
        _err(str(exc))
        return EXIT_ERROR
    if not pairs:
        _err(_nothing_found_message(args))
        return EXIT_NOTHING
    limit = max(args.limit, 1)
    print(f"{'#':>2}  {'Agent':<12} {'Started':<17} {'Length':<8} {'Project':<28} Task")
    shown = 0
    for adapter, path in pairs:
        if shown >= limit:
            break
        try:
            s = adapter.load(path)
        except AdapterError:
            continue
        shown += 1
        length = human_duration((s.end - s.start).total_seconds()) if s.start and s.end else "-"
        project = squash(nice_path(s.cwd or "", None), 28)
        print(f"{shown:>2}  {adapter.label:<12} {local_stamp(iso(s.start)):<17} {length:<8} {project:<28} {squash(s.title, 50)}")
    if len(pairs) > shown:
        print(f"\n{len(pairs) - shown} older session(s) not shown. Use --limit to see more.")
    print("\nUse `agent-receipts --nth N` to get a receipt for one of these.")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    style = Style(_use_colour(args, sys.stdout))
    print(style("bold", f"Agent Receipts {__version__}  |  Python {sys.version.split()[0]}"))
    print()
    total = 0
    for adapter in adapters.ALL:
        root = Path(args.dir).expanduser() if args.dir else None
        files = adapter.find(root)
        total += len(files)
        reason = adapter.problem(root)
        if files:
            try:
                s = adapter.load(files[0])
                print(f"  {style('green', '[ok]')}    {adapter.label:<12} {len(files)} session(s). Newest read fine ({len(s.actions)} actions).")
            except AdapterError as exc:
                print(f"  {style('red', '[FAIL]')}  {adapter.label:<12} found {len(files)} session(s) but could not read the newest: {exc}")
        elif reason:
            print(f"  {style('yellow', '[warn]')}  {adapter.label:<12} {reason}")
        else:
            print(f"  {style('dim', '[none]')}  {adapter.label:<12} no sessions in {adapter.where}")
    print()
    if total:
        print("Everything needed is in place. Try:  agent-receipts")
        return EXIT_OK
    print("No sessions found yet. Use one of the agents above for a while, then run this again.")
    print("Logs somewhere unusual? Use --dir FOLDER or --file PATH when you run a receipt.")
    return EXIT_NOTHING


def main(argv: Optional[List[str]] = None) -> int:
    _fix_windows_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"show", "list", "install", "doctor", "-h", "--help", "--version"}
    if not argv or argv[0] not in known:
        argv = ["show"] + argv
    p = parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        if args.command == "list":
            return cmd_list(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "install":
            return installer.run(args.agent, args.dry_run, args.force, args.uninstall)
        return cmd_show(args)
    except KeyboardInterrupt:
        _err("Stopped.")
        return 130
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # last resort: never show a raw traceback to a normal user
        if os.environ.get("AGENT_RECEIPTS_DEBUG"):
            raise
        _err(f"Something unexpected went wrong ({type(exc).__name__}: {exc}). "
             f"Run again with AGENT_RECEIPTS_DEBUG=1 to see details, and please report it.")
        return EXIT_ERROR
