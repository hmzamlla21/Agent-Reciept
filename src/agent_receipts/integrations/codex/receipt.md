---
description: Show a plain-English receipt of what the agent did in this session
argument-hint: [--turn | --since 2h]
---
Run this command in the shell and read its output:

agent-receipts --agent codex --no-color $ARGUMENTS

Then tell the user, in short and simple words, what needs their attention first: anything marked HIGH, a failed or missing test run, and anything you said that the receipt does not back up. Do not hide or play down anything. If the command is not found, tell the user to install it with: pipx install git+https://github.com/hmzamlla21/agent-reciept.git
