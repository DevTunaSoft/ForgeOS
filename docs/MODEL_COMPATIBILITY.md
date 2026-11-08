# ForgeOS local model compatibility and setup (experimental)

> **Status:** This document describes the proposed multi-model feature branch
> `feat/local-multimodel-endpoints-20261107`. It must not be interpreted as a
> verified production feature of `main` until review and CI pass.

**Implementation milestones and completion evidence:** [Full multi-LLM engineering roadmap](MULTI_LLM_ROADMAP.md) (work packages A–J, P0–P3, source owners, RAM targets, safety and release gates). This page remains the user-facing compatibility/setup guide, not a competing implementation tracker.

## Capability inventory

| Feature | Source status | Independent runtime proof |
| --- | --- | --- |
| Ollama | Existing working integration and automatic installed-model size routing | Real-model Windows acceptance still required |
| llama.cpp loopback `/v1/chat/completions` | New experimental adapter | Server/Windows integration unverified |
| LM Studio loopback `/v1/chat/completions` | New experimental adapter | Server/Windows integration unverified |
| Other loopback OpenAI-compatible servers | New experimental adapter | Backend-specific compatibility unverified |
| Hugging Face public GGUF search and file-size/hash metadata | New experimental metadata catalog | Live Hugging Face GUI workflow unverified |
| Hugging Face GGUF download and SHA-256 resume verification | **New experimental implementation** with pinned revision, partial journal, disk budget and atomic publish | Real multi-GiB Windows/network acceptance unverified |
| Integrated GGUF execution via user-provided llama-server | **Experimental PR #51 managed start/stop and authenticated local adapter**; no native inference code, installation/update/remove | Real CPU GGUF and Windows lifecycle unverified |
| Managed llama.cpp binaries, automatic process lifecycle | **Partial prototype:** SHA-pinned user-supplied executable, verified GGUF, CPU-only argv, local-only authenticated server, start/stop/timeout | No binary auto-installer; DLL signatures, port/process ownership, child-tree termination, idle unload and real-model Windows acceptance unfinished |
| Automatic cross-backend intelligent task/agent router | **Not implemented** | — |
| Advanced model memory monitoring, idle unload and OOM recovery | **Not implemented** | — |

ForgeOS has one GUI and no extra Python dependencies for these adapters.
The external endpoint adapters talk only to a server on **the same machine**.
The **optional experimental managed GGUF control** in the existing GUI now
starts/stops a user-supplied, SHA-pinned llama-server executable with a
ForgeOS-verified GGUF and a temporary bearer API key. ForgeOS does **not**
automatically install/download llama-server, verify its adjacent DLLs, or
install LM Studio. No Docker or paid API is needed for these code paths.

## Run a verified GGUF with a managed CPU llama-server (experimental PR #51)

