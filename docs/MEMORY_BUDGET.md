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
