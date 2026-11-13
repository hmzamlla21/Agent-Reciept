"""The one shape every agent's log is turned into."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# What an action did, in plain groups.
COMMAND = "command"
READ = "read"
WRITE = "write"
EDIT = "edit"
DELETE = "delete"
SEARCH = "search"
WEB = "web"
SUBAGENT = "subagent"
OTHER = "other"

HIGH = "high"
MEDIUM = "medium"
INFO = "info"
LEVEL_ORDER = {HIGH: 0, MEDIUM: 1, INFO: 2}


@dataclass
class Risk:
    level: str
    code: str
    message: str


@dataclass
class Action:
    """One thing the agent did (ran a command, read a file, edited a file...)."""

    tool: str
    kind: str
    target: str = ""
    ts: Optional[datetime] = None
    ok: Optional[bool] = None  # None means the log does not say
    detail: str = ""  # short error text when something failed
    call_id: str = ""
    risks: List[Risk] = field(default_factory=list)


@dataclass
class Session:
    agent_id: str
    agent_label: str
    session_id: str
    source: Path
    cwd: Optional[str] = None
    title: str = ""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    prompt_times: List[Optional[datetime]] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    last_message: str = ""
    tokens: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def note_time(self, ts: Optional[datetime]) -> None:
        if ts is None:
            return
        if self.start is None or ts < self.start:
            self.start = ts
        if self.end is None or ts > self.end:
            self.end = ts
