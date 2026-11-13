import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

LOGS = ROOT / "examples" / "logs"
DIRS = {
    "claude-code": LOGS / "claude-code",
    "codex": LOGS / "codex",
    "gemini": LOGS / "gemini" / "tmp",
    "opencode": LOGS / "opencode" / "storage",
}


def run_cli(*argv):
    """Run the command line in-process. Returns (exit code, stdout, stderr)."""
    from agent_receipts.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def load_example(agent_id):
    from agent_receipts import adapters

    adapter = adapters.get(agent_id)
    files = adapter.find(DIRS[agent_id])
    assert files, f"no example log for {agent_id}"
    return adapter.load(files[0])
