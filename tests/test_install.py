import os
import tempfile
import unittest
from pathlib import Path

import helpers

ENV_KEYS = ("HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME")


class Install(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["HOME"] = str(self.home)
        os.environ["USERPROFILE"] = str(self.home)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def files(self):
        return {
            "claude-code": self.home / ".claude" / "commands" / "receipt.md",
            "codex": self.home / ".codex" / "prompts" / "receipt.md",
            "gemini": self.home / ".gemini" / "commands" / "receipt.toml",
            "opencode": self.home / ".config" / "opencode" / "command" / "receipt.md",
        }

    def test_nothing_found_gives_a_hint(self):
        code, out, _ = helpers.run_cli("install")
        self.assertEqual(code, 3)
        self.assertIn("agent-receipts install claude-code", out)
        self.assertFalse(any(p.exists() for p in self.files().values()))

    def test_installs_only_for_agents_found(self):
        (self.home / ".claude").mkdir()
        (self.home / ".gemini").mkdir()
        code, out, _ = helpers.run_cli("install")
        self.assertEqual(code, 0, out)
        files = self.files()
        self.assertTrue(files["claude-code"].is_file())
        self.assertTrue(files["gemini"].is_file())
        self.assertFalse(files["codex"].exists())
        self.assertIn("skipped", out)
        self.assertIn("/receipt", out)

    def test_each_file_points_at_its_own_agent(self):
        code, _, _ = helpers.run_cli("install", "all")
        self.assertEqual(code, 3)  # none found
        for agent, path in self.files().items():
            code, _, _ = helpers.run_cli("install", agent)
            self.assertEqual(code, 0)
            self.assertIn(f"--agent {agent}", path.read_text(), agent)

    def test_running_twice_is_safe(self):
        (self.home / ".codex").mkdir()
        helpers.run_cli("install")
        code, out, _ = helpers.run_cli("install")
        self.assertEqual(code, 0)
        self.assertIn("already installed", out)

    def test_your_own_edits_are_protected(self):
        helpers.run_cli("install", "claude-code")
        path = self.files()["claude-code"]
        path.write_text("my own command")
        code, out, _ = helpers.run_cli("install", "claude-code")
        self.assertEqual(code, 1)
        self.assertEqual(path.read_text(), "my own command")
        self.assertIn("--force", out)
        code, _, _ = helpers.run_cli("install", "claude-code", "--force")
        self.assertEqual(code, 0)
        self.assertIn("agent-receipts", path.read_text())

    def test_dry_run_changes_nothing(self):
        code, out, _ = helpers.run_cli("install", "gemini", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would install", out)
        self.assertFalse(self.files()["gemini"].exists())

    def test_uninstall(self):
        helpers.run_cli("install", "codex")
        path = self.files()["codex"]
        code, _, _ = helpers.run_cli("install", "codex", "--uninstall")
        self.assertEqual(code, 0)
        self.assertFalse(path.exists())
        helpers.run_cli("install", "codex")
        path.write_text("edited by me")
        helpers.run_cli("install", "codex", "--uninstall")
        self.assertTrue(path.exists())  # not ours any more, so it is kept

    def test_opencode_uses_commands_folder_when_that_is_what_exists(self):
        (self.home / ".config" / "opencode" / "commands").mkdir(parents=True)
        helpers.run_cli("install", "opencode")
        self.assertTrue((self.home / ".config" / "opencode" / "commands" / "receipt.md").is_file())

    def test_respects_config_dir_variables(self):
        elsewhere = self.home / "elsewhere"
        os.environ["CLAUDE_CONFIG_DIR"] = str(elsewhere)
        helpers.run_cli("install", "claude-code")
        self.assertTrue((elsewhere / "commands" / "receipt.md").is_file())

    def test_bad_agent_name_is_a_usage_error(self):
        self.assertEqual(helpers.run_cli("install", "emacs")[0], 2)


if __name__ == "__main__":
    unittest.main()
