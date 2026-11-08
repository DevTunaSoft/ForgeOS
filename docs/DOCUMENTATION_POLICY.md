# Documentation maintenance policy

**Status: required project practice for all future ForgeOS development.**

ForgeOS documentation is part of the product, not a one-time marketing exercise. **Every substantive source change must be evaluated for documentation impact in the same pull request.** No capability may be described as shipped, secure, fast, production-ready, or supported on particular hardware without matching implementation and independently checked evidence.

## The source of truth

1. **Code and verified tests/CI** determine what is implemented and what was actually checked.
2. **README.md** is the public-facing entry point: mission, primary features, installation, current limitations, live links.
3. **docs/USER_GUIDE.md** is the click-by-click, jargon-light operational manual, including warnings and recovery steps.
4. **docs/ARCHITECTURE.md** explains canonical modules, data flow, security boundaries and nonduplication decisions.
5. **docs/ROADMAP.md** separates completed prototypes, work in progress, next priorities and future plans.
6. **docs/WINDOWS_BUILD.md** defines the executable download/build procedure and precisely what acceptance tests prove.
7. **docs/MEMORY_BUDGET.md** tracks numerical limits and measured-versus-target memory claims.
8. **docs/RTK_ENHANCEMENTS.md** and **docs/ENHANCEMENT_DEDUP_AUDIT.md** track the 52 outcome commitments and overlap.
9. **docs/HANDOFF.md** records engineering history, outstanding issues, evidence and continuation instructions.
10. **CONTRIBUTING.md** and **.github/pull_request_template.md** govern contribution documentation review.

If two files contradict each other, **correct the outdated document** rather than assuming the more optimistic claim is true.

## Documentation trigger matrix

| When this changes | Update / recheck |
| --- | --- |
| User-visible behavior, GUI, labels, workflow | README and/or USER_GUIDE, screenshots only from verified running UI |
| CLI options or commands | USER_GUIDE command reference; README examples if prominently featured |
| New, removed or changed subsystem | ARCHITECTURE; README feature matrix when users are affected |
| New feature, milestone or resolved blocker | ROADMAP, relevant capability tracker, README feature status |
| Security, trust, sandbox, diagnostics or persistence | ARCHITECTURE, warning text, relevant guide; security regression evidence |
| Runtime/build/packaging or GitHub Actions | WINDOWS_BUILD, README install notes, measured CI evidence |
| Memory, model size, output capture, performance | MEMORY_BUDGET and relevant README limitation; explain measurement scope |
| RTK-related outcome, optimization or duplicate | RTK_ENHANCEMENTS, ENHANCEMENT_DEDUP_AUDIT, canonical module owner |
| Any significant source change | HANDOFF with files, verified test results, remaining blockers and next work |
| New user problem or fixed confusing behavior | USER_GUIDE troubleshooting and reproduction/solution |

## Four distinct status labels

- **Implemented:** code exists on the specified branch; behavior still requires honest testing caveats.
- **Experimental:** integrated prototype with constrained or not-yet-real-world-tested behavior.
- **In progress:** open branch/PR or incomplete implementation, **not** merged into main.
- **Planned:** accepted requirement or roadmap item without implemented functionality.

**Verified** is an evidence qualifier, not a synonym for implemented. Document exact validation (OS, version, test command, workflow, result, hardware) and identify what remains unverified.

Do **not** publish fake screenshots, invented benchmark numbers, unmerged features as shipped, unsupported RAM minimums, or passing test claims based only on CI configuration.

## Required pull-request workflow

1. Inspect impacted implementation and tests; identify doc changes before writing PR description.
2. Update user instructions/status and architecture/security as needed **in the same branch**.
3. Run the repository's documentation checks and relevant application tests.
4. Confirm no newly added relative Markdown link is broken and no CLI example contradicts the code.
5. Include a release-readiness section separating evidence from outstanding physical Windows end-to-end acceptance.
6. Record progress and continuation in HANDOFF for significant work.
7. Review the PR checklist; explain any document marked not applicable.
8. Merge only after required checks and applicable acceptance gates pass.

The pull-request automation enforces that changes to product implementation have at least one corresponding documentation change. It is a **minimum compliance check**, not a replacement for review of the substance or accuracy of the text.

## Maintenance rules

- Keep one unified ForgeOS app; do not introduce duplicate documentation or architecture for a second GUI/backend without explicit justification.
- Preserve examples on Windows PowerShell where possible and separate source/development-mode risk from packaged runtime restrictions.
- Prefer links to canonical docs over copying long implementation narratives into the README.
- Periodically review old PR numbers and version-specific statements; avoid describing historical pending PRs as still open without checking GitHub.
- Use screenshots only when captured from an actual tested ForgeOS UI and clearly label their branch/version; mockups must be labeled as concepts.
- Avoid embedding personal/project data, credentials, raw logs or local secrets in docs.
- Do not silently modify the release-readiness claim or remove known defects merely to make the project page more appealing.

## Local validation

```powershell
py tools/check_documentation.py --links
py -m unittest discover -s tests -v
```

For a pull request, CI also compares its base and head revisions to verify a code/documentation update pairing. To test that locally when both refs are available:

```powershell
py tools/check_documentation.py --base origin/main --head HEAD
```

The documentation gate is deliberately conservative. If it blocks a change, update the applicable document(s) with the real behavior and evidence; do not add empty filler just to pass.
