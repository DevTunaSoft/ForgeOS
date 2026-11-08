# What ForgeOS Will Do — and What It Will Not Do

> **ForgeOS is an active, experimental prototype, not a finished autonomous application builder.**
> This document explains the **intended product**, the **features actually available on `main`**, the **limits that remain**, and the **behaviors we deliberately will not allow**.
> A shipped prototype or passing CI test is **not** proof that a complete application was built, installed, secured, and independently validated on a user's PC.
>
> **Verified against repository documentation:** 2026-11-07, commit `f5809c7e`.
> Check the current [roadmap](ROADMAP.md) and [main branch](https://github.com/DevTunaSoft/ForgeOS) for subsequent changes.

## In one sentence

**ForgeOS is being built to turn ordinary-language software requirements into tested, recoverable, usable software—using a single, local-first Windows application with free local AI as the default.**

The goal is a **turnkey application builder**, not merely a chat box that suggests snippets of source code. A non-programmer should be able to describe a project once and let the system handle the authorized engineering steps, including testing, troubleshooting, packaging, documentation, and a complete handoff.

**Today**, ForgeOS primarily accepts tasks for **existing local Git repositories**. Its real, but limited, prototype can plan changes with an already installed Ollama model, make bounded edits in separate worktrees, run selected tests, capture evidence, attempt repairs within limits, and preserve some recovery checkpoints. A genuinely hands-off request-to-installable-application workflow is **not yet verified or complete**.

## The target experience

A user may eventually enter a request such as:

> Build an offline Windows application that records meetings, transcribes audio, identifies speakers, summarizes discussion, and exports Word and PDF files. Deliver a tested executable, source code, a user manual, and a complete handoff.

The intended system then:

1. Understands and records the requirements, assumptions, constraints and acceptance criteria.
2. Plans the architecture, file structure, development phases, dependencies and test strategy.
3. Selects suitable installed local AI models without unexpected paid API charges.
4. Creates or safely updates source code in an isolated, recoverable workspace.
5. Handles necessary development dependencies and invokes the correct build toolchains.
6. Executes relevant unit, integration, regression, failure-injection, security and lifecycle tests.
7. Diagnoses failures, fixes their root causes, retests and measures actual forward progress.
8. Persists checkpoints and safely resumes eligible work if a task or computer stops.
9. Packages and exercises the resulting application in an appropriate target environment.
10. Delivers the working artifact **if successfully verified**, complete source, a user guide, test evidence, known limitations and a detailed continuation handoff.

**This is the product vision. It is not a description of an end-to-end capability proven in the current build.** An honest blocked/incomplete result is preferable to a fictitious success or unverified installer.

## Capabilities: implemented versus planned

| Area | Final intended behavior | Current implementation or limitation |
| --- | --- | --- |
| One integrated interface | Accessible desktop experience for non-programmers | **Implemented prototype:** single Tkinter GUI with Development and Activity & diagnostics tabs |
| Plain-language requests | Create a complete plan and implement a requested application | **Implemented prototype:** task queue, Ollama planning and constrained change requests for existing Git repositories |
| Free local AI | Prefer installed local models; manage resource budgets; no mandatory paid API | **Implemented prototype:** Ollama integration and RAM-aware selection; models must be installed separately |
| Multiple cooperating AI roles | Separate and coordinate planning, implementation, testing, debugging and review roles | **Planned:** full multi-agent orchestration and dynamic routing |
| Source generation | Create or modify code safely, with dependency-aware application structure | **Partial:** validated, budgeted multi-file edits in separate Git worktrees; no universal new-project builder |
| Automatic troubleshooting | Diagnose, repair, rebuild, retest and continue without repeated approvals | **Partial:** bounded test/repair loops and advisory failure-learning statistics |
| Windows applications | Build, exercise and deliver installable EXEs for requested projects | **Planned:** ForgeOS itself is packaged as an EXE; this is **not** a general Windows app builder |
| Android applications | Handle Android SDK/Gradle, compile and validate APKs | **Planned:** general automated APK builder and APK delivery |
| Web applications | Build, test and package complete frontend/backend web projects | **Planned:** general web builder and deployment workflow |
| Quality assurance | Automatically create and run comprehensive tests, fuzzing, failure injection and regression suites | **Partial:** selected test presets, automated project tests and ForgeOS's own CI; not universal application QA |
| Test execution safety | Restrict generated/untrusted code without mandatory Docker | **Partial:** hosted Windows frozen AppContainer and Job Object smoke for supported Python unittest and experimental bundled pytest; broader runtimes/hardware unverified |
| Crash and file recovery | Restart from verified checkpoints without losing or overwriting work | **Partial:** persistent SQLite tasks, edit journals and limited test/repair checkpoint resumption |
| Progress and learning | Maintain task state, measure quality progress and retain verified lessons | **Partial:** task records, diagnostics, bounded repair-learning statistics; not a fully semantic long-term project memory |
| GitHub integration | Import repos, manage branches/PRs, review CI, publish after approval | **Planned inside the ForgeOS app**; ForgeOS's own repository is maintained on GitHub externally |
| Low-memory performance | Remain responsive on modest Windows systems and avoid unbounded allocations | **Implemented prototype optimizations:** paged tasks, streamed logs, bounded context, one-shot memory reports; hardware targets unverified |
| Results and diagnostics | Present trustworthy success/failure, original evidence, logs and corrective actions | **Implemented prototype:** SHA-256 evidence references, bounded summaries and activity views; raw evidence is not encrypted at rest |
| Backups, export and release | Versioned encrypted backups, verified restore, source/installer/archive + handoff | **Partial/planned:** engineering handoff exists; automatic complete export, signed installer, encrypted backup/restore are unfinished |
| User manual | Explain setup, controls, limitations, errors and recovery as functionality changes | **Implemented:** [living user manual](USER_GUIDE.md), offline GUI help and documentation sync checks |

These status labels describe **availability and scope**, not perfection: **implemented prototype** means code exists; **partial** means only specified workflows exist; **planned** means not delivered. Even tested prototypes still need real Windows/Ollama/hardware acceptance.

## What ForgeOS can actually do today

The current [Windows EXE prototype](WINDOWS_BUILD.md) can open a single desktop interface. **This is not a general Windows app builder.** It can save task descriptions in a local SQLite database, display paginated history, and keep larger historical results off the main GUI list.

Given an existing **local Git repository**, the user can enter a plain-English task, select an installed Ollama model or `auto`, generate a plan, and initiate an experimental build/test/repair loop. The engine validates edit requests, writes to **separate Git worktrees**, retains transactional edit journals, and keeps results available for manual inspection.

For compatible projects and runtimes, ForgeOS can run selected tests, capture bounded original output and compact summaries, archive evidence with SHA-256 references, and attempt limited automatic repairs. Supported secure **frozen Windows** Python runtimes include `python-unittest` and an experimental **bundled `python-pytest`** path; other selectable presets are **not** automatically supported or isolated in the packaged executable.

It can report its **own process** memory on demand. It uses bounded logs and context, a 150-row task-history page size, and resource-aware model heuristics; **it cannot measure total Ollama/build/GPU memory from that single process report**. The GUI provides **Activity & diagnostics → How to use ForgeOS**, which opens the canonical manual offline.

The repository's GitHub Actions workflows have passed Linux/Windows regression checks, packaged Windows GUI lifecycle/pagination checks with a 520-task synthetic database, and a hosted frozen AppContainer boundary smoke. **These checks validate particular scenarios on hosted runners, not a complete unattended application build on an actual user's machine.**

## What ForgeOS cannot do yet

- **Take any arbitrary prompt and reliably return a finished, installable Windows application, Android APK or deployed website.** The general application-building and release pipelines are not complete.
- **Create every new application project from nothing through the GUI.** Current workflows primarily expect an existing local Git repository.
- **Install and configure Git, Ollama, model weights, Android SDKs, compilers and other toolchains for every possible project.** Some components must be installed separately.
- **Guarantee that AI-generated code is correct, secure, original, maintainable or complete.** Tests and review provide evidence for a defined scope, not infallibility.
- **Run all language ecosystems safely inside the packaged Windows sandbox.** Unsupported presets must fail closed; source/dev-mode host execution may run project code directly.
- **Resume every possible crash from the exact instruction or phase.** Current recovery is checkpoint- and workspace-dependent; some interruptions are deliberately blocked for inspection.
- **Manage GitHub authentication, pull requests, reviews, releases or deployments inside ForgeOS today.** These are roadmap items.
- **Automatically publish, commit, merge or push the code it generated.** Generated worktrees are for review.
- **Provide a verified encrypted backup/restore system, a secrets vault or encrypted diagnostics at rest.**
- **Promise a 4 GB/8 GB Windows minimum or show complete AI/process-tree RAM usage.** Those are engineering targets requiring physical or constrained-VM tests.
- **Provide a signed production installer, warranty of unattended safety or production-ready quality today.**

See the [roadmap](ROADMAP.md) for sequence and exit criteria and the [architecture](ARCHITECTURE.md) for current technical boundaries.

## What ForgeOS is deliberately designed **not** to do

These are **product principles**, not guarantees that every proposed safeguard has already been implemented:

| We do **not** want | Required design behavior |
| --- | --- |
| Mandatory paid AI subscriptions | Prefer free/local AI; never silently introduce paid provider charges |
| Mandatory Docker | Use lightweight, supported native isolation approaches where viable |
| A collection of disconnected coding apps | Retain one integrated user-facing GUI and reuse canonical modules |
| Repeated permission requests for routine authorized development | Proceed autonomously inside scope; ask only for genuinely consequential actions |
| Silent GitHub publishing, deployment or external purchases | Require appropriate explicit approval for consequential external effects |
| Silent overwrites or deletion of existing work | Validate edits, isolate them and preserve transaction/recovery evidence |
| Running untrusted code unrestricted after containment fails | Fail closed rather than silently falling back to host execution |
| Fake successful tests or guessed performance numbers | State verified results, failed results and untested cases separately |
| Unbounded RAM, log growth or indefinite runaway jobs | Maintain enforceable budgets, bounded capture and reliable cancellation/recovery |
| Automatically uploading private source/logs to a cloud AI service | Keep local AI as the default; any external integration must be intentionally enabled |
| Duplicate subsystems to inflate a feature count | Extend a tested existing implementation and keep the 52-outcome deduplication audit accurate |

**Security nuance:** Worktrees protect the user's Git branch structure; they are **not** operating-system sandboxes. Current source-mode tests can execute project code on the host with explicit trust/consent. A restricted frozen Windows AppContainer path exists for certain Python tests, but still requires real-world acceptance.

## An illustrative completed project

Suppose the user requests:

> Build a Windows volume-monitoring application. Keep selected volume levels above a minimum, save diagnostics, provide a simple configuration screen, and supply an installer, source code and instructions.

**When ForgeOS reaches the target capabilities**, a successful delivery would include:

1. A requirements specification with explicit boundaries and testable criteria.
2. Source files and reproducible build instructions.
3. Real executable output with dependencies accounted for.
4. A verified test record covering normal behavior, permission failures, restarts, recovery and edge cases.
5. Security and resource usage findings, including unresolved risks.
6. A beginner-friendly installation and operating manual.
7. A full source package and engineering handoff that another developer or AI can continue.
8. An accurate final assessment, including anything not tested on real target hardware.

A directory of generated Python files, a passing synthetic unit test or a green CI badge **alone would not count as complete delivery**.

## Memory design and expected hardware

The [memory engineering plan](MEMORY_BUDGET.md) sets **targets, not verified minimums**:

| System RAM | Engineering target |
| --- | --- |
| 4 GiB | Interface, task queue, lightweight diagnostics and non-AI operations |
| 8 GiB | Narrow local-model tasks using small suitable installed models |
| 16 GiB | More practical sequential planning/editing/testing with suitable models |
| 32 GiB or more | Larger models, larger context windows and more demanding builds |

Ollama model weights, KV cache, operating system, compilers and GPU/shared graphics consume memory separately from the ForgeOS GUI. The goal is to avoid unnecessary memory use and keep code clean and maintainable, **not** to assert that every AI workload fits on low-memory hardware.

## What must be proven before release

ForgeOS should **not** be called a turnkey production builder until independently documented acceptance includes:

- **Full workflow:** real English request → actual local-model planning → edits → test/repair → packaged usable application → source/manual/handoff.
- **Real Windows operation:** interactive EXE launch and accessibility on ordinary desktops, local Ollama connectivity and relevant toolchains.
- **Robust recovery:** induced crashes, partial writes, damaged journals, stale checkpoints and safe restart without overwriting unrelated work.
- **Security:** contained execution with adversarial tests, clear permission boundaries, no silent host/network fallback.
- **Memory/reliability:** long-running process-tree RAM, CPU and disk measurements on representative target machines; realistic model workloads.
- **Release quality:** reproducible artifacts, test logs, package integrity, documented dependencies, installation/uninstallation and rollback.
- **Documentation:** accurate user manual, troubleshooting, architecture, roadmap and [cumulative handoff](HANDOFF.md) updated with every substantive change.

Release evidence must distinguish **implemented**, **experimental**, **in progress**, **planned** and **actually verified**. No numerical project-completion percentage should be presented as an independently validated release-readiness score.

## Where to go next

**If you want to use today's prototype:** begin with the [step-by-step user manual](USER_GUIDE.md), then review the [Windows build and test guide](WINDOWS_BUILD.md).

**If you want to understand the engineering work:** read the [technical architecture](ARCHITECTURE.md), [roadmap](ROADMAP.md), [52-enhancement tracker](RTK_ENHANCEMENTS.md), [deduplication audit](ENHANCEMENT_DEDUP_AUDIT.md) and [handoff](HANDOFF.md).

**Bottom line:** ForgeOS is intended to become an autonomous software engineering department in one local-first desktop application. The underlying task, editing, testing, diagnostics and recovery systems are taking shape. **The defining goal—dependable, end-to-end application delivery for someone who does not program—remains the next major proof point.**
