# ForgeOS product requirements

## Mission
One turnkey, beginner-friendly, local-first Windows application for building Windows EXEs, Android APKs and web apps through natural-language requests, with free local AI where practical.

## Required capabilities
- One unified interface; users do not need programming knowledge.
- Local Ollama inference initially, adaptable model/provider interface and CPU-only fallback.
- Resource-aware sequential scheduling for 16 GB and 32 GB Windows laptops.
- GitHub import, branches, safe commits, review, rollback and optional publication with approval.
- Plan, edit, build, test, diagnose, repair and bounded retry automatically.
- Reproducible Windows, Android and web compilation and packaging.
- Executable unit, integration, regression, fuzz, fault-injection, security and recovery tests.
- Crash-safe persistence, versioned backups, event logs and complete source/handoff exports.
- Least-privilege safeguards, secrets protection and human approval for consequential external actions.
- Clear beginner-oriented product documentation.

## Implemented in 0.1.0
SQLite task store, recovery, Tkinter GUI, CLI, local-only Ollama planning, manually triggered allowlisted test presets and baseline tests.

## Not yet implemented
GitHub integration inside ForgeOS, APK/web builders, broad supported AppContainer runtimes, signed installers, secure secrets vault, actual Windows GUI/Ollama acceptance, and production-tested backup/restore. Since version 0.1.0, working prototypes of worktree code editing, bounded repair and Windows frozen unittest AppContainer isolation have been added; they are not yet a finished product.
