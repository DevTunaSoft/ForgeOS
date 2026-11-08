# Contributing to ForgeOS

Thank you for helping advance ForgeOS. It is an **experimental local-first software engineering application**, not yet a production-ready application builder.

## Before making a change

- Read [README.md](README.md), [architecture and security boundaries](docs/ARCHITECTURE.md), [roadmap](docs/ROADMAP.md), and [documentation policy](docs/DOCUMENTATION_POLICY.md).
- Check [existing issues](https://github.com/DevTunaSoft/ForgeOS/issues), [pull requests](https://github.com/DevTunaSoft/ForgeOS/pulls) and the [enhancement deduplication audit](docs/ENHANCEMENT_DEDUP_AUDIT.md) so you do not accidentally build a second implementation of an existing feature.
- Keep the **single desktop GUI**, local Ollama route, non-Docker design, explicit trust boundaries, recoverable edits and bounded resource consumption intact.
- Avoid pulling arbitrary third-party code or claims of compatibility without reviewing licensing and test evidence.

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
