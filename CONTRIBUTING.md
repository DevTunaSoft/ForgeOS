# Contributing to ForgeOS

Thank you for helping advance ForgeOS. It is an **experimental local-first software engineering application**, not yet a production-ready application builder.

## Before making a change

- Read [README.md](README.md), [architecture and security boundaries](docs/ARCHITECTURE.md), [roadmap](docs/ROADMAP.md), and [documentation policy](docs/DOCUMENTATION_POLICY.md).
- Check [existing issues](https://github.com/DevTunaSoft/ForgeOS/issues), [pull requests](https://github.com/DevTunaSoft/ForgeOS/pulls) and the [enhancement deduplication audit](docs/ENHANCEMENT_DEDUP_AUDIT.md) so you do not accidentally build a second implementation of an existing feature.
- Keep the **single desktop GUI**, local Ollama route, non-Docker design, explicit trust boundaries, recoverable edits and bounded resource consumption intact.
- Avoid pulling arbitrary third-party code or claims of compatibility without reviewing licensing and test evidence.

## Coding principles

These principles **add to** the requirements in this file, [REQUIREMENTS.md](docs/REQUIREMENTS.md), [DOCUMENTATION_POLICY.md](docs/DOCUMENTATION_POLICY.md) and [CI_POLICY.md](docs/CI_POLICY.md). They do not replace, relax or reorder any of them. If a principle seems to conflict with a requirement, the requirement wins and the conflict is stated in the pull request. Repository-specific rules in an `AGENTS.md` or `CLAUDE.md`, when one is present, also come first.

**Original requirements, unchanged:**

- **Clean, modular code:** one canonical owner for each behavior. Reuse existing modules. Do not create a second GUI, router, store or runner.
- **Memory-efficient, bounded code:** every read, queue, log, subprocess output, scan, prompt and model context has an explicit limit. See [MEMORY_BUDGET.md](docs/MEMORY_BUDGET.md).
- **Autonomous testing:** each behavioral change ships with automated tests that run without manual steps. Failure and recovery paths are tested, not assumed.
- **Thorough regression checks:** a reported defect gets a test that fails on the unchanged code before the fix. Run the focused tests, then the full suite.
- **Continuous development:** finish each checkpoint, record it in [HANDOFF.md](docs/HANDOFF.md) and the roadmap, and name the next unblocked task.

**Priority areas for autonomous work:** execution and recovery (fail closed, resume only from validated checkpoints, never report success without evidence); security (generated code, model output, repository content and diagnostics are untrusted); Windows compatibility (case-insensitive names, trailing dots and spaces, device names, and paths that Win32 aliases); and low memory use (bounded buffers, bounded process trees). Measure resource use rather than guessing.

**Smallest complete change:**

1. Before editing, list every place the change must reach: callers, tests, fixtures, configuration and documentation. For a bug fix, search every caller of the function you touch, then fix the root cause once in shared code.
2. Prefer, in order: an existing ForgeOS helper used the way the surrounding code uses it; the Python standard library; a new dependency only when the project has none and the benefit is real. Never add a dependency for a few lines.
3. Do not add an abstraction, wrapper, option, configuration value or "for later" code that nobody asked for. Deleting code beats adding it. Keep the structure, layers and conventions the codebase already has.
4. Be lazy about the solution, never about the change. Finish every part the task needs, including the callers, tests and documents it affects.
5. Keep error handling and validation when code is moved or merged. Trust-boundary validation, error handling that prevents data loss, security, accessibility, real-hardware calibration and bounded resource limits are never removed to make a change shorter.
6. Comment only the reason the code cannot show, in one line. A known shortcut gets a comment in this form: `shortcut: <the limit>, <when to upgrade>`.
7. New non-trivial logic (a branch, loop, parser, security or data-loss decision, or a new script) keeps at least one small automated test. The "trivial change" exemption does not apply to product code under `src/forgeos/` or `forgeos_build/`.

**Reporting:** end each final report with one or two lines that state what was skipped or not checked and any risk the user must know. This is in addition to the verification evidence the definition of done requires.

## Source development

Requirements: Python 3.10+, Git; Ollama optional unless testing AI functionality.

```powershell
git clone https://github.com/DevTunaSoft/ForgeOS.git
cd ForgeOS
py -m pip install -e .
py -m unittest discover -s tests -v
py tools/check_documentation.py --links
```

Use a topic branch. Keep changes focused and testable. Prefer a new regression test proving a reported defect before or with its fix. Run relevant unit tests and the full suite, report failures accurately, and do not call a feature release-ready without its applicable real runtime acceptance.

**Safety:** source-mode testing can run generated project code on the host. Only test trusted repositories; never commit secrets or raw diagnostics. The packaged frozen AppContainer safety boundary has additional restrictions and still needs real-world acceptance.

## Definition of done for a pull request

- Explain the problem, root cause, implementation and why it does not duplicate an existing subsystem.
- Include automated tests for behavioral changes and describe their results; label tests not run.
- Document security/privacy implications, RAM/CPU changes, failure/recovery behavior and any limitations.
- **Update applicable documentation in the same PR**. UI/CLI changes require the user manual; architecture/security changes require architecture notes; new features update roadmap/status. Significant work updates the engineering handoff.
- Verify relative documentation links and run the documentation gate.
- Clearly separate merged code, open PR work and future designs.

The [PR template](.github/pull_request_template.md) has a checklist. Documentation changes are not a substitute for tests or user acceptance.

## Reporting defects

Use [GitHub Issues](https://github.com/DevTunaSoft/ForgeOS/issues). Include expected behavior, observed behavior, reproduction steps, operating system, Python/Windows build version, local model name if relevant, and which checks you personally completed. **Redact credentials, tokens, private paths and sensitive log contents.**

## Licensing

No license file is currently published in this repository. Public visibility does not grant blanket reuse rights. A project license should be selected and published deliberately by the repository owner before external redistribution is encouraged.
