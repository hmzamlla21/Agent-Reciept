# Security

Agent Receipts reads session logs on your computer. It does not send data anywhere.

## Reporting a problem

If you find a security problem (for example, a way a secret can slip into a receipt, or a log file that makes the tool run code), please **do not open a public issue**. Use GitHub's private reporting instead: go to the **Security** tab of this repository and choose **Report a vulnerability**.

## Good to know

- Secret hiding is **best effort**. It catches common key and token shapes, not every possible secret. Check a receipt before you share it.
- Risk warnings are review hints, not a security sandbox.
