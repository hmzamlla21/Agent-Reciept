import json
import tempfile
import unittest
from pathlib import Path

import helpers
from agent_receipts import adapters
from agent_receipts.model import COMMAND, Action, Session
from agent_receipts.receipt import build
from agent_receipts.risk import assess
from agent_receipts.util import AdapterError, parse_duration, parse_ts

AGENTS = ["claude-code", "codex", "gemini", "opencode"]


class EveryAgent(unittest.TestCase):
    def test_example_sessions_read_the_same_story(self):
        for agent in AGENTS:
            with self.subTest(agent=agent):
                s = helpers.load_example(agent)
                r = build(s)
                self.assertEqual(r["session"]["cwd"], "/home/dev/shop-api")
                self.assertIn("checkout", r["session"]["title"].lower())
                self.assertEqual({e["path"] for e in r["files"]["edited"]}, {"src/checkout.py", "src/pricing.py"})
                self.assertEqual(r["summary"]["commands_failed"], 1)
                self.assertEqual(r["verification"]["verdict"], "fail")
                self.assertEqual(r["verification"]["runs"], 3)
                self.assertIsNotNone(r["session"]["duration_seconds"])
                self.assertTrue(r["last_message"].startswith("All done"))
                found = {a["code"] for a in r["attention"]}
                for code in ("rm-recursive", "git-force-push", "pipe-to-shell", "claim-mismatch", "install"):
                    self.assertIn(code, found)
                self.assertTrue(any(c in found for c in ("secret-read", "secret-touch")))

    def test_secrets_never_reach_any_output(self):
        for agent in AGENTS:
            with self.subTest(agent=agent):
                r = build(helpers.load_example(agent))
                self.assertNotIn("EXAMPLE1234567890", json.dumps(r))
                code, out, _ = helpers.run_cli("--agent", agent, "--dir", str(helpers.DIRS[agent]), "--format", "html", "-o", "-")
                self.assertNotIn("EXAMPLE1234567890", out)

    def test_no_redact_flag_shows_secret(self):
        r = build(helpers.load_example("claude-code"), redact_secrets=False)
        self.assertIn("EXAMPLE1234567890", json.dumps(r))

    def test_failures_carry_the_error_text(self):
        for agent in AGENTS:
            with self.subTest(agent=agent):
                r = build(helpers.load_example(agent))
                self.assertEqual(len(r["failures"]), 1)
                self.assertIn("AssertionError", r["failures"][0]["detail"])

    def test_gemini_project_is_guessed_and_says_so(self):
        r = build(helpers.load_example("gemini"))
        self.assertTrue(any("guessed" in w for w in r["warnings"]))

    def test_turn_only_keeps_the_latest_request(self):
        s = helpers.load_example("claude-code")
        s.prompt_times.append(s.actions[5].ts)  # pretend a new message arrived mid-way
        r = build(s, turn_only=True)
        self.assertLess(r["summary"]["actions"], len(s.actions))
        self.assertIn("Latest request", r["window"])

    def test_since_window_drops_old_actions(self):
        r = build(helpers.load_example("claude-code"), since_seconds=60)  # example logs are from 2026-11-12
        self.assertEqual(r["summary"]["actions"], 0)

    def test_find_sniffs_the_right_agent(self):
        for agent in AGENTS:
            with self.subTest(agent=agent):
                path = adapters.get(agent).find(helpers.DIRS[agent])[0]
                self.assertEqual(adapters.sniff(path).id, agent)


def session_with(*specs):
    s = Session("claude-code", "Claude Code", "t", Path("t.jsonl"), cwd="/p")
    for i, (cmd, ok) in enumerate(specs):
        a = Action("Bash", COMMAND, cmd, ts=parse_ts(1_700_000_000 + i), ok=ok)
        s.actions.append(a)
        assess(a, s.cwd)
    return s


