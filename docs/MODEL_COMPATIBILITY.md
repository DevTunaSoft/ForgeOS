# ForgeOS local model compatibility and setup (experimental)

> **Status:** This document describes the proposed multi-model feature branch
> `feat/local-multimodel-endpoints-20261107`. It must not be interpreted as a
> verified production feature of `main` until review and CI pass.

## Capability inventory

| Feature | Source status | Independent runtime proof |
| --- | --- | --- |
| Ollama | Existing working integration and automatic installed-model size routing | Real-model Windows acceptance still required |
| llama.cpp loopback `/v1/chat/completions` | New experimental adapter | Server/Windows integration unverified |
| LM Studio loopback `/v1/chat/completions` | New experimental adapter | Server/Windows integration unverified |
| Other loopback OpenAI-compatible servers | New experimental adapter | Backend-specific compatibility unverified |
| Hugging Face public GGUF search and file-size/hash metadata | New read-only experimental catalog | Live Hugging Face GUI workflow unverified |
| Hugging Face download, resume, install and update | **Not implemented** | — |
| Native GGUF loading, managed llama.cpp binaries, automatic process lifecycle | **Not implemented** | — |
| Automatic cross-backend intelligent task/agent router | **Not implemented** | — |
| Advanced model memory monitoring, idle unload and OOM recovery | **Not implemented** | — |

ForgeOS has one GUI and no extra Python dependencies for these adapters.
The added adapters communicate with an inference server already running on
**the same machine**. They do not install LM Studio, start llama.cpp, or use
Hugging Face as a hosted inference provider. No Docker or paid API is needed
for these code paths.

## How to use the new local-model controls

1. Open ForgeOS, select a test repository and create a small task.
2. In **Execution settings**, choose **Inference backend**:
   - `ollama` (default; local port 11434)
   - `llama.cpp` (default local port 8080)
   - `lm-studio` (default local port 1234)
   - `openai-local` (default local port 8000)
3. Type the **exact model identifier** advertised by that running server in
   **Local model**. The default `auto` requires the server to advertise model
   sizes; many OpenAI-compatible runtimes do not. Choose the model explicitly
   if ForgeOS explains that sizes are unavailable.
4. Optionally set a different local endpoint such as
   `http://127.0.0.1:8081/v1`. Only literal `127.0.0.1` and `[::1]`
   HTTP URLs are permitted. Remote/cloud URLs, `localhost` hostname URLs,
   credentials, redirects and proxies are rejected or disabled.
5. Try **Generate plan** before a code-edit run. Check the output and any
   server errors before enabling host-test execution.

The backend and endpoint are captured when a GUI task is started. Non-Ollama
execution checkpoints record backend and endpoint in their resume contract,
preventing an incompatible configuration from being substituted silently.
Existing Ollama checkpoint compatibility is retained. **The experimental
backend-aware `resume-safe` path also reconstructs the saved llama.cpp,
LM Studio or local endpoint configuration**, after validating that the
persisted backend and canonical loopback endpoint match the original
execution contract. Corrupt or unexpected provider records are skipped;
there is no silent fallback to a different model or server. End-to-end
restart/recovery acceptance with real runtimes is still unverified.

### CLI examples

```powershell
# List what a local server advertises; no Hugging Face network traffic
forgeos models --backend llama.cpp
forgeos models --backend lm-studio --endpoint http://127.0.0.1:1234/v1

# Search Hugging Face metadata (opt-in internet request, not inference)
forgeos model-catalog search "Qwen2.5-Coder"
forgeos model-catalog files Qwen/Qwen2.5-Coder-3B-Instruct-GGUF

# Retain existing Ollama usage, or opt into a local compatible server
forgeos plan 1 --model qwen2.5-coder:3b
forgeos plan 1 --backend llama.cpp --model your-loaded-model-id
forgeos run 1 --workspace-parent C:\\ForgeOS-Workspaces --allow-host-tests --backend lm-studio --model your-loaded-model-id
```

The last command is a development-mode host-testing operation; never run
untrusted code on the host. Frozen Windows builds use the existing fail-closed
test isolation rules; model support does not relax them.

## Hugging Face discovery: what the GUI does

Select **Browse Hugging Face**, search for public GGUF repositories, then
choose one and **Inspect GGUF sizes**. Results are **metadata**, not proof
that the model fits in memory, has the correct chat template, works in your
inference runtime, or can legally be used for your application.

