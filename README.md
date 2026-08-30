<div align="center">

# Nullius

**An artificial research institution — built so that it can be proven wrong.**

*nullius in verba* · take nobody's word for it

[![CI](https://github.com/martex-dev/nullius/actions/workflows/ci.yml/badge.svg)](https://github.com/martex-dev/nullius/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)

</div>

---

## What this is

A multi-agent system that carries a research question through hypothesis, preregistration, experiment design, sandboxed execution, statistical analysis, adversarial challenge, independent replication and peer review — into a claim whose every number traces back to a hashed artifact.

And, more importantly, **a way to find out whether any of that helps.**

Nullius is evaluated on research questions generated from structural causal models we author. We know the true effect of every intervention — including the ones that are exactly zero — which features are causal and which are spurious, and where a defect was planted. So "did the institution reach the right conclusion, and was it appropriately confident?" is measured against ground truth rather than judged by a language model reading another language model's prose.

The project's own claim is falsifiable:

> Institutional structure — preregistration, adversarial challenge, independent replication, and evidence-typed memory — improves the accuracy and calibration of autonomous empirical research relative to an unstructured agent, at a measurable cost.

That may turn out to be false. The benchmark is designed to be able to say so.

## What this is not

Not a chatbot, not an AutoGPT descendant, not a paper generator, not a RAG application, not a wrapper around an existing agent framework. There is no conversation between agents. There is a ledger.

## Design principles

**Norms are invariants, not instructions.** A model asked not to rewrite its hypothesis after seeing results will rewrite its hypothesis after seeing results. So preregistration is a content hash written before dispatch and checked by a foreign key; the test split lives only inside a custodian process; a `CHECK` constraint makes it impossible for an agent-authored number about the holdout to enter the database at all.

**No number passes through a language model.** Every statistic is computed by library code. Reports are template-rendered from the database, with prose confined to slots the renderer rejects numerals in.

**Agents do not converse.** Every action is `typed state view → validated artifact → append-only event`. Replay, provenance, audit and cost control all fall out of that one choice.

**Refutation is a success.** `refuted` and `inconclusive` are terminal states reported with the same prominence as `institutional`. Nearly half the question bank has a true effect of exactly zero, so a system that always finds something scores badly.

**Confidence is computed, never asserted.** It is a function of replication count, effect size over interval width, open critical objections, preregistration status, and holdout queries consumed.

## Status

Early. Under active construction against a public plan — see **[BUILD_PLAN.md](BUILD_PLAN.md)** for milestones and their acceptance criteria, and the badge above for what currently passes.

Nothing here claims to work until its acceptance test is green in CI.

## Documentation

| | |
|---|---|
| [`BUILD_PLAN.md`](BUILD_PLAN.md) | Milestones, acceptance criteria, environment constraints |
| [`docs/01-critique.md`](docs/01-critique.md) | Analysis of the originating spec: ambiguities, 19 failure modes, where LLMs may and may not be trusted, prior art |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Agent protocol, research state machine, experiment lifecycle, knowledge representation, research economy |
| [`docs/03-data-model.md`](docs/03-data-model.md) | Schema and the invariants expressed as constraints |
| [`docs/04-evaluation.md`](docs/04-evaluation.md) | Question bank, metrics, the B0–B7 baseline ladder |
| [`docs/05-security.md`](docs/05-security.md) | Threat model and sandbox design |
| [`docs/06-roadmap.md`](docs/06-roadmap.md) | Staged roadmap, complexity estimates, the first experiment |
| [`docs/adr/`](docs/adr/) | Decision records, including every deviation from the above |

## Quick start

```bash
git clone https://github.com/martex-dev/nullius
cd nullius
uv sync
uv run nullius doctor
```

No database server, no container runtime and no API key are required to build or test the project. See [ADR-0001](docs/adr/0001-sqlite-default-postgres-optional.md) and [ADR-0005](docs/adr/0005-llm-cache-and-replay.md) for why.

## ⚠️ Security posture

The default sandbox (`SubprocessSandbox`) uses AST validation, an import allowlist, a Python audit hook denying sockets and subprocesses, and hard resource limits. **It is not a security boundary against a determined adversary.** It is defence in depth against accidental and emergent misbehaviour, which is the actual threat while the MVP executes only our own compiled code ([ADR-0004](docs/adr/0004-builder-as-compiler.md)).

Code generation (M12) is gated in code on `DockerSandbox` being active. Do not run untrusted code under the default backend. See [`docs/05-security.md`](docs/05-security.md).

## Licence

Apache-2.0. See [LICENSE](LICENSE).
