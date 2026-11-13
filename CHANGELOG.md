# Changelog

## 0.1.0 (first release)

- Receipts for **Claude Code**, **Codex CLI**, **Gemini CLI** and **OpenCode** sessions.
- Output as text, Markdown, HTML (one self-contained file) or JSON.
- Checks: test results, "agent said tests pass" mismatch, deletes, risky git, secrets, files outside the project, installs, deploys and more.
- Secrets are hidden by default.
- `agent-receipts install` adds a `/receipt` command inside each agent.
- `agent-receipts doctor` checks the setup. `agent-receipts list` shows recent sessions.
- `--turn`, `--since`, `--fail-on` for focused receipts and CI.
