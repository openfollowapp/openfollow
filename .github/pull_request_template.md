<!--
Title: <type>(<scope>): <summary>, see CLAUDE.md "Commit messages, PR and issue titles".
Workflow: CLAUDE.md "Issue → PR workflow". Mark a box that does not apply "n/a: <why>"
(docs-only change, no Pi reachable); drop "Closes" for Dependabot and release bumps.
-->

Closes #<n>

## Change

## Verification

- [ ] macOS: `make ci` green on a Mac
- [ ] Pi: `make ci-remote` green on the Pi, not the local fallback
- [ ] Manual test (what, on which target):
- [ ] Claude Code `/code-review`: no major finding left
- Hardware tests outstanding (none / list):
