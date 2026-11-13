"""Decide which actions deserve a human look.

Rules read the command the way a shell would (split into parts, strip `sudo`, look inside
`bash -c "..."`), so `echo "rm -rf /"` is not flagged but `sudo rm -rf build` is.
These are warnings to help a person review quickly. They are not a security sandbox.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from typing import List, Optional, Tuple

from .model import (
    COMMAND,
    DELETE,
    EDIT,
    HIGH,
    INFO,
    MEDIUM,
    READ,
    WRITE,
    Action,
    Risk,
)
from .util import is_inside, nice_path

# ---------------------------------------------------------------------------
# Reading a command line
# ---------------------------------------------------------------------------


def split_segments(command: str) -> List[str]:
    """Split on unquoted && || ; | & and newlines."""
    segments: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            current.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == "\\" and i + 1 < len(command):
            current.append(ch)
            current.append(command[i + 1])
            i += 1
        elif ch in ";\n":
            segments.append("".join(current))
            current = []
        elif ch in "&|":
            segments.append("".join(current))
            current = []
            if i + 1 < len(command) and command[i + 1] == ch:
                i += 1
        else:
            current.append(ch)
        i += 1
    segments.append("".join(current))
    return [s.strip() for s in segments if s.strip()]


def tokenize(segment: str) -> List[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"env", "command", "nohup", "time", "nice", "exec", "xargs", "builtin", "stdbuf"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}


def _risk(risks: List[Risk], level: str, code: str, message: str) -> None:
    if not any(r.code == code and r.message == message for r in risks):
        risks.append(Risk(level, code, message))


def _strip_wrappers(tokens: List[str], risks: List[Risk]) -> List[str]:
    tokens = list(tokens)
    while tokens:
        head = tokens[0]
        base = os.path.basename(head)
        if _ENV_ASSIGN.match(head):
            tokens.pop(0)
        elif base in ("sudo", "doas"):
            _risk(risks, HIGH, "sudo", "Ran a command with admin rights (sudo)")
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                flag = tokens.pop(0)
                if flag in ("-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T") and tokens:
                    tokens.pop(0)
        elif base in _WRAPPERS:
            tokens.pop(0)
            while tokens and (tokens[0].startswith("-") or _ENV_ASSIGN.match(tokens[0])):
                tokens.pop(0)
        else:
            break
    return tokens


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SAFE_ENV_SUFFIX = (".example", ".sample", ".template", ".dist", ".defaults", ".tpl")
_SECRET_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".netrc", ".pgpass",
    "credentials.json", "service-account.json", "secrets.json", "secrets.yml",
    "secrets.yaml", "kubeconfig",
}
_SECRET_SUFFIX = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
_STARTUP_FILES = {
    ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile", ".zshenv",
    ".bash_aliases", ".gitconfig",
}


def secret_path(path: str) -> bool:
    norm = path.replace("\\", "/").lower().strip("'\"")
    base = norm.rsplit("/", 1)[-1]
    if base == ".env" or (base.startswith(".env.") and not base.endswith(_SAFE_ENV_SUFFIX)):
        return True
    if base in _SECRET_NAMES or base.endswith(_SECRET_SUFFIX):
        return True
    if "/.aws/credentials" in norm or norm.endswith(".aws/credentials"):
        return True
    return any(
        marker in norm
        for marker in ("/.ssh/", "/.gnupg/", "/.kube/config", "/.docker/config.json")
    )


def _tmp_roots() -> List[str]:
    roots = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders", "/var/tmp"]
    try:
        roots.append(tempfile.gettempdir())
    except Exception:  # pragma: no cover - defensive
        pass
    return roots


def _agent_data_dirs() -> List[str]:
    home = os.path.expanduser("~").replace("\\", "/").rstrip("/")
    return [f"{home}/{name}" for name in (".claude", ".codex", ".gemini", ".local/share/opencode", ".cache")]


def _is_absolute(path: str) -> bool:
    return path.startswith(("/", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", path))


def check_path(path: str, cwd: Optional[str], kind: str) -> List[Risk]:
    risks: List[Risk] = []
    if not path:
        return risks
    expanded = os.path.expanduser(path) if path.startswith("~") else path
    norm = expanded.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    changes = kind in (WRITE, EDIT, DELETE)
    shown = nice_path(expanded, cwd)

    if secret_path(norm):
        if changes:
            _risk(risks, HIGH, "secret-write", f"Changed a file that may hold secrets ({shown})")
        else:
            _risk(risks, MEDIUM, "secret-read", f"Opened a file that may hold secrets ({shown})")
    if not changes:
        return risks

    if "/.git/hooks/" in norm:
        _risk(risks, HIGH, "git-hook", f"Changed a git hook (it runs on its own later): {shown}")
    elif norm.endswith("/.git/config"):
        _risk(risks, MEDIUM, "git-config", "Changed the repository's git settings")
    if base in _STARTUP_FILES or norm.startswith("/etc/"):
        _risk(risks, HIGH, "system-config", f"Changed a shell or system settings file: {shown}")
    if any(m in norm for m in ("/.claude/settings", "/.codex/config", "/.gemini/settings")) or base == "opencode.json":
        _risk(risks, MEDIUM, "agent-settings", f"Changed the agent's own settings: {shown}")
        return risks
    if base in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        _risk(risks, INFO, "agent-instructions", f"Changed the agent instructions file: {shown}")
    if "/.github/workflows/" in "/" + norm.lstrip("/") or base in (".gitlab-ci.yml", "Jenkinsfile") or "/.circleci/" in norm:
        _risk(risks, MEDIUM, "ci-change", f"Changed a CI/CD pipeline file: {shown}")

    if cwd and _is_absolute(path) and not is_inside(norm, cwd.replace("\\", "/")):
        if not any(is_inside(norm, root) for root in _tmp_roots() + _agent_data_dirs()):
            _risk(risks, MEDIUM, "outside-project", f"Changed a file outside the project: {shown}")
    return risks


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

_PIPE_TO_SHELL = re.compile(r"\b(?:curl|wget|fetch)\b[^|;&\n]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b")
_SQL_DROP = re.compile(r"\b(?:drop\s+(?:table|database|schema)|truncate\s+table)\b", re.I)
_SQL_DELETE_ALL = re.compile(r"\bdelete\s+from\s+[\w.\"`]+(?!.*\bwhere\b)", re.I)
_SECRET_ECHO = re.compile(r"\$\{?[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*", re.I)
_DANGEROUS_RM_TARGETS = {"/", "/*", "~", "~/", "$home", "${home}", "*", ".", "..", "./", "../", "/home", "/usr", "/etc", "/var"}

_TEST_RUNNERS = {"pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "rspec", "phpunit", "ctest", "bats", "unittest"}
_RUN_LAUNCHERS = {"uv", "poetry", "pipenv", "pdm", "hatch", "rye"}
_NPX_LIKE = {"npx", "pnpx", "bunx"}
_SUBCOMMAND_TEST = {"go": {"test"}, "cargo": {"test", "nextest"}, "dotnet": {"test"}, "deno": {"test"},
                    "swift": {"test"}, "flutter": {"test"}, "mix": {"test"}, "zig": {"test"}}
_TASK_RUNNERS = {"make", "just", "task", "rake", "gradle", "gradlew", "mvn", "mvnw", "ant"}


def _launcher_stripped(tokens: List[str]) -> List[str]:
    """Look through `uv run`, `npx`, `python -m` and similar so the real program is first."""
    for _ in range(6):
        if not tokens:
            break
        base = os.path.basename(tokens[0])
        if base in _RUN_LAUNCHERS and tokens[1:2] == ["run"]:
            tokens = tokens[2:]
        elif base in _NPX_LIKE or (base in ("pnpm", "yarn", "npm", "bun") and tokens[1:2] in (["exec"], ["dlx"], ["x"])):
            tokens = tokens[1:] if base in _NPX_LIKE else tokens[2:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
        elif re.fullmatch(r"python[\d.]*", base) and tokens[1:2] == ["-m"] and len(tokens) > 2:
            tokens = tokens[2:]
        elif base == "coverage" and tokens[1:2] == ["run"]:
            tokens = tokens[2:]
            if tokens[:1] == ["-m"]:
                tokens = tokens[1:]
        else:
            break
    return tokens


def _segment_runs_tests(tokens: List[str], depth: int) -> bool:
    tokens = _launcher_stripped(_strip_wrappers(tokens, []))
    if not tokens:
        return False
    prog = os.path.basename(tokens[0])
    rest = tokens[1:]
    plain = [t for t in rest if not t.startswith("-")]
    sub = plain[0] if plain else ""

    if prog in _SHELLS and depth < 3:
        for idx, item in enumerate(rest):
            if item.startswith("-") and not item.startswith("--") and item.endswith("c") and idx + 1 < len(rest):
                return is_test_command(rest[idx + 1], depth + 1)
        return False
    if prog in _TEST_RUNNERS:
        return True
    if prog == "playwright":
        return sub == "test"
    if prog == "cypress":
        return sub == "run"
    if prog in _SUBCOMMAND_TEST:
        return sub in _SUBCOMMAND_TEST[prog]
    if prog in ("npm", "pnpm", "yarn", "bun"):
        if sub in ("test", "t", "tst"):
            return True
        if sub.startswith("test:"):
            return True
        return sub == "run" and len(plain) > 1 and (plain[1] == "test" or plain[1].startswith("test:"))
    if prog in _TASK_RUNNERS:
        return any(a in ("test", "tests", "check", "verify") or a.startswith("test:") for a in plain)
    return False


def is_test_command(command: str, depth: int = 0) -> bool:
    """True when a shell command really runs a test suite (not just mentions one)."""
    return any(_segment_runs_tests(tokenize(seg), depth) for seg in split_segments(command or ""))


def _git_subcommand(args: List[str]) -> Tuple[str, List[str]]:
    i = 0
    while i < len(args):
        item = args[i]
        if item in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
        elif item.startswith("-"):
            i += 1
        else:
            return item, args[i + 1:]
    return "", []


def _analyze_segment(tokens: List[str], risks: List[Risk], depth: int) -> None:
    tokens = _strip_wrappers(tokens, risks)
    if not tokens:
        return
    prog = os.path.basename(tokens[0])
    args = tokens[1:]
    flags = [a for a in args if a.startswith("-")]
    plain = [a for a in args if not a.startswith("-")]

    if prog in _SHELLS and depth < 3:
        for idx, item in enumerate(args):
            if item.startswith("-") and not item.startswith("--") and item.endswith("c") and idx + 1 < len(args):
                for seg in split_segments(args[idx + 1]):
                    _analyze_segment(tokenize(seg), risks, depth + 1)
                break
        return

    if prog == "rm":
        recursive = any(f in ("-r", "-R", "--recursive") or (not f.startswith("--") and ("r" in f or "R" in f)) for f in flags)
        shown = " ".join(plain[:3]) + (" ..." if len(plain) > 3 else "")
        if any(p.lower() in _DANGEROUS_RM_TARGETS for p in plain) and recursive:
            _risk(risks, HIGH, "rm-dangerous", f"Deleted a very broad target with rm -r: {shown}")
        elif recursive:
            _risk(risks, HIGH, "rm-recursive", f"Deleted folders and everything inside them: {shown}")
        else:
            _risk(risks, MEDIUM, "rm", f"Deleted files: {shown}")
    elif prog in ("rmdir", "unlink", "shred", "trash"):
        _risk(risks, MEDIUM, "delete", f"Deleted files or folders ({prog})")
    elif prog == "find" and (
        "-delete" in args or ("-exec" in args and any(os.path.basename(a) == "rm" for a in args))
    ):
        _risk(risks, HIGH, "find-delete", "Deleted files found by a search (find)")
    elif prog == "git":
        _analyze_git(args, flags, risks)
    elif prog in ("curl", "wget", "http", "https", "fetch"):
        urls = [a for a in plain if re.match(r"^[a-z]+://", a, re.I)]
        _risk(risks, INFO, "network", f"Made a network request: {urls[0] if urls else prog}")
    elif prog in ("chmod", "chown", "chgrp"):
        if any(a in ("777", "a+rwx", "ugo+rwx", "o+w", "666") for a in plain) and prog == "chmod":
            _risk(risks, HIGH, "chmod-open", "Opened file permissions to everyone (chmod)")
        elif any(f in ("-R", "-r", "--recursive") for f in flags):
            _risk(risks, MEDIUM, "chmod-recursive", f"Changed owner or permissions on a whole folder ({prog} -R)")
    elif prog == "dd" and any(a.startswith("of=/dev/") for a in args):
        _risk(risks, HIGH, "disk-write", "Wrote straight to a disk device (dd)")
    elif prog.startswith("mkfs") or prog in ("fdisk", "parted", "wipefs"):
        _risk(risks, HIGH, "disk-format", f"Used a disk formatting tool ({prog})")
    elif prog in ("kill", "pkill", "killall"):
        _risk(risks, MEDIUM, "kill", f"Stopped running processes ({prog})")
    elif prog in ("npm", "pnpm", "yarn", "bun"):
        _analyze_node(prog, args, risks)
    elif prog in ("pip", "pip3", "pipx", "poetry", "uv", "conda", "mamba") or re.fullmatch(r"python[\d.]*", prog):
        _analyze_python(prog, args, risks)
    elif prog in ("cargo", "go", "gem", "brew", "apt", "apt-get", "dnf", "yum", "pacman", "apk", "choco", "winget", "scoop", "composer"):
        _analyze_other_install(prog, args, risks)
    elif prog in ("twine",) and "upload" in args:
        _risk(risks, HIGH, "publish", "Published a package (twine upload)")
    elif prog == "docker" and plain[:1] == ["push"]:
        _risk(risks, HIGH, "publish", "Pushed a container image (docker push)")
    elif prog == "docker" and plain[:2] in (["system", "prune"], ["volume", "prune"], ["volume", "rm"]):
        _risk(risks, MEDIUM, "docker-prune", "Deleted Docker data")
    elif prog in ("terraform", "tofu", "pulumi") and plain[:1] in (["apply"], ["destroy"], ["up"]):
        _risk(risks, HIGH, "infra", f"Changed real infrastructure ({prog} {plain[0]})")
    elif prog == "kubectl" and plain[:1] in (["delete"], ["apply"], ["replace"], ["scale"]):
        _risk(risks, HIGH, "infra", f"Changed a Kubernetes cluster (kubectl {plain[0]})")
    elif prog == "gh" and plain[:2] in (["release", "create"], ["repo", "delete"], ["repo", "create"], ["pr", "merge"]):
        level = HIGH if plain[:2] in (["release", "create"], ["repo", "delete"]) else MEDIUM
        _risk(risks, level, "github", f"Changed something on GitHub (gh {plain[0]} {plain[1]})")
    elif prog in ("vercel", "netlify", "fly", "flyctl", "firebase", "wrangler", "heroku", "serverless", "sls") and any(a in ("deploy", "--prod", "publish") for a in args):
        _risk(risks, HIGH, "deploy", f"Deployed to a live service ({prog})")
    elif prog in ("printenv",) or (prog == "env" and not args):
        _risk(risks, MEDIUM, "env-dump", "Printed environment variables (they can hold secrets)")
    elif prog == "echo" and any(_SECRET_ECHO.search(a) for a in args):
        _risk(risks, MEDIUM, "env-dump", "Printed a secret-looking environment variable")
    elif prog in ("sed", "perl") and any(f.startswith("-i") or (f.startswith("-") and "i" in f and prog == "perl") for f in flags):
        _risk(risks, INFO, "shell-edit", "Edited files from the shell (not listed under files changed)")

    if "--no-verify" in args and prog != "git":
        _risk(risks, MEDIUM, "no-verify", "Skipped safety checks (--no-verify)")

    for token in args:
        cleaned = token.lstrip("<>")
        if cleaned and secret_path(cleaned) and prog not in ("git",):
            _risk(risks, MEDIUM, "secret-touch", f"Touched a file that may hold secrets ({cleaned})")


def _analyze_git(args: List[str], flags: List[str], risks: List[Risk]) -> None:
    sub, rest = _git_subcommand(args)
    rest_flags = [a for a in rest if a.startswith("-")]
    force_flag = any(
        f in ("--force", "-f", "--force-with-lease", "--force-if-includes") or f.startswith("--force-with-lease=")
        for f in rest_flags
    )
    plus_refspec = any(a.startswith("+") for a in rest if not a.startswith("-"))
    force = force_flag or plus_refspec
    if sub == "push":
        if force:
            _risk(risks, HIGH, "git-force-push", "Force-pushed to a remote (can overwrite other people's work)")
        elif "--delete" in rest_flags or any(a.startswith(":") for a in rest):
            _risk(risks, HIGH, "git-delete-remote", "Deleted a branch or tag on the remote")
        else:
            _risk(risks, MEDIUM, "git-push", "Pushed code to a remote")
    elif sub == "reset" and "--hard" in rest_flags:
        _risk(risks, HIGH, "git-reset-hard", "Hard reset: threw away uncommitted work")
    elif sub == "clean" and any(f for f in rest_flags if f == "--force" or (not f.startswith("--") and "f" in f)):
        _risk(risks, HIGH, "git-clean", "Deleted untracked files (git clean)")
    elif sub in ("checkout", "restore", "switch") and (
        "--" in rest or "." in rest or any(f in ("-f", "--force", "--discard-changes") for f in rest_flags)
    ) and not ("--staged" in rest_flags and sub == "restore"):
        _risk(risks, MEDIUM, "git-discard", "May have thrown away local changes")
    elif sub == "commit":
        if "--no-verify" in rest_flags or "-n" in rest_flags:
            _risk(risks, MEDIUM, "no-verify", "Committed while skipping git hooks (--no-verify)")
        elif "--amend" in rest_flags:
            _risk(risks, MEDIUM, "git-amend", "Rewrote the previous commit (--amend)")
        else:
            _risk(risks, INFO, "git-commit", "Created a commit")
    elif sub in ("rebase", "filter-branch", "filter-repo", "replace"):
        _risk(risks, MEDIUM, "git-rewrite", f"Rewrote git history (git {sub})")
    elif sub == "branch" and any(f in ("-D", "--delete", "-d") for f in rest_flags):
        _risk(risks, MEDIUM, "git-branch-delete", "Deleted a branch")
    elif sub == "stash" and rest[:1] in (["drop"], ["clear"]):
        _risk(risks, MEDIUM, "git-stash-drop", "Deleted saved stashes")
    elif sub == "config" and ("--global" in rest_flags or "--system" in rest_flags):
        values = [a for a in rest if not a.startswith("-")]
        reading = any(f in ("--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin") for f in rest_flags)
        writing = len(values) >= 2 or any(
            f in ("--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e") for f in rest_flags
        )
        if writing and not reading:
            _risk(risks, MEDIUM, "git-config-global", "Changed global git settings")
    elif sub in ("merge", "pull", "cherry-pick", "revert", "tag"):
        _risk(risks, INFO, f"git-{sub}", f"Ran git {sub}")


def _analyze_node(prog: str, args: List[str], risks: List[Risk]) -> None:
    sub = next((a for a in args if not a.startswith("-")), "")
    if sub in ("install", "i", "add", "ci", "update", "upgrade", "up"):
        _risk(risks, MEDIUM, "install", f"Installed or updated packages ({prog})")
    elif sub == "publish":
        _risk(risks, HIGH, "publish", f"Published a package ({prog} publish)")
    elif sub in ("uninstall", "remove", "rm", "un"):
        _risk(risks, INFO, "uninstall", f"Removed packages ({prog})")
    elif sub in ("exec", "dlx", "x"):
        _risk(risks, MEDIUM, "run-remote", f"Downloaded and ran a package ({prog} {sub})")


def _analyze_python(prog: str, args: List[str], risks: List[Risk]) -> None:
    plain = [a for a in args if not a.startswith("-")]
    if re.fullmatch(r"python[\d.]*", prog):
        if args[:2] == ["-m", "pip"] and "install" in args[2:]:
            _risk(risks, MEDIUM, "install", "Installed Python packages (pip)")
        elif args[:2] == ["-m", "twine"] and "upload" in args:
            _risk(risks, HIGH, "publish", "Published a package (twine upload)")
        return
    if prog in ("pip", "pip3", "pipx") and plain[:1] == ["install"]:
        _risk(risks, MEDIUM, "install", f"Installed Python packages ({prog})")
    elif prog == "poetry" and plain[:1] in (["add"], ["install"], ["update"]):
        _risk(risks, MEDIUM, "install", "Installed Python packages (poetry)")
    elif prog == "poetry" and plain[:1] == ["publish"]:
        _risk(risks, HIGH, "publish", "Published a package (poetry publish)")
    elif prog == "uv" and (plain[:1] in (["add"], ["sync"]) or plain[:2] in (["pip", "install"], ["tool", "install"])):
        _risk(risks, MEDIUM, "install", "Installed Python packages (uv)")
    elif prog == "uv" and plain[:1] == ["publish"]:
        _risk(risks, HIGH, "publish", "Published a package (uv publish)")
    elif prog in ("conda", "mamba") and plain[:1] in (["install"], ["create"], ["update"]):
        _risk(risks, MEDIUM, "install", f"Installed packages ({prog})")


def _analyze_other_install(prog: str, args: List[str], risks: List[Risk]) -> None:
    plain = [a for a in args if not a.startswith("-")]
    first = plain[:1]
    if prog == "cargo" and first in (["add"], ["install"]):
        _risk(risks, MEDIUM, "install", "Installed Rust packages (cargo)")
    elif prog == "cargo" and first == ["publish"]:
        _risk(risks, HIGH, "publish", "Published a package (cargo publish)")
    elif prog == "go" and first in (["get"], ["install"]):
        _risk(risks, MEDIUM, "install", "Installed Go packages")
    elif prog in ("gem", "composer") and first in (["install"], ["require"]):
        _risk(risks, MEDIUM, "install", f"Installed packages ({prog})")
    elif prog in ("brew", "apt", "apt-get", "dnf", "yum", "apk", "choco", "winget", "scoop") and first in (["install"], ["add"], ["upgrade"], ["remove"], ["uninstall"]):
        _risk(risks, MEDIUM, "system-install", f"Changed software installed on this computer ({prog})")
    elif prog == "pacman" and any(f.startswith("-S") or f.startswith("-R") for f in args if f.startswith("-")):
        _risk(risks, MEDIUM, "system-install", "Changed software installed on this computer (pacman)")


def check_command(command: str, depth: int = 0) -> List[Risk]:
    risks: List[Risk] = []
    if not command:
        return risks
    if _PIPE_TO_SHELL.search(command):
        _risk(risks, HIGH, "pipe-to-shell", "Downloaded a script and ran it straight away (curl | sh)")
    if _SQL_DROP.search(command):
        _risk(risks, HIGH, "sql-drop", "Ran SQL that deletes tables or databases")
    elif _SQL_DELETE_ALL.search(command):
        _risk(risks, MEDIUM, "sql-delete", "Ran SQL that may delete every row in a table")
    for segment in split_segments(command):
        _analyze_segment(tokenize(segment), risks, depth)
    return risks


def assess(action: Action, cwd: Optional[str]) -> None:
    """Fill action.risks in place."""
    risks: List[Risk] = []
    if action.kind == COMMAND:
        risks.extend(check_command(action.target))
    elif action.kind in (READ, WRITE, EDIT, DELETE):
        risks.extend(check_path(action.target, cwd, action.kind))
    action.risks = risks