1. Obtain a **Windows x64 CPU llama-server binary from the official
   [ggml-org llama.cpp project](https://github.com/ggml-org/llama.cpp)**.
   Independently verify release origin, executable SHA-256, license and
   **all accompanying DLLs**. ForgeOS does not download executables, does
   not attest the publisher and does not bundle binary dependencies.
2. In ForgeOS **Browse Hugging Face**, select a suitable public GGUF and
   choose **Download and verify** (or reuse a previously verified ForgeOS
   GGUF). A separate `.forgeos.json` manifest contains pinned repository
   revision, size and expected SHA-256.
3. In **Execution settings**, click **Run verified local GGUF**. Select
   the `llama-server.exe`, paste the independent **lowercase SHA-256 of the
   executable**, and select the downloaded `.gguf` file. Click **Start
   local model**.
4. ForgeOS verifies the model bytes and the executable before executing
   anything, rejects a model too large for its conservative available-memory
   budget, selects a random local port and launches the CPU-only binary
   with a private temporary API-key file. Startup/verification is performed
   off the GUI thread; the API key is not placed on the command line.
5. After readiness, the GUI sets backend `llama.cpp`, model
   `forgeos-managed`, and its private loopback endpoint. Choose a task
   and **Generate plan**. Generated edits are still untrusted and continue
   through existing guarded worktree/test handling.
6. To release model RAM, click **Run verified local GGUF** again and
   **Stop local model**, or close ForgeOS after the current task ends.

**Current limitations:** only one GUI-owned managed server per ForgeOS
session, no automatic official binary installation, no model update/remove,
no idle unload, no persistent session authentication across app restarts,
no verified child-process-tree termination, and no process-owner proof on
randomly allocated loopback ports. A same-host port takeover remains a
possible confidentiality risk. A malicious binary or DLL can execute on the
host; **a matching hash is not proof of publisher trust**. Use only a
trusted executable from an independently verified upstream source. Managed
model session secrets are transient and not saved in the checkpoint. Thus
`resume-safe` cannot autonomously restart a private managed session
until pinned identity and credentials are reconstructed safely. Live
llama.cpp model output and physical Windows acceptance remain unverified.

Official llama.cpp server options used here include `--api-key-file`,
`--host`, `--port`, `--model`, `--alias`, and `--n-gpu-layers 0`.
See the [upstream documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

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
# Replace the filename with the exact result of model-catalog files:
forgeos model-download Qwen/Qwen2.5-Coder-3B-Instruct-GGUF EXACT-Q4-FILENAME.gguf

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
The file listing can display SHA-256 values supplied as Git LFS OIDs.
Select a GGUF file with an available SHA-256 and click **Download and verify**.
ForgeOS asks for confirmation before contacting the Hub for that file,
pins a revision, reserves disk space, writes to a resumable `.part` file,
and verifies the final bytes before atomic publication under
`~/.forgeos/models`. A missing hash, insufficient disk, untrusted redirect,
corrupted journal, unexpected byte count or mismatched SHA-256 fails closed.
The downloaded GGUF is **not automatically loaded**. In the experimental
branch it can be explicitly launched with **Run verified local GGUF** and a
trusted user-provided llama-server binary. Model update/remove and real
CPU inference acceptance are unfinished. Do not mistake SHA-256 metadata
verification for model license or executable supply-chain security.

## Conservative model shortlist for CPU-first Windows machines

Published weight sizes are approximate and **exclude** runtime overhead,
KV cache, long context, the OS, ForgeOS, compilers and integrated graphics.

| Candidate | Example quantization and published weight size | Assessment |
| --- | --- | --- |
| [Qwen2.5-Coder-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF) | Q4_K_M about 1.12 GB | Good first experiment for 8/16 GB and quick tasks; output quality needs benchmarking |
| [Qwen2.5-Coder-3B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF) | Q4_K_M about 2.1 GB | Initial trial for a 16–32 GB CPU-first workstation |
| [Qwen2.5-Coder-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF) | Q4_K_M about 4.68 GB | Trial for difficult code tasks on 32 GB, subject to measured latency and RAM |
| [Qwen3.5-4B GGUF](https://huggingface.co/lmstudio-community/Qwen3.5-4B-GGUF) | Q4_K_M about 2.7 GB; [Hugging Face quantization reference](https://huggingface.co/blog/transformers-llama-cpp-quants) | Newer general-purpose compact candidate with code and reasoning potential, but role-specific accuracy and exact llama.cpp template support on this hardware are unverified |
| [Qwen3-Coder-Next-GGUF](https://huggingface.co/Qwen/Qwen3-Coder-Next-GGUF) | Q4_K_M about 48 GB | **Not suitable** for the 32 GB target in normal RAM-limited operation |

These are **research candidates**, not benchmark winners. Evaluate model cards,
license terms, GGUF integrity, tool-call formatting, structured JSON edit
quality, and compatibility with the actual runtime before enabling automatic
routing. Qwen3-Coder-Next has low active parameter count, but total model
weight size still exceeds the target RAM budget.

For the i7-1370P, 32 GB RAM and integrated GPU target, begin with the
Qwen2.5-Coder-3B Q4 model and short contexts, then test the newer Qwen3.5-4B
Q4 and Qwen2.5-Coder-7B Q4 alternatives sequentially.
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
  metadata differences, backend failover, completed live multi-gigabyte
  download acceptance and in-process model execution remain future work.
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
4. Finish real Windows acceptance for the initial resumable, pinned-revision
   Hugging Face download implementation, then add safe deletion, version
   updates and integrated runtime installation.
5. Add task-role routing, one-model-at-a-time lifecycle, benchmark-driven
   scoring and OOM fallback without silent task re-execution.
6. Measure user-visible latency, code quality, task completion, RAM and CPU
   on 8/16/32 GB Windows systems, including restart and crash injection.


## Offline local GGUF inventory (experimental)

The existing ForgeOS model download location now supports metadata-only
listing using the **same downloader manifest**; no duplicate database,
background scanner, internet call or full multi-GB rehash during browsing.

- In **Run verified local GGUF**, choose **Choose from ForgeOS downloads**
  to select existing local model weights. This list displays published
  weight sizes and repository IDs; `present_not_reverified` is deliberately
  **not** a proof of current model file integrity.
- Use `forgeos models-local` for an advanced text inventory. It scans
  at most 4096 filesystem entries and returns at most 200 candidate GGUFs.
- Use `forgeos model-verify-local "<exact path to model.gguf>"` for an
  explicit full streaming SHA-256 check. Verification is read-only and
  rechecks file identity and GGUF magic. The managed server performs its
  own full verification before starting.
- Missing weights, changed file sizes and corrupt manifests are rejected
  or classified rather than silently used. Model import from arbitrary
  external folders, license validation, updates and safe deletion remain
  future work.

## Optional API/cloud model providers (planned; not implemented)

A later phase will add explicit opt-in adapter support for OpenAI/ChatGPT,
Anthropic/Claude and other user-authorized compatible endpoints, while
keeping **free local inference the default**. External providers must
never receive project files, logs or secrets without informed per-project
consent, credentials must not be written into checkpoints, and spend/token
limits and emergency disable controls must be enforced. **Free ChatGPT
or Claude chat access does not imply free API usage.** No cloud connector
exists in this experimental implementation, and there is no hidden
cloud fallback.
