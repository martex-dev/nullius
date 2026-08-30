# ADR-0002 — Pluggable sandbox backends, and an honest security posture

- **Status:** accepted
- **Date:** 2026-08-30
- **Relates to:** `docs/05-security.md`

## Context

`docs/05-security.md` specifies a Docker sandbox with `--network=none`, read-only rootfs, dropped capabilities and cgroup limits, on the premise that generated code is untrusted. The build machine has no container runtime.

But the MVP makes that premise false for now. [ADR-0004](0004-builder-as-compiler.md) removes LLM-written code from the MVP entirely: experiments are compiled from a typed `ExperimentSpec` by our own unit-tested harness. Until code generation lands (M12), the code being executed is **ours**.

The threat therefore shifts from *malice* to *accident and emergence*: a compiled experiment that reads a path it shouldn't, an unbounded loop, a memory blow-up, or — the one that actually matters — a code path that touches the holdout split.

## Decision

Define `SandboxBackend` with three implementations, selected by configuration and recorded in every run's provenance.

**`SubprocessSandbox` (default).** Separate process, restricted working directory, and before user code runs:
- AST validation and an import allowlist (reject `socket`, `subprocess`, `ctypes`, `eval`/`exec`/`compile`, dynamic import).
- A `sys.addaudithook` (PEP 578) denying `socket.connect`, `socket.bind`, `subprocess.Popen`, `os.system`, and any `open` outside the workdir.
- Wall-clock kill; `psutil` memory ceiling; output confined to `workdir/out` with a size cap.

**`DockerSandbox`.** The full hardening set from `docs/05-security.md`. Required from M12, available earlier to anyone who has Docker.

**`GvisorSandbox`.** Placeholder; only if the system ever runs third-party code or exposes a network service.

`SubprocessSandbox` is **not a security boundary against a determined adversary**, and the README, the CLI banner and the generated reports all say so in those words. It is defence in depth against accident. Holdout isolation does not depend on it in either case: the test split is never in the sandbox's filesystem view at all — that guarantee comes from the Custodian's process boundary, not from the sandbox tier.

## Consequences

**Good.** The project builds and runs today. The interface is right, so upgrading is configuration rather than refactoring. The provenance record makes the isolation tier of every historical claim auditable.

**Bad.** A contributor could mistake the default for real isolation. Mitigated by naming: the config value is `sandbox.backend = "subprocess-unsafe-for-untrusted-code"`, and `nullius doctor` warns.

**Consequence with teeth.** M12 (code generation) has a hard gate: `DockerSandbox` must be the active backend, enforced in code, not by convention.
