"""Hide secrets before anything is printed or saved.

A receipt is meant to be shared with a teammate or pasted into a ticket, so it must
never carry a key the agent happened to see. This is best effort: it catches the
common token shapes, not every possible secret. Use --no-redact only on your own screen.
"""

from __future__ import annotations

import re

MASK = "[hidden]"

_PATTERNS = [
    # Private key blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "[hidden private key]"),
    # Well-known token shapes
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[hidden aws key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[hidden github token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[hidden github token]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{12,}\b"), "[hidden gitlab token]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}\b"), "[hidden api key]"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"), "[hidden api key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "[hidden google key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "[hidden slack token]"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), "[hidden stripe key]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), "[hidden npm token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "[hidden jwt]"),
    # Passwords inside URLs: https://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:)[^\s/@]+(@)", re.I), r"\1" + MASK + r"\2"),
    # Authorization headers
    (re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic|token)\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1" + MASK),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=\-]{16,}"), r"\1" + MASK),
    # NAME=value where NAME looks secret
    (
        re.compile(
            r"(?i)(\b[A-Za-z0-9_.\-]*(?:password|passwd|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|credential)[A-Za-z0-9_.\-]*"
            r"\s*[=:]\s*)(?!\[hidden)(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s\"';&|]{3,})"
        ),
        r"\1" + MASK,
    ),
    # Command-line flags such as --token abc123 or --password=abc123
    (
        re.compile(r"(?i)(--(?:password|passwd|token|secret|api[_\-]?key|auth)[=\s]+)(\"[^\"\n]+\"|'[^'\n]+'|[^\s\"';&|]+)"),
        r"\1" + MASK,
    ),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
