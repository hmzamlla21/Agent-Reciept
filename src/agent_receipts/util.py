"""Small helpers shared by every part of the tool. Standard library only."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


class AdapterError(Exception):
    """A session file could not be read. The message is safe to show to the user."""


_FRACTION = re.compile(r"\.(\d+)")


def parse_ts(value: Any) -> Optional[datetime]:
    """Turn an ISO string or a Unix time (seconds or milliseconds) into an aware datetime."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 1e11:  # milliseconds
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
        text = str(value).strip()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            return parse_ts(float(text))
        if text[-1:] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        # Python 3.9 and 3.10 only accept 3 or 6 fraction digits.
        text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, OverflowError, OSError):
        return None


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def local_clock(value: Optional[str]) -> str:
    """HH:MM:SS in the user's own time zone, from an ISO string."""
    dt = parse_ts(value)
    return dt.astimezone().strftime("%H:%M:%S") if dt else ""


def local_stamp(value: Optional[str]) -> str:
    dt = parse_ts(value)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "unknown"


def human_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def parse_duration(text: str) -> Optional[float]:
    """'90s', '30m', '2h', '1d' -> seconds. Returns None when it does not look like one."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*", text or "", re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]


def squash(text: Any, limit: int = 160) -> str:
    """One line, no runs of whitespace, cut to `limit` characters."""
    if text is None:
        return ""
    flat = re.sub(r"\s+", " ", str(text)).strip()
    if len(flat) > limit:
        return flat[: max(limit - 1, 1)].rstrip() + "…"
    return flat


def home_dir() -> Path:
    return Path(os.path.expanduser("~"))


def nice_path(path: str, cwd: Optional[str] = None) -> str:
    """Show a path relative to the project when possible, or with ~ for the home folder."""
    if not path:
        return ""
    norm = path.replace("\\", "/")
    if cwd:
        base = cwd.replace("\\", "/").rstrip("/")
        if base and norm.startswith(base + "/"):
            return norm[len(base) + 1:]
        if norm == base:
            return "."
    home = str(home_dir()).replace("\\", "/").rstrip("/")
    if home and norm.startswith(home + "/"):
        return "~/" + norm[len(home) + 1:]
    return path


def is_inside(path: str, folder: str) -> bool:
    a = path.replace("\\", "/").rstrip("/")
    b = folder.replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:/", a) and re.match(r"^[A-Za-z]:/", b):
        a, b = a.lower(), b.lower()
    return a == b or a.startswith(b + "/")


def iter_jsonl(path: Path, stats: Dict[str, int]) -> Iterator[Dict[str, Any]]:
    """Yield each JSON object in a .jsonl file. Broken lines are counted, not fatal."""
    name = str(path)
    if name.endswith(".zst"):
        try:
            done = subprocess.run(
                ["zstd", "-d", "-c", name], capture_output=True, check=True, timeout=120
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdapterError(
                f"{path.name} is compressed with zstd. Install the 'zstd' command "
                f"(for example: brew install zstd, or apt install zstd) and try again."
            ) from exc
        lines: Any = done.stdout.decode("utf-8", "replace").splitlines()
        yield from _decode_lines(lines, stats)
        return
    try:
        with open(name, "r", encoding="utf-8", errors="replace") as handle:
            yield from _decode_lines(handle, stats)
    except OSError as exc:
        raise AdapterError(f"Could not read {path}: {exc.strerror or exc}") from exc


def _decode_lines(lines: Any, stats: Dict[str, int]) -> Iterator[Dict[str, Any]]:
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            stats["bad"] = stats.get("bad", 0) + 1
            continue
        if isinstance(obj, dict):
            yield obj


def read_json(path: Path) -> Any:
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except OSError as exc:
        raise AdapterError(f"Could not read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise AdapterError(f"{path.name} is not valid JSON ({exc}).") from exc


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def text_of(content: Any) -> str:
    """Pull plain text out of a message 'content' field of unknown shape."""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
    elif isinstance(content, dict):
        value = content.get("text")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)
