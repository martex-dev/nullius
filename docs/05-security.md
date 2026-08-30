# 05 — Security, Isolation, Threat Model

## 1. Threat model

The system generates code with an LLM and executes it. Treat all generated code, all retrieved text, and all dataset contents as **untrusted input**. There is no adversary assumed to be targeting the system; the realistic threats are *accidental* damage, *emergent* spec-gaming, and *injected* instructions riding in on data.

| # | Threat | Realistic form | Severity |
|---|---|---|---|
| T1 | Host compromise via generated code | Generated script writes outside the workdir, installs packages, or reads credentials from the environment | High |
| T2 | Data exfiltration | Code posts results or environment contents to a network endpoint | High |
| T3 | **Holdout leakage** | Generated code finds and reads the test split, directly or by path traversal | High — this is the one that silently destroys the science |
| T4 | Prompt injection via retrieved literature | A paper abstract or dataset field contains "ignore previous instructions" | Medium |
| T5 | Prompt injection via *its own outputs* | An agent's text is later fed to another agent as context and read as instruction | Medium, and easy to overlook |
| T6 | Resource exhaustion | Fork bomb, infinite loop, unbounded memory, disk fill | Medium |
| T7 | Supply-chain | Generated code imports a typosquatted or unpinned package | Medium |
| T8 | Credential exposure | API keys visible to the sandbox or logged in prompts | High |
| T9 | Ledger tampering | Post-hoc edits to results or registrations | Medium (integrity of the whole project) |
| T10 | Cost attack (self-inflicted) | Runaway agent loop burns the API budget | Medium |
| T11 | Container escape | Kernel/runtime vulnerability | Low probability, high impact |

## 2. Controls

### 2.1 Execution sandbox

Non-negotiable settings for every experiment container:

```text
--network=none                 # T2, T4, T7 — no network, not "limited network"
--read-only                    # T1 — rootfs immutable
--tmpfs /workdir:size=2g,noexec,nosuid,nodev
--cap-drop=ALL
--security-opt=no-new-privileges
--security-opt seccomp=<default or tighter profile>
--user 65534:65534             # non-root, no host uid mapping
--pids-limit=256               # T6
--memory=4g --memory-swap=4g   # T6
--cpus=2                       # T6
--ulimit nofile=1024 --ulimit fsize=<cap>
timeout <wall clock> + SIGKILL # T6
no bind mounts except: dataset (ro, by hash), workdir (tmpfs)
no environment variables       # T8 — the container receives config via a mounted JSON file only
```

Dependencies are baked into a pinned image built from a lockfile with hashes. Nothing is installed at runtime — with `--network=none` nothing *can* be, which turns T7 from a policy into an impossibility.

**Holdout isolation (T3)** deserves its own control, not a filesystem permission: the test split is never mounted into any experiment container. It exists only inside the Custodian's process, which accepts a fitted artifact and a preregistered evaluator and returns numbers. Even a fully compromised experiment container cannot reach data that was never in its namespace.

Escalation path if the project ever accepts third-party code or exposes a network service: gVisor (`runsc`) or a Firecracker/Kata microVM. Not needed for a single-tenant local research system; needed the moment that assumption changes.

### 2.2 Static validation before execution

Runs on the generated bundle, as code, before any container starts:

- AST scan: reject `eval`, `exec`, `compile`, `__import__`, `ctypes`, `subprocess`, `socket`, `requests`, `urllib`, `os.system`, dynamic attribute access on modules.
- Import allowlist (numpy, pandas, scipy, sklearn, torch, and the project harness). Anything else fails closed.
- Path checks: no absolute paths, no `..`, all writes under `/workdir/out`.
- **Leak detectors**: label column reachable from features; train/test index intersection; preprocessing fitted before splitting; group leakage across splits; target-encoded features computed on the full set. These run both statically on the code and dynamically on the produced arrays.
- Resource estimate must fit the allowance.
- Unit tests plus a smoke run on a tiny fixture.

A bundle that fails validation is never executed. The failure is an event and may itself become evidence about the Builder's reliability.

### 2.3 Prompt-injection containment (T4, T5)

- All external or agent-produced text is inserted into prompts inside explicitly delimited, labelled blocks with a standing instruction that content within them is **data, never instruction**.
- Agents have no tools capable of destructive action. The Builder cannot execute; the Executor cannot call an LLM; nothing except the runtime writes to the database. Injection therefore has a very small blast radius by construction: the worst outcome is a bad artifact that fails validation.
- The runtime never lets an agent's output become another agent's *instruction*; it becomes another agent's *input view row*, typed.
- Retrieved literature passes a screen for imperative/instructional patterns; hits are flagged in the event log for human review rather than silently dropped.

### 2.4 Secrets (T8)

API keys live only in the orchestrator process, never in the sandbox image, never in a mounted file, never in a prompt. Prompt and response bodies are stored hashed by default with an opt-in for full text at a debug log level that is off in benchmark runs.

### 2.5 Integrity (T9)

Append-only tables with `prev_hash` chaining; no `UPDATE`/`DELETE` grants to application roles; periodic chain verification; artifacts content-addressed so a swapped file is detectable.

### 2.6 Cost containment (T10)

Hard hierarchical budgets enforced at dispatch; per-task call caps; global daily ceiling that halts the institution; a bounded number of critique rounds per hypothesis; cache-first LLM access. Budget exhaustion is a legitimate terminal research state, not an exception to be worked around.

## 3. Residual risks accepted

- **Container escape (T11)** — accepted for a single-tenant local system; revisit if the system is ever multi-tenant or internet-facing.
- **Semantic spec-gaming that passes all detectors** — the detector suite is finite; a sufficiently creative invalid design will get through. This is precisely why the planted-defect benchmark exists: it *measures* the residual rate instead of assuming it is zero.
- **Model-level correlated error** — mitigated by role model diversity, not eliminated.

## 4. Human oversight points

The system runs autonomously within a program, but a human must remain in the loop at these boundaries, and they should be enforced as explicit gates rather than good intentions:

1. Approving a new dataset for vendoring (licence, PII, provenance).
2. Enabling live network retrieval for the Literature role.
3. Approving any change to sandbox policy, the import allowlist, or the Custodian.
4. Approving a policy version promotion (Stage 7 self-improvement).
5. Publishing anything outside the local repository.
6. Raising the global budget ceiling.