class Verification(unittest.TestCase):
    def verdict(self, *specs, last=""):
        s = session_with(*specs)
        s.last_message = last
        return build(s)

    def test_verdicts(self):
        self.assertEqual(self.verdict(("pytest", True))["verification"]["verdict"], "pass")
        self.assertEqual(self.verdict(("pytest", False))["verification"]["verdict"], "fail")
        self.assertEqual(self.verdict(("ls", True))["verification"]["verdict"], "none")
        self.assertEqual(self.verdict(("pytest", None))["verification"]["verdict"], "unknown")

    def test_edits_after_passing_run_make_it_stale(self):
        s = session_with(("pytest", True))
        edit = Action("Edit", "edit", "/p/a.py", ts=parse_ts(1_700_000_500), ok=True)
        s.actions.append(edit)
        r = build(s)
        self.assertEqual(r["verification"]["verdict"], "pass-but-stale")
        self.assertEqual(r["verification"]["edited_after"], ["a.py"])

    def test_claim_mismatch(self):
        self.assertTrue(any(a["code"] == "claim-mismatch" for a in self.verdict(("pytest", False), last="Done! All tests pass.")["attention"]))
        self.assertTrue(any(a["code"] == "claim-mismatch" for a in self.verdict(("ls", True), last="Everything passes now.")["attention"]))
        self.assertFalse(any(a["code"] == "claim-mismatch" for a in self.verdict(("pytest", True), last="All tests pass.")["attention"]))

    def test_claims_with_negation_or_no_claim_are_not_flagged(self):
        for text in ["Not all tests pass yet.", "The tests still fail, I could not fix it.", "I changed the file.", ""]:
            with self.subTest(text=text):
                r = self.verdict(("pytest", False), last=text)
                self.assertFalse(any(a["code"] == "claim-mismatch" for a in r["attention"]), text)


class Robustness(unittest.TestCase):
    def test_bad_lines_are_skipped_and_reported(self):
        src = adapters.get("claude-code").find(helpers.DIRS["claude-code"])[0]
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            target = proj / "s.jsonl"
            target.write_text(src.read_text(encoding="utf-8") + "{not json\n\n[1,2]\n\"x\"\n" + '{"type":"assistant","message":', encoding="utf-8")
            clean = len(adapters.get("claude-code").load(src).actions)
            s = adapters.get("claude-code").load(target)
            self.assertTrue(any("could not be read" in w for w in s.warnings))
            self.assertEqual(len(s.actions), clean)  # every good line still counted

    def test_empty_and_odd_files_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "e.jsonl"
            empty.write_text("", encoding="utf-8")
            for agent in ("claude-code", "codex"):
                s = adapters.get(agent).load(empty)
                r = build(s)
                self.assertEqual(r["summary"]["actions"], 0)
            odd = Path(tmp) / "session-x.json"
            odd.write_text(json.dumps({"sessionId": "a", "messages": [None, 5, {"type": "gemini", "toolCalls": "nope"}]}), encoding="utf-8")
            self.assertEqual(build(adapters.get("gemini").load(odd))["summary"]["actions"], 0)
            bad = Path(tmp) / "session-y.json"
            bad.write_text("{oops", encoding="utf-8")
            with self.assertRaises(AdapterError):
                adapters.get("gemini").load(bad)

    def test_codex_command_shapes(self):
        from agent_receipts.adapters.codex import command_text

        self.assertEqual(command_text(["bash", "-lc", "ls -la"]), "ls -la")
        self.assertEqual(command_text(["git", "status"]), "git status")
        self.assertEqual(command_text("echo hi"), "echo hi")
        self.assertEqual(command_text(None), "")

    def test_time_helpers(self):
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertIsNone(parse_duration("soon"))
        self.assertIsNone(parse_duration("-5m"))
        self.assertEqual(parse_ts("2026-11-12T09:00:00.1234567Z").year, 2026)
        self.assertEqual(parse_ts(1_700_000_000_000), parse_ts(1_700_000_000))
        self.assertIsNone(parse_ts("garbage"))
        self.assertIsNone(parse_ts(None))


if __name__ == "__main__":
    unittest.main()
