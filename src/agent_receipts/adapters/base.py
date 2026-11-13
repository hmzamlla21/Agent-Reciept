"""The shape every agent reader follows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..model import Session
from ..risk import assess
from ..util import home_dir, squash


class Adapter:
    id = ""
    label = ""
    aliases: tuple = ()
    # Plain-words hint shown by `doctor` and when no sessions are found.
    where = ""

    def default_roots(self) -> List[Path]:
        raise NotImplementedError

    def find(self, root: Optional[Path] = None) -> List[Path]:
        """Session files, newest first."""
        raise NotImplementedError

    def load(self, path: Path) -> Session:
        raise NotImplementedError

    def problem(self, root: Optional[Path] = None) -> Optional[str]:
        """Return a short reason when this agent's logs exist but cannot be read."""
        return None

    # -- helpers -----------------------------------------------------------------

    def roots(self, override: Optional[Path]) -> List[Path]:
        return [override.expanduser()] if override else self.default_roots()

    def new_session(self, path: Path, session_id: str = "") -> Session:
        return Session(
            agent_id=self.id,
            agent_label=self.label,
            session_id=session_id or path.stem,
            source=path,
        )

    @staticmethod
    def finish(session: Session, stats: Optional[Dict[str, int]] = None) -> Session:
        for action in session.actions:
            assess(action, session.cwd)
        if stats and stats.get("bad"):
            count = stats["bad"]
            session.warnings.append(
                f"{count} line{'s' if count != 1 else ''} in the log could not be read and "
                f"{'were' if count != 1 else 'was'} skipped."
            )
        if not session.title and session.prompt_times:
            session.title = "(untitled)"
        return session


def newest_first(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    unique = []
    for p in paths:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(unique, key=mtime, reverse=True)


def env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def first_text(mapping: Dict[str, Any], keys: Iterable[str]) -> str:
    """First non-empty string value among `keys`."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def summarize_args(args: Dict[str, Any]) -> str:
    """A short description of tool arguments we have no special rule for."""
    for value in args.values():
        if isinstance(value, str) and value:
            return squash(value, 120)
    return ""


__all__ = [
    "Adapter",
    "newest_first",
    "env_path",
    "first_text",
    "summarize_args",
    "home_dir",
]
