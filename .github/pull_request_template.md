## What changed?

<!-- Summarize behavior, affected modules and why this change is necessary. -->

## Implementation and duplication review

- [ ] Reused the canonical ForgeOS GUI, model, storage, diagnostics and test infrastructure where applicable
- [ ] Checked existing PRs/issues and the enhancement deduplication audit
- [ ] Kept local-first operation, bounded RAM and Docker-free design intact

## Verification and safety

- [ ] Relevant targeted tests added or updated
- [ ] Full Python regression tests run, or blockers clearly described below
- [ ] Windows EXE packaging and frozen security/GUI acceptance checked where applicable
- [ ] Security, privacy, resource, cancellation and recovery failure modes considered
- [ ] No unverified production/sandbox/memory claims made

**Tests actually run (commands, OS, results):**

**Known failures / unverified real-world acceptance:**

## Documentation — required for substantive changes

- [ ] README feature status and installation accurately reflect this change, or not applicable
- [ ] docs/USER_GUIDE.md revised for UI/CLI/workflow changes, or not applicable
- [ ] docs/ARCHITECTURE.md and security boundaries revised, or not applicable
- [ ] docs/ROADMAP.md / capability tracker status revised, or not applicable
- [ ] docs/WINDOWS_BUILD.md / docs/MEMORY_BUDGET.md revised where relevant, or not applicable
- [ ] docs/HANDOFF.md updated with implementation, evidence, blockers and next actions where significant
- [ ] Documentation link checker passed: `py tools/check_documentation.py --links`
- [ ] Explained documentation not-applicable decisions below

**Documentation notes / justified exceptions:**

## Release readiness

**Implemented on this PR:**

**Remaining unverified / unfinished before release:**