Search and file inspection contact the Hugging Face public HTTPS API **only
when requested**. The GUI does not send project source or secrets to the Hub.
The read-only file listing can display SHA-256 values supplied as Git LFS OIDs;
missing hashes are shown as missing. It does not download files or claim
verification of their contents.

## Conservative model shortlist for CPU-first Windows machines

Published weight sizes are approximate and **exclude** runtime overhead,
KV cache, long context, the OS, ForgeOS, compilers and integrated graphics.

| Candidate | Example quantization and published weight size | Assessment |
| --- | --- | --- |
| [Qwen2.5-Coder-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF) | Q4_K_M about 1.12 GB | Good first experiment for 8/16 GB and quick tasks; output quality needs benchmarking |
| [Qwen2.5-Coder-3B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF) | Q4_K_M about 2.1 GB | Initial trial for a 16–32 GB CPU-first workstation |
| [Qwen2.5-Coder-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF) | Q4_K_M about 4.68 GB | Trial for difficult code tasks on 32 GB, subject to measured latency and RAM |
| [Qwen3-Coder-Next-GGUF](https://huggingface.co/Qwen/Qwen3-Coder-Next-GGUF) | Q4_K_M about 48 GB | **Not suitable** for the 32 GB target in normal RAM-limited operation |

These are **research candidates**, not benchmark winners. Evaluate model cards,
license terms, GGUF integrity, tool-call formatting, structured JSON edit
quality, and compatibility with the actual runtime before enabling automatic
routing. Qwen3-Coder-Next has low active parameter count, but total model
weight size still exceeds the target RAM budget.

For the i7-1370P, 32 GB RAM and integrated GPU target, begin with the 3B Q4
model and short contexts, then test the 7B Q4 alternative sequentially.
Do not keep both loaded simultaneously unless actual measurement justifies it.
Do not claim a minimum supported RAM amount until full process-tree memory
and Windows end-to-end acceptance evidence is recorded.

## Security and runtime limitations

- An endpoint is restricted to literal loopback addresses, and local adapter
  HTTP requests disable proxy configuration and redirects. This prevents
  accidental use of remote or paid inference services through adapter URLs.
- A loopback server is **not automatically trustworthy**. If another process
  owns the chosen port, it can receive model prompts, including bounded
  repository context. Authenticate or verify your local runtime as needed.
- Model output remains untrusted. ForgeOS's existing strict JSON edit manifest,
  path allowlist, transactional file handling and guarded test execution apply.
- Requests are limited to 512 KiB and responses to 8 MiB. Catalogs are limited
  to 2 MiB. Low-memory profile limits generation length, but external runtimes
  control model weights, threads, caches and idle unload.
- Streaming generation, structured-output mode, tool-call parsing, LM Studio
  metadata differences, backend failover, full GGUF downloads and in-process
  model execution remain future work.
- Local automatic model selection is only possible when a server advertises
  positive model sizes; otherwise explicit model choice is required.
- The Hub requires internet access for discovery. Once models are obtained and
  loaded in an actual local runtime, inference can remain offline.

## Test/acceptance matrix

**Unit tests proposed:** local URL validation, no redirects/proxies, bounded
requests/responses, malformed JSON, truncated response, unknown model,
automatic RAM-sized routing, read-only Hub schema filtering, invalid paths,
and metadata caps.

**Still required:** passing CI on Linux and Windows, packaged EXE GUI start,
actual llama.cpp / LM Studio response parsing, host reboot, interrupted
server/recovery, concurrent jobs, 1.5B/3B/7B benchmarks on target Windows
hardware, CPU-only load/unload, privacy checks, and user-directed end-to-end
model management. Do not mark these verified without corresponding evidence.

## Future integration phases

1. Confirm this adapter branch passes all CI and packaged EXE acceptance gates.
2. Add persistent, versioned provider profiles and extend the current
   backend-aware checkpoint resume with end-to-end interruption tests,
   retaining the existing SQLite state machine and preventing silent changes.
3. Add a managed, signed/checksummed **llama.cpp CPU runtime** with absolute
   executable paths, controlled launch/stop, idle unloading, and process-tree
   memory limits; no Docker required.
4. Add robust Hugging Face downloads using pinned revision, trusted checksums,
   atomic resume/quarantine, disk-space and memory preflight, and safe deletion.
5. Add task-role routing, one-model-at-a-time lifecycle, benchmark-driven
   scoring and OOM fallback without silent task re-execution.
6. Measure user-visible latency, code quality, task completion, RAM and CPU
   on 8/16/32 GB Windows systems, including restart and crash injection.
