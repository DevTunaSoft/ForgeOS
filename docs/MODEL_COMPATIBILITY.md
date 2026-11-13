# ForgeOS local model compatibility and setup (experimental)

> **Status:** The experimental multi-model implementation is integrated on
> `main` (PR #51) and has received subsequent source updates. This guide
> describes implemented behavior, not production readiness. Real local-model
> inference, physical Windows acceptance, and the release gates in the roadmap
> remain unverified.

**Implementation milestones and completion evidence:** [Full multi-LLM engineering roadmap](MULTI_LLM_ROADMAP.md) (work packages A–J, P0–P3, source owners, RAM targets, safety and release gates). This page remains the user-facing compatibility/setup guide, not a competing implementation tracker.

## Capability inventory

| Feature | Source status | Independent runtime proof |
| --- | --- | --- |
| Ollama | Existing working integration and automatic installed-model size routing | Real-model Windows acceptance still required |
| llama.cpp loopback `/v1/chat/completions` | Experimental adapter on main | Real server/Windows integration unverified |
| LM Studio loopback `/v1/chat/completions` | Experimental adapter on main | Real server/Windows integration unverified |
| Other loopback OpenAI-compatible servers | Experimental adapter on main | Backend-specific compatibility unverified |
| Hugging Face public GGUF search, pinned inspection and local inventory status | Experimental opt-in metadata catalog on main | Live Hub GUI workflow and actual local-file re-verification unverified |
| Hugging Face GGUF download and SHA-256 resume verification | Experimental implementation with pinned revisions, partial journal, disk reserve and atomic publish | Real multi-GiB Windows/network acceptance unverified |
| Integrated GGUF execution via user-provided llama-server | Experimental local authenticated CPU managed server with manual binary selection and optional installed-runtime path | Real CPU GGUF and Windows lifecycle unverified |
| Managed llama.cpp binaries and process lifecycle | Experimental pinned official Windows x64 CPU archive installer with bundle verification, GUI start/stop and idle unload | Target Windows process ownership, DLL mutation resistance and real-GGUF inference remain unverified |
| Role-aware cross-backend task router | **Not implemented** | — |
| Managed-model memory evidence and idle unload | Experimental one-shot memory reporting and idle unload exist; no continuous governor | Real process-tree/RAM acceptance unverified |
| Automatic OOM recovery | **Not implemented** | — |

### Adapter capability matrix (source-level contract)

| Adapter | Requests ForgeOS sends | Tools / structured output | Context and output limits | Model files / license | Version identity |
| --- | --- | --- | --- | --- | --- |
| Ollama | Local `/api/generate`; non-streaming; edits request JSON mode | No tool calls. JSON mode is requested for edits, then ForgeOS validates the edit manifest | ForgeOS sends bounded `num_ctx` and `num_predict` options from its memory profile | Ollama-managed model formats; ForgeOS does not verify the model license | Server version is not queried by the adapter |
| Managed llama.cpp | Local `/v1/models` and `/v1/chat/completions`; non-streaming; sends `temperature=0` and `max_tokens` | ForgeOS sends no tool definitions or JSON-schema/response-format parameter; edit JSON is validated afterward | Output budget is sent; context ceiling remains server/model configuration and is not attested in the request | Managed path loads verified GGUF bytes; license remains a Hub/author claim requiring user review | Installed official runtime bundle and GGUF hashes are checked; live server version and physical Windows identity acceptance remain open |
| User-managed llama.cpp, LM Studio, other loopback OpenAI-compatible | Same local `/v1/models` and non-streaming `/v1/chat/completions` request contract | No tool calls or schema parameter; ForgeOS validates the returned edit manifest | Output budget is sent; server/model context support is unknown to ForgeOS | Runtime-supported formats and model licenses are provider/user supplied and not verified by the adapter | Server version, binary provenance and model identity are not attested by generic adapters |

This matrix records ForgeOS source behavior, not a guarantee that every server
accepts the same parameters. Real backend/version compatibility profiles and
measured context limits remain open release work.

ForgeOS has one GUI and no extra Python dependencies for these adapters.
The external endpoint adapters talk only to a server on **the same machine**.
The **optional experimental managed GGUF control** in the existing GUI now
starts/stops either a SHA-pinned manually supplied llama-server or a
separately installed pinned official Windows x64 CPU release. **Installation
is explicitly user-initiated and verified against the original release ZIP,
including every bundled DLL**, and does not run an installer executable or
install LM Studio. Model inference uses a temporary bearer API key. No Docker or paid API is needed for these code paths.

## Run a verified GGUF with an opt-in managed CPU runtime (experimental on main)

1. Use **Browse Hugging Face → Download and verify** to install an
   appropriately licensed GGUF. Weights are pinned to an immutable revision
   and validated by SHA-256, with a local `.forgeos.json` manifest.
2. Click **Run verified local GGUF** in the existing **Execution settings**.
   Choose **Install verified official Windows CPU runtime (opt-in)** and
   explicitly approve the GitHub download. Supported auto-install target:
   **Windows x64 CPU**, pinned llama.cpp release `b12199`, ZIP
   `llama-b12199-bin-win-cpu-x64.zip`. Archive SHA-256 and size are
   hard-coded in `runtime_installer.py`. ForgeOS rejects changed upstream
   metadata, untrusted download redirection, unsafe ZIP entries or DLL
   content that differs from the exact original verified ZIP.
3. Choose **Choose from ForgeOS downloads** to select a GGUF, then
   **Start installed CPU runtime**. The startup worker verifies the installed
   archive and its DLLs again, then verifies the GGUF against its SHA-256,
   enforces conservative free-memory admission and starts a CPU-only local
   server with a private, temporary API key. No download on Start.
4. **Manual fallback:** select your independently trusted
   `llama-server.exe`, enter its trusted lowercase SHA-256 and the GGUF,
   then **Start manual runtime**. Manual selection verifies only the
   executable and GGUF, not arbitrary companion DLL provenance.
5. ForgeOS selects backend `llama.cpp`, model `forgeos-managed`, and
   the private localhost endpoint. Generate a small plan; generated edits
   continue through existing restricted worktree/test workflows. Stop from
   the same dialog or let idle unload occur after five inactive minutes.

**Current limits:** one GUI-owned managed server per ForgeOS session; model
updates and destructive weight deletion are not implemented (reversible
download retirement and imported-model unregistration are available). There
are no persistent credentials or automatic managed-session recreation across
restart. The code checks the managed listener PID before prompts and before
accepting responses, but those checks do not atomically bind the HTTP socket to
that process; live Windows IP Helper access is denied in the current managed
session, and target Windows ownership behavior remains unverified. The child is
created suspended and assigned to its Job Object before it resumes, but target
Windows lifecycle and DLL-mutation acceptance are still open. The AppContainer
sandbox for untrusted **generated tests** does not sandbox the native
model-server process. SHA-256 is integrity evidence, **not a software
publisher signature or proof that a binary is harmless**.
Use trusted upstream binaries and noncritical projects during evaluation.

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
forgeos model-catalog inspect Qwen/Qwen2.5-Coder-3B-Instruct-GGUF
# Use the exact filename and 40-digit revision returned by inspect:
forgeos model-download Qwen/Qwen2.5-Coder-3B-Instruct-GGUF EXACT-Q4-FILENAME.gguf --revision EXACT_40_DIGIT_REVISION

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
choose one and **Inspect GGUF sizes**. Inspection shows bounded Hub card
claims (clearly labeled unverified), the immutable repository revision,
Hub-reported file sizes and hashes, and matching local-inventory status.
These results are **metadata**, not proof
that the model fits in memory, has the correct chat template, works in your
inference runtime, or can legally be used for your application.
Use **More results** to follow a Hub-provided continuation page. ForgeOS
validates that each continuation stays on the fixed Hugging Face model API
origin and preserves the original search and page size; it never constructs
or guesses an opaque pagination cursor. The GUI stops after ten pages (up to
150 repository entries at its current page size) to keep the result view
bounded.

Search and file inspection contact the Hugging Face public HTTPS API **only
when requested**. The GUI does not send project source or secrets to the Hub.
The file listing can display SHA-256 values supplied as Git LFS OIDs. Local
inventory status is metadata-only; explicit verification or managed startup
must rehash the actual local bytes.
Select a GGUF file with an available SHA-256 and click **Download and verify**.
ForgeOS asks for confirmation before contacting the Hub for that file,
pins a revision, reserves disk space, writes to a resumable `.part` file,
and verifies the final bytes before atomic publication under
`~/.forgeos/models`. A missing hash, insufficient disk, untrusted redirect,
corrupted journal, unexpected byte count or mismatched SHA-256 fails closed.
The downloaded GGUF is **not automatically loaded**. It can be explicitly
launched with **Run verified local GGUF** and a trusted user-provided or
ForgeOS-installed llama-server binary. Model updates, destructive deletion
and real CPU inference acceptance are unfinished. Do not mistake SHA-256 metadata
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
weight size still exceeds the target RAM budget. Repository availability,
license tags, and file sizes can change; use **Inspect GGUF sizes** to review
the current Hub revision and file metadata before downloading. The values above
are not measured ForgeOS benchmarks or compatibility claims.

For the i7-1370P, 32 GB RAM and integrated GPU target, begin with the
Qwen2.5-Coder-3B Q4 model and short contexts, then test the newer Qwen3.5-4B
Q4 and Qwen2.5-Coder-7B Q4 alternatives sequentially.
Do not keep both loaded simultaneously unless actual measurement justifies it.
Do not claim a minimum supported RAM amount until full process-tree memory
and Windows end-to-end acceptance evidence is recorded.

## Security and runtime limitations

- Local adapters require literal loopback addresses, and adapter
  HTTP requests disable proxy configuration and redirects. This prevents
  accidental use of remote or paid inference services through adapter URLs.
- A loopback server is **not automatically trustworthy**. External local
  endpoints are not process-attested; a different process owning their port
  could receive bounded project context. The managed llama.cpp session checks
  its listener PID and uses a temporary bearer key, but live Windows ownership
  access and atomic socket-to-process binding remain unverified.
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

**Focused unit coverage exists** for local URL validation, proxy/redirect
handling, bounded requests/responses, malformed JSON, truncated responses,
unknown models, automatic RAM-sized routing, Hub response filtering, pinned
revision and hash handling, invalid paths, local-inventory status, download
resumption and GUI contracts. These tests do not replace real runtime tests.

**Still required:** passing CI on Linux and Windows, packaged EXE GUI start,
actual llama.cpp / LM Studio response parsing, host reboot, interrupted
server/recovery, concurrent jobs, 1.5B/3B/7B benchmarks on target Windows
hardware, CPU-only load/unload, privacy checks, and user-directed end-to-end
model management. Do not mark these verified without corresponding evidence.

## Remaining integration and release phases

1. Verify current-head Linux/Windows tests, frozen Windows packaging and
   fail-closed AppContainer behavior.
2. Accept the managed runtime on physical Windows 11: install the pinned CPU
   release, load a compatible verified GGUF, run plan/edit contracts, inspect
   listener ownership, stress shutdown/restart and measure RAM/latency.
3. Exercise real Hub/CDN downloads, authentication and anti-abuse failures,
   process-kill resume, disk pressure, concurrent processes and Windows paths.
4. Complete model update/repair and quota policy without weakening the current
   per-file size, free-space reserve, journal and hash protections.
5. Run the fixed final role-qualification corpus on candidate models before
   enabling role routing or any automatic escalation.
6. Measure task quality, latency, CPU and process-tree RAM on supported
   Windows tiers, including restart and crash injection.


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
  external folders explicitly hashes and registers an existing `.gguf`
  without copying it; source/revision/license provenance remains unknown.
  Imported files are rehashed before managed use. Reversible unregister and
  reimport are supported; license validation, model updates and permanent
  deletion remain future work.

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
