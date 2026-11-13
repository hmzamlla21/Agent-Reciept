import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers


def base(agent="claude-code"):
    return ["--agent", agent, "--dir", str(helpers.DIRS[agent]), "--no-color"]


class Cli(unittest.TestCase):
    def test_default_text_receipt(self):
        code, out, err = helpers.run_cli(*base())
        self.assertEqual(code, 0, err)
        for heading in ("AGENT RECEIPT", "WHAT HAPPENED", "NEEDS YOUR ATTENTION", "TESTS", "FILES CHANGED", "COMMANDS"):
            self.assertIn(heading, out)

    def test_every_format_for_every_agent(self):
        for agent in ("claude-code", "codex", "gemini", "opencode"):
            for fmt in ("text", "md", "html", "json"):
                with self.subTest(agent=agent, fmt=fmt):
                    code, out, err = helpers.run_cli(*base(agent), "--format", fmt, "-o", "-")
                    self.assertEqual(code, 0, err)
                    self.assertTrue(out.strip())
                    if fmt == "json":
                        self.assertEqual(json.loads(out)["schema"], "agent-receipt/1")
                    if fmt == "html":
                        self.assertTrue(out.startswith("<!doctype html>"))
                        self.assertNotIn("<script", out.lower())

    def test_html_escapes_untrusted_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "p" / "s.jsonl"
            log.parent.mkdir()
            line = {"type": "user", "uuid": "1", "sessionId": "s", "cwd": "/p", "timestamp": "2026-11-12T09:00:00Z",
                    "message": {"role": "user", "content": "<img src=x onerror=alert(1)> <script>boom()</script>"}}
            log.write_text(json.dumps(line) + "\n")
            code, out, _ = helpers.run_cli("--agent", "claude-code", "--dir", tmp, "--format", "html", "-o", "-")
            self.assertEqual(code, 0)
            self.assertNotIn("<script>boom", out)
            self.assertNotIn("<img src=x", out)
            self.assertIn("&lt;img", out)

    def test_saves_file_and_infers_format_from_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new folder" / "receipt.md"
            code, out, err = helpers.run_cli(*base(), "-o", str(target))
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            self.assertTrue(target.read_text().startswith("# Agent Receipt"))

    def test_json_fields(self):
        _, out, _ = helpers.run_cli(*base("codex"), "--format", "json", "-o", "-")
        data = json.loads(out)
        for key in ("agent", "session", "summary", "attention", "verification", "files", "commands", "failures"):
            self.assertIn(key, data)
        self.assertTrue(data["redacted"])

    def test_max_items(self):
        _, out, _ = helpers.run_cli(*base(), "--max-items", "2")
        self.assertIn("and 7 more", out)
        _, out, _ = helpers.run_cli(*base(), "--max-items", "0")
        self.assertNotIn("more (use", out)

    def test_fail_on(self):
        self.assertEqual(helpers.run_cli(*base(), "--fail-on", "high")[0], 4)
        self.assertEqual(helpers.run_cli(*base(), "--fail-on", "tests")[0], 4)
        self.assertEqual(helpers.run_cli(*base(), "--since", "1m", "--fail-on", "high")[0], 0)

    def test_usage_errors_exit_2_with_plain_message(self):
        code, _, err = helpers.run_cli(*base(), "--since", "soon")
        self.assertEqual(code, 2)
        self.assertIn("like 30m, 2h or 1d", err)
        self.assertEqual(helpers.run_cli(*base(), "--turn", "--since", "1h")[0], 2)
        self.assertEqual(helpers.run_cli(*base(), "--open", "--format", "text")[0], 2)
        self.assertEqual(helpers.run_cli("--bogus")[0], 2)

    def test_nothing_found_is_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = helpers.run_cli("--agent", "codex", "--dir", tmp)
            self.assertEqual(code, 3)
            self.assertIn("No coding-agent sessions were found", err)
            self.assertIn("doctor", err)
            self.assertNotIn("Traceback", err)

    def test_unknown_agent_and_missing_file(self):
        code, _, err = helpers.run_cli("--agent", "nope")
        self.assertEqual(code, 1)
        self.assertIn("claude-code", err)
        code, _, err = helpers.run_cli("--file", "/no/such/file.jsonl")
        self.assertEqual(code, 1)
        self.assertIn("File not found", err)

    def test_file_option_detects_agent(self):
        path = helpers.LOGS / "codex" / "sessions"
        rollout = next(path.rglob("rollout-*.jsonl"))
        code, out, err = helpers.run_cli("--file", str(rollout), "--no-color")
        self.assertEqual(code, 0, err)
        self.assertIn("Codex CLI", out)

    def test_list_and_nth_and_project(self):
        code, out, _ = helpers.run_cli("list", *base("gemini"))
        self.assertEqual(code, 0)
        self.assertIn("Gemini CLI", out)
        self.assertEqual(helpers.run_cli(*base(), "--nth", "5")[0], 1)
        code, _, err = helpers.run_cli(*base(), "--project", "does-not-exist")
        self.assertEqual(code, 3)
        code, out, _ = helpers.run_cli(*base(), "--project", "shop-api")
        self.assertEqual(code, 0)

    def test_session_prefix(self):
        code, out, _ = helpers.run_cli(*base(), "--session", "7d1c1b9e")
        self.assertEqual(code, 0)
        self.assertEqual(helpers.run_cli(*base(), "--session", "zzzz")[0], 1)

    def test_doctor_runs(self):
        code, out, _ = helpers.run_cli("doctor", "--no-color", "--dir", str(helpers.DIRS["claude-code"]))
        self.assertIn(code, (0, 3))
        self.assertIn("Claude Code", out)

    def test_version_and_help(self):
        self.assertEqual(helpers.run_cli("--version")[0], 0)
        self.assertEqual(helpers.run_cli("--help")[0], 0)

    def test_no_color_env_and_plain_output_has_no_escape_codes(self):
        os.environ["NO_COLOR"] = "1"
        try:
            _, out, _ = helpers.run_cli("--agent", "claude-code", "--dir", str(helpers.DIRS["claude-code"]))
        finally:
            del os.environ["NO_COLOR"]
        self.assertNotIn("\x1b[", out)


if __name__ == "__main__":
    unittest.main()
