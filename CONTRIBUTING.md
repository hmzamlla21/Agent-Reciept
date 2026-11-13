# Contributing

Thank you for helping. Bug reports, new agents, new rules and better wording are all welcome.

## Set up

You only need Python 3.9 or newer. There is nothing else to install.

```bash
git clone https://github.com/hmzamlla21/agent-reciept.git
cd agent-reciept
python3 -m unittest discover -s tests      # run the tests
PYTHONPATH=src python3 -m agent_receipts --agent claude-code --dir examples/logs/claude-code
```

Please keep the tool free of outside dependencies. That is part of why it is easy to install.

## Add a new agent

An "adapter" is one small file that reads one agent's logs and turns them into a list of actions.

1. Copy the closest file in `src/agent_receipts/adapters/` (for example `gemini.py` for one-JSON-file-per-session tools, or `claude_code.py` for JSONL tools).
2. Fill in `id`, `label`, `default_roots()`, `find()` and `load()`. In `load()`, create one `Action` per tool call and set `ok` to `True` or `False` when the log says whether it worked. Finish with `return self.finish(session)`.
3. Register it in `src/agent_receipts/adapters/__init__.py` (the `ALL` list, and `sniff()` if the file name is recognisable).
4. Add a story for it in `tools/make_examples.py`, run `python3 tools/make_examples.py`, and add the new agent to the test lists in `tests/`.
5. Optional: add a `/receipt` command file in `src/agent_receipts/integrations/<agent>/` and a target in `src/agent_receipts/install.py`.
6. Add a row to the table in the README.

Rules for a good adapter: never crash on a strange or half-written file (skip it and add a warning instead), never guess when the log does not say, and use only the standard library.

## Add or change a warning

Warnings live in `src/agent_receipts/risk.py`. Each one is a small rule on a parsed command or file path. When you add one:

- Add a test in `tests/test_risk.py` for the case it **should** flag **and** a harmless look-alike it should **not** flag. False alarms make people ignore the receipt.
- Use `HIGH` only for things that can lose work, leak secrets or change real systems.

## Pull requests

- Keep each pull request small and focused.
- Run the tests first. CI runs them on Linux, macOS and Windows.
- Write in plain, simple English, in code comments and in docs.
