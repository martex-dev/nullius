# ADR-0008 — The Station draws the record, not the design

- **Status:** accepted
- **Date:** 2026-09-02
- **Relates to:** `BUILD_PLAN.md` M22, `docs/02-architecture.md` §3, `docs/03-data-model.md`

## Context

M22 renders the institution as a floor plan: one room per department, laid out from `Role` and `HypothesisState` and filled in from the committed protocols, the committed results, the locked truths, and — optionally — one ladder's ledger.

Deriving the map from the enums was the point. It is also what made the disagreement visible.

`docs/02-architecture.md` §3 specifies a nine-state pipeline with five terminal exits, and states as a hard invariant that *the report generator fails loudly if any registration lacks a terminal state*. The ledgers say something else. Across every ladder this project has run — v2 through v6, every arm, every replicate — **every hypothesis sits at `analyzed`**, and `advance_hypothesis` is called with only six of the fifteen states anywhere in `src/`:

| written by some code path | written by nothing |
|---|---|
| `draft`, `shelved`, `registered`, `executed`, `analyzed`, `abandoned_budget` | `screened`, `built`, `challenged`, `replicated`, `reviewed`, `institutional`, `refuted`, `inconclusive`, `revised` |

The work those states describe is genuinely done. The same ledgers carry `bundle.built`, `objection.raised`, `replication.recorded` and `claim.promoted` events, with the Skeptic, the Replicator and the Director as their actors. What is missing is not the behaviour; it is the *state transition that would record the behaviour*.

Separately, `Arm.reviewer` is hashed into every registered protocol and read by no code path (`BUILD_PLAN.md` M22), so the `reviewed` state has no producer even in principle.

So a map derived from the enum has fourteen rooms, five of which own a state that nothing writes, and six terminal doors that have never been used.

## Decision

**The station draws what the record says, and labels the gap.**

Three options were considered and two rejected.

*Rejected: draw the design.* Show the pipeline as `docs/02` specifies it, with tokens flowing through Challenge, the Blind room and Review and out through the terminal doors. This is the prettier picture and it is the one the brief describes. It is also a picture of a system that does not exist, presented by a project whose entire argument is that its record survives inspection. A diagram is more persuasive than a table and less obviously checkable, which makes it the worst place in the repository to be optimistic.

*Rejected: fix the state machine now.* Make the kernel advance a hypothesis through every state and write a terminal one. This is a real change to what every institutional arm does — it writes rows, emits events, and touches the confidence rubric's inputs. Every committed results file would then describe a system that no longer exists, and `nullius benchmark run` would produce different ledgers for the same registered protocol. That is a protocol v7 with a re-run ladder, not a milestone that draws a picture.

*Accepted: report it.* Every terminal exit is drawn at the same width, counted from `hypothesis.state_changed` events, and reads zero. A room whose state nothing writes shows an empty room rather than invented activity. The Review room, whose switch reaches no code at all, is drawn locked with the reason on it. The page's own limitations list says that the pipeline's back half runs without being recorded as state.

**`Verdict` and `HypothesisState` are kept apart.** Both vocabularies contain `refuted` and `inconclusive`, and they mean different things: the first is an answer about the world scored against planted truth, the second is where a hypothesis stopped. The record room shows the verdict distribution and the terminal-door counts in separate blocks, with the distinction stated, and a test asserts the overlap in wording still exists rather than asserting it away.

**Nothing is asserted that can be counted.** Where an invariant can be shown holding on the record in front of the reader, it is: the Registry counts runs that began after the registration authorising them, and the Vault counts holdout rows by who computed them. A constraint existing and a constraint having held are different claims, and the second is the one worth drawing.

## Consequences

- `docs/02-architecture.md` §3 is **not edited**. It is a design document and a historical record, on the same principle that keeps a superseded protocol on disk. The divergence lives here.
- The invariant it states — a report generator that fails on a registration with no terminal state — remains unimplemented, and implementing it today would fail every run in the project's history. Doing so is future work, and it belongs with whatever wires the missing transitions.
- The station's terminal doors are, for now, a picture of an unfinished state machine. They are also the sharpest thing on the page: the exits are drawn equal because refutation is a terminal success, and every one of them reads zero.
- If a later milestone writes those transitions, nothing in the station needs editing. The counts come from the events, the Review room's lock comes from `dead_switches()`, and both update themselves. The tests are written so that they keep passing when the numbers change, and fail if the drawing stops matching the record.
