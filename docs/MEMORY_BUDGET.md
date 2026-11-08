# ForgeOS low-memory engineering and acceptance

## Design targets, not verified minimum requirements

| System RAM | Target |
| --- | --- |
| 4 GiB | Unified GUI, project list, diagnostics and lightweight core operations |
| 8 GiB | Above plus small installed local AI models and narrow development tasks |
| 16 GiB | Sequential model-powered edit/test/repair with suitable model sizes |
| 32 GiB+ | Larger models, longer contexts and heavier projects |

ForgeOS itself should remain lean. Ollama model weights, the KV cache,
integrated GPU allocations, compilers and the operating system all consume
memory separately. Model file size is an imprecise planning heuristic, not
proof that a particular model will fit. All hardware targets require actual
Windows measurements before being stated as supported minimum specifications.

## Implemented prototype improvements on PR #32

- Host test output is read in 64-KiB chunks into a temporary disk spool
  capped at 32 MiB. Native Windows Job Object and AppContainer result readers
  use the same bounded head/tail preview policy.
- The existing SHA-256 evidence archive receives streamed original bytes
  for complete outputs up to 32 MiB. AI/GUI previews are limited to 2 MiB
  at the raw-capture layer and much smaller at the prompt/view layer.
- For incomplete or unarchivable evidence, return explicit status and
  block automatic approval/repair. Exit codes and AppContainer boundaries
  remain authoritative and unchanged.
- GUI task refresh queries only id, status and description, excluding large
  result/error blobs. Activity text is bounded around 64,000 characters.
- GUI evidence previews verify the SHA-256 archive while reading bounded
  portions. The user-requested full CLI retrieval still exists.
- Auto-model routing reads native Windows free RAM using
  GlobalMemoryStatusEx; Linux uses MemAvailable and cgroup limits.
  A reserve is maintained before selecting installed models by file size.
- Context limits are 1024/2048/4096/8192 based on memory pressure. Low-RAM
  requests set keep_alive=0 to unload Ollama models after each generation.
  Local model catalog and response JSON reads have strict byte limits.
- No Docker, paid API calls, background polling service, second GUI or
  duplicate inference backend are required.

Native Windows Job Object/AppContainer stdout uses a low-frequency 500-ms
watchdog. The frozen unittest test-output.txt now participates in the same
watchdog, and the runner terminates the Job Object if either exceeds 32 MiB
or the child substitutes an unsafe link. Only the owned execution snapshot's
fixed output path can be monitored; arbitrary host paths are rejected before
the sandbox launch. The final file size is checked again at process exit.
Tests may create other arbitrary files in their snapshot, so this is **not**
a global quota for all child-created files. Real Windows GUI/AI tests, GPU
model RAM and full task-cycle process RSS still need measured acceptance.
Raw archives may contain secrets and are not encrypted at rest.

## Reproducible benchmark

After installing the source package:

    python tools/memory_benchmark.py --mib 12 --check
    python tools/memory_benchmark.py --mib 28

Both commands use generated synthetic text and an ephemeral evidence archive.
They never install models or contact the network. The output reports elapsed
time, traced Python allocation peak, completeness and model preflight.
This is NOT a measurement of total working set, Ollama RAM or GPU VRAM.

## Acceptance work before claiming 4/8 GiB support

Run real Windows sessions or constrained virtual machines at 4 and 8 GiB.
Record GUI startup/idle working set, repeated queue refresh, several hours
of task/diagnostic activity, peaks during sequential Ollama/build/test runs,
memory after model unload, process-tree RSS, Windows job caps and recovery
after termination. Confirm that the frozen AppContainer isolation tests pass.
Do not claim production or a minimum specification based on GitHub CI alone.

For 4 GiB, the intended feature set is lightweight core operation; locally
running large models is not expected to fit. Inability to fit any installed
model should produce a clear preflight error, never automatic cloud fallback.


## On-demand working-set telemetry (in development)

ForgeOS now has a stdlib-only native working-set probe on Windows and Linux.
Windows reports GetProcessMemoryInfo process working set, peak working set,
and private committed memory. Linux reads /proc/self/status VmRSS and VmHWM;
private memory is unknown rather than guessed. No monitoring daemon or
continuous polling is installed.

Run: `python -m forgeos memory` for a one-shot system/own-process report,
and `python tools/memory_benchmark.py --mib 12 --cycles 3 --check`
for synthetic repeated-capture allocations and post-GC RSS readings.
This does **not** include the Ollama process, child compilers, memory-mapped
models or shared GPU allocations, and cannot establish the 4/8 GiB hardware
acceptance requirements on its own.

## Hosted Windows packaged GUI lifecycle check (PR #37)

The frozen Windows executable is now exercised by GitHub Actions with an
actual hidden Tkinter window, real widgets, notebook tabs, event loop and
clean close(), reporting one-shot own-process RSS in local JSON. A 90-second
outer watchdog prevents hung UI startup from stalling packaging indefinitely.
This is a hosted Windows GUI lifecycle test, not an installed
4/8 GiB Windows user device, local Ollama inference, or autonomous
end-to-end acceptance. Such real-world tests remain mandatory.

## Packaged GUI multi-page memory pressure check (PR #38)

The frozen hosted Windows CI harness now uses an ephemeral 520-task SQLite
fixture with >4 MiB of synthetic stored diagnostics, validates four real
Treeview pages and both page navigation directions, and records process RSS
before/after paged history access. Added memory is capped at 64 MiB for this
one acceptance scenario. This is a broad defensive ceiling, **not** a claim
that ordinary paging requires 64 MiB, and is not a 4/8 GiB hardware test.
No user repositories, Ollama weights, or private files are used.


## Experimental secure pytest runtime (successor to PR #29; pending CI)

The frozen Windows bundled pytest runner uses a fixed, snapshot-owned
`.forgeos-runtime/pytest-log.txt` instead of the Windows NUL device,
which the zero-capability AppContainer denied in the original PR #29 smoke.
The parent validates both the original test-output file and pytest logging
file before launching and watches **each** for its existing 32 MiB bound,
plus Windows link/junction substitution checks. The optional
`secure-pytest` packaging extra avoids installing pytest on source-only
systems that don't use the frozen pytest runtime. The logging plugin and
`caplog` behavior remain enabled; external plugin autoload is disabled.
This guard is per-file, **not** a complete filesystem quota. Physical 4/8 GiB
Ollama and cross-process RAM acceptance remain outstanding.
