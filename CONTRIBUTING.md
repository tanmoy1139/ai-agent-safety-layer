# Contributing

This project exists to make AI agents safer. Contributions are welcome.

## How to contribute

1. Fork the repo and create a branch from `main`.
2. Make your change.
3. Run the test suite: `pytest tests/`
4. Open a pull request against `main`.

## Pull request rules

**Every PR must:**
- Include tests for new behavior. No test, no merge.
- Keep all existing tests green. If your change breaks a test, fix the change, not the test. If the test itself is wrong, say so explicitly in the PR description.
- Follow the fail-closed principle. Any new code path that governs an action must block on error, never permit. If you add a `try/except` around a safety check, the `except` branch must deny.
- Never commit secrets. No API keys, tokens, passwords, or private keys in code, tests, docs, or commit messages. Use environment variables.
- Keep public APIs in plain English. Internal module names may keep their existing vocabulary, but anything a user imports should be self-explanatory.

**PR description must include:**
- What the change does, in one paragraph.
- Why it is needed.
- How you tested it.
- Any safety implication: does this change what gets blocked or permitted?

**What will not be accepted:**
- Changes that weaken a safety check without a documented reason.
- New dependencies without justification. The core stays lean.
- Fail-open behavior in any governance path.
- Marketing copy or branding changes without discussion.

## Reporting security issues

If you find a vulnerability in the safety layer itself, do not open a public issue. Open a pull request with the fix and the test that proves it, following the same rules above. The project's own history (see `docs/REMEDIATION.md`) shows this is how it improves: attack it, fix it, verify it.

## Code of conduct

Be direct, be honest, be kind. Attack the code, not the person. Assume good intent until proven otherwise.
