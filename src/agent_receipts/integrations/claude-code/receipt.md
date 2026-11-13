---
description: Show a plain-English receipt of what you did in this session
allowed-tools: Bash(agent-receipts:*)
---
Here is the receipt for this session:

!`agent-receipts --agent claude-code --no-color $ARGUMENTS`

Read the receipt above and tell the user, in short and simple words, what needs their attention first: anything marked HIGH, a failed or missing test run, and anything you said that the receipt does not back up. Do not hide or play down anything.
