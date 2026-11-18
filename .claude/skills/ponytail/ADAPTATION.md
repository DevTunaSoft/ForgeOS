# Ponytail skill: provenance and adaptation

## Source

- Repository: https://github.com/DietrichGebert/ponytail
- Commit: `96608e078b2d5ea607c5c3b8b5a54d1c1fba6846` (shallow clone, read 2026-11-18)
- File: `skills/ponytail/SKILL.md`. The upstream plugin manifest lists version 5.3.3.
- Licence: MIT, copyright 2026 DietrichGebert. The full licence text is in `LICENSE.txt`, copied unchanged.

## What is included

`SKILL.md` carries the upstream rules verbatim, with three blocks marked **ForgeOS adaptation**:

1. **Precedence.** The repository's rules and the ForgeOS requirements come before this skill. The upstream "trivial changes need no test" rule does not apply to product code.
2. **Activation.** Claude Code applies a project skill when it is invoked or judged relevant. Upstream's "active for the whole session" behaviour depended on the plugin hooks, which are not installed here.
3. **Never-cut list.** The memory limits in `docs/MEMORY_BUDGET.md` and the trust boundaries in `docs/ARCHITECTURE.md` are added to the upstream list.

## What is deliberately not included

- **The Claude Code plugin hooks** (`.claude-plugin/`, `hooks/claude-codex-hooks.json`, `hooks/claude-band.json`). The session-start, prompt-submit and subagent hooks run `node` scripts. `hooks/ponytail-mode-tracker.js` starts a detached background process (`ponytail-band-import.js`) and writes mode flags under the user's home directory. The band and board write per-turn ledger rows, and the plugin manifest offers an optional webhook that can receive task names. ForgeOS does not run background services, so none of this is installed.
- **The other ponytail skills and commands** (`ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`, `ponytail-help`, and the `commands/` directory). They were not requested. Review and audit could be added later, after their own review.
- **`AGENTS.md` and the other host adapters** (Cursor, Copilot, Windsurf, Kiro and others). Claude Code does not need them. `AGENTS.md` would add an always-on instruction file that duplicates this skill, and `main` has none.

## Conflict check

- On `main` there was no `CLAUDE.md`, `AGENTS.md` or `.claude/skills/`. The only Claude Code configuration is `.claude/settings.json`, which is unchanged.
- Other remote branches carry unmerged instruction files: `AGENTS.md` on `docs/agent-instructions-index-20261115`, `chore/ai-credit-efficiency-20261115` and the `fragment/rtk*` branches, and `.github/copilot-instructions.md` on `chore/copilot-autonomy-bootstrap-20261112`. None is merged, and this change does not touch them. If one is merged later, check it against the precedence clause in `SKILL.md`.

## How to update

1. Fetch the upstream repository and compare its `skills/ponytail/SKILL.md` with the copy here.
2. Re-apply only the three ForgeOS adaptation blocks.
3. Replace the commit hash above, and copy `LICENSE` again if it changed.
4. Check the precedence clause against `docs/REQUIREMENTS.md`, `CONTRIBUTING.md`, `docs/DOCUMENTATION_POLICY.md` and `docs/CI_POLICY.md`.
