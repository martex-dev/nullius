"""What each department is, said in ordinary words.

Everything else on the station page is derived: the figures come out of
committed artifacts, the room map comes out of ``db/enums.py``, the route comes
out of the arm's own switches. This module is the exception and says so. It is
hand-written prose, and it exists because a charter that reads

    "Detectors and the Skeptic raise typed objections, each carrying the
    experiment that would tell it apart from the claim it disputes"

is exact and is no use at all to somebody who has just clicked on a room and
wants to know what happens in it.

The rule that keeps the exception safe is the same rule the rest of the page
runs on: **nothing here contains a number.** Not one. Every quantity on a
department's brief is filled in by the page from the record, so this file
cannot be the reason something on screen is wrong about what the institution
did. It can only be wrong about what the institution is *for*, which is a thing
a person wrote down on purpose and can be argued with.

The exact wording of the rule is still one click away on every brief, because
plain language is a summary and a summary is a lossy thing.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DEPTS", "PEOPLE", "Dept", "Desk", "Section", "numerals"]


@dataclass(frozen=True, slots=True)
class Section:
    """One heading and one paragraph on a department's own tab."""

    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class Desk:
    """A department's own tab: the thing this room, and no other, does."""

    tab: str
    """Its id, unique across the station, because the tab strip is one list."""

    label: str
    lead: str
    sections: tuple[Section, ...]


@dataclass(frozen=True, slots=True)
class Dept:
    """A department in ordinary words."""

    plain: str
    """What this room is for, in one or two sentences."""

    steps: tuple[str, ...]
    """What happens here, in order, as somebody standing in the room would see it."""

    next_up: str
    """What would happen here next, or what it is waiting on."""

    desk: Desk
    """The department's own tab: the thing it, and no other room, is for."""

    extra: tuple[Desk, ...] = ()
    """Any further tabs of its own. One department is nothing like another and a
    dossier that gave them all the same six tabs said the opposite."""

    @property
    def desks(self) -> tuple[Desk, ...]:
        return (self.desk, *self.extra)


#: Who the eleven actors are. A job title somebody outside the project would
#: recognise, and a sentence saying what that person actually does here.
PEOPLE: dict[str, tuple[str, str]] = {
    "theorist": (
        "the one with the idea",
        "Writes down a guess about how something works, and — in the same breath — writes "
        "down what would show the guess is wrong. If it cannot say what would prove it "
        "wrong, the idea is turned away at the door.",
    ),
    "director": (
        "the one who decides what gets money",
        "Picks which ideas are worth running and which are not. The ones that are not are "
        "not deleted: the decision is written down, along with what beat them.",
    ),
    "literature": (
        "the one who checks it is not already known",
        "Reads what has already been established, inside this institution and outside it, "
        "so nobody spends money finding out something that is on a shelf somewhere.",
    ),
    "designer": (
        "the one who writes the plan",
        "Turns the idea into an exact plan for an experiment: what will be measured, how "
        "many times, and what counts as a result. The plan is locked before anything runs.",
    ),
    "builder": (
        "the one who builds the thing that runs",
        "Turns the locked plan into something a machine can actually execute, and takes a "
        "fingerprint of it, so what ran later can be shown to be what was planned.",
    ),
    "analyst": (
        "the one who reads the results",
        "Writes down what the experiment showed. It does not do the arithmetic — the "
        "arithmetic is done by code — because a number that passed through a writer is a "
        "number somebody could have nudged.",
    ),
    "skeptic": (
        "the one whose job is to disagree",
        "Tries to break the result. An objection is only allowed if it comes with an "
        "experiment that would settle the argument; complaining without one does not count.",
    ),
    "replicator": (
        "the one who tries it again from scratch",
        "Runs the same experiment again without being allowed to see how the first one "
        "went. It is handed the plan and nothing else, and what it was allowed to look at "
        "is recorded so the blindfold can be checked afterwards.",
    ),
    "reviewer": (
        "the one who signs it off",
        "Would score the finished claim against the record underneath it and decide "
        "whether the institution is willing to stand behind it. In this build nothing "
        "ever asks it to, which the map shows by drawing its room locked.",
    ),
    "custodian": (
        "the keeper of the sealed answers",
        "Holds the part of the data nobody else may touch, in a separate process, and "
        "answers only a fixed number of questions that were written down in advance.",
    ),
    "system": (
        "the machinery, not a person",
        "The part of the institution that is code rather than judgement: it compiles the "
        "plans, runs the sandbox and writes down what happened. It is drawn as a machine "
        "because giving it a face would suggest it makes decisions, and it does not.",
    ),
}


DEPTS: dict[str, Dept] = {
    # -------------------------------------------------------------------- 01
    "drafting": Dept(
        plain=(
            "Where an idea starts. Somebody writes down a guess about how something works "
            "and, at the same time, writes down what would show the guess is wrong."
        ),
        steps=(
            "The Theorist writes one idea: what it thinks happens, and why it thinks so.",
            "It names the single measurement that would settle the question, and which way "
            "that measurement should move if the idea is right.",
            "It names the smallest change worth caring about, so that a tiny wobble in the "
            "results cannot later be announced as a discovery.",
            "It writes the condition that would prove the idea wrong. An idea without that "
            "line is refused here and never reaches the rest of the building.",
        ),
        next_up=(
            "Ideas that get through this room go next door to be screened. Nothing in this "
            "room is waiting on anything."
        ),
        desk=Desk(
            tab="intake",
            label="what gets in",
            lead="Two things decide whether an idea is allowed to exist here at all.",
            sections=(
                Section(
                    "It has to be able to be wrong",
                    "An idea that no possible result could contradict is not an idea about "
                    "the world, it is a description of a mood. Every draft carries the "
                    "condition that would sink it, and one that does not is refused at "
                    "intake rather than politely filed.",
                ),
                Section(
                    "It has to be new",
                    "A draft too close to one the institution already holds is refused as "
                    "unnovel. This is a machine check against what is already believed, not "
                    "a matter of taste, and it is why the room next door reads the "
                    "literature before anybody spends money.",
                ),
                Section(
                    "Why the wrongness comes first",
                    "Writing down what would refute the idea before running anything is the "
                    "whole trick. Afterwards, everybody can find a reason the result was "
                    "what they expected; the condition has to be fixed while it can still "
                    "cost somebody something.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="metric",
                label="the one number",
                lead="Every idea here names a single measurement in advance. This is why that is "
                "one.",
                sections=(
                    Section(
                        "Choosing afterwards is choosing the winner",
                        "If the measurement is picked once the results are in, there is "
                        "almost always one that came out well, and reporting that one is not "
                        "dishonest so much as inevitable. Naming it first is what makes the "
                        "result a test rather than a search.",
                    ),
                    Section(
                        "And a direction, and a size",
                        "The draft also says which way the number should move and how much of "
                        "a move is worth calling a finding. A result that is real but far too "
                        "small to matter is a different thing from a result, and the two are "
                        "easy to conflate once everybody is pleased.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 02
    "screening": Dept(
        plain=(
            "Where somebody decides which ideas are worth spending money on. Most are not — "
            "and the ones that are not are not thrown away."
        ),
        steps=(
            "The Literature actor checks whether the answer is already known somewhere.",
            "The Director decides what gets funded out of what was drafted.",
            "Anything not funded is shelved. Shelving writes down the decision and names "
            "what beat it, so the reason is still there years later.",
        ),
        next_up=(
            "Funded ideas go to the Registry Room to be written up as an exact plan and "
            "locked. Shelved ones stay on the shelf, findable, with their reasons attached."
        ),
        desk=Desk(
            tab="shelving",
            label="what happens to the losers",
            lead="Most ideas do not get funded. This is what that means here.",
            sections=(
                Section(
                    "Shelved is not deleted",
                    "A shelved idea keeps everything it arrived with, and gains a row saying "
                    "when it was shelved and which idea was preferred. Nothing in this "
                    "building has a delete button; that is a deliberate architectural "
                    "choice, not an oversight.",
                ),
                Section(
                    "Why that matters",
                    "An institution that quietly drops the ideas it did not like looks, from "
                    "the outside, exactly like an institution that only ever had good ideas. "
                    "Keeping the losers with their reasons is what makes the hit rate mean "
                    "anything.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="funding",
                label="how the call is made",
                lead="What the Director is deciding between, and what the decision is recorded "
                "against.",
                sections=(
                    Section(
                        "It is a comparison, not a verdict",
                        "Funding is a choice among the drafts on the table, so a shelved idea "
                        "is not judged bad. It is judged less worth the money than something "
                        "else that week, and the record names the something else.",
                    ),
                    Section(
                        "The reading happens first",
                        "The Literature actor checks what is already known before the money "
                        "is committed, because the cheapest experiment is the one somebody "
                        "has already run and written up.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 03
    "registry": Dept(
        plain=(
            "Where a funded idea becomes an exact plan, and the plan is locked. After this "
            "room, the plan cannot be quietly changed — only replaced, in public."
        ),
        steps=(
            "The Designer writes the experiment out in full: what is measured, how many "
            "times, and what would count as a result.",
            "A checker reads the plan and refuses it if anything is missing.",
            "The plan is fingerprinted and locked. Nothing anywhere in the building is "
            "allowed to run without a locked plan behind it.",
        ),
        next_up=(
            "The locked plan goes to the Development Workshop to be turned into something a "
            "machine can run."
        ),
        desk=Desk(
            tab="lock",
            label="the lock",
            lead="What locking a plan actually does, and what it stops.",
            sections=(
                Section(
                    "The refusal is not a promise",
                    "Running without a locked plan is refused by the database itself, not by "
                    "an actor choosing to behave. That distinction is the point: a rule an "
                    "agent could decide to ignore is not a rule, it is a preference.",
                ),
                Section(
                    "Changing your mind is allowed, quietly changing it is not",
                    "A locked plan cannot be edited. Changing anything creates a new, "
                    "connected registration marked as exploratory — so the record shows both "
                    "what was originally promised and what was actually done, side by side.",
                ),
                Section(
                    "Why fingerprint it",
                    "So that later, when somebody asks whether the experiment that ran was "
                    "the experiment that was planned, the answer is a comparison rather than "
                    "a recollection.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="exploratory",
                label="changing your mind",
                lead="What happens when the plan turns out to be wrong after it is locked.",
                sections=(
                    Section(
                        "A child, not an edit",
                        "Altering a locked plan creates a new registration, linked to the "
                        "original and marked exploratory. Both stay readable, so a reader can "
                        "see what was promised and what was actually done without taking "
                        "anybody's word for it.",
                    ),
                    Section(
                        "Exploratory is not a lesser kind of work",
                        "It is an honest label for work whose plan came after some of the "
                        "data. Nothing forbids it. What is forbidden is doing it and calling "
                        "it the original plan.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 04
    "workshop": Dept(
        plain=(
            "Where a locked plan is turned into something a machine can actually run, and "
            "that something is fingerprinted before it is allowed anywhere near the data."
        ),
        steps=(
            "The plan is compiled into a runnable bundle.",
            "The bundle is fingerprinted, and the fingerprint is written down first.",
            "Only then is the bundle handed to the Experiment Floor.",
        ),
        next_up=(
            "In the current build this room is staffed by ordinary tested code rather than "
            "by a language model, which is why nobody here writes prose."
        ),
        desk=Desk(
            tab="bundle",
            label="the bundle",
            lead="What gets built here, and why its fingerprint is taken first.",
            sections=(
                Section(
                    "Fingerprint before, not after",
                    "The bundle's fingerprint is recorded before it runs. Taking it "
                    "afterwards would prove only that the thing which ran was the thing "
                    "which ran.",
                ),
                Section(
                    "Built by code, on purpose",
                    "The compiler here is the project's own tested harness rather than an "
                    "agent. Nothing about the step needs judgement, and a step that needs no "
                    "judgement should not be given any.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="seeds",
                label="running it more than once",
                lead="Why the same experiment is built to run several times over.",
                sections=(
                    Section(
                        "One run is an anecdote",
                        "The plan registers a set of starting conditions in advance, and the "
                        "bundle is built to run once for each. A single run cannot tell a "
                        "real effect from the luck of where it happened to start.",
                    ),
                    Section(
                        "Registered in advance, again for the same reason",
                        "The starting conditions are fixed at registration. Choosing them "
                        "afterwards would let somebody keep the runs that came out well, "
                        "which is the same problem as choosing the measurement afterwards "
                        "wearing a different hat.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 05
    "execution": Dept(
        plain=(
            "Where the experiment actually runs — sealed off from the rest of the machine, "
            "once for each starting condition that was agreed in advance."
        ),
        steps=(
            "The bundle runs inside a sandbox, once per registered starting seed.",
            "Everything it produces is fingerprinted, along with a note of exactly what "
            "machine and what software versions it ran on.",
            "If the machinery breaks, the run may be tried again. If the experiment fails, "
            "it never is — a failure is a result.",
        ),
        next_up=(
            "The results go to the Analysis Room, where code — not a writer — turns them "
            "into numbers."
        ),
        desk=Desk(
            tab="sandbox",
            label="the sandbox",
            lead="What the experiment is allowed to do while it runs, and what it is not.",
            sections=(
                Section(
                    "What is blocked",
                    "Reaching the network, starting other programs, and writing anywhere "
                    "outside its own working folder are all refused and written down. The "
                    "log of refusals is part of the record.",
                ),
                Section(
                    "Broken machinery versus a broken idea",
                    "A crashed disk may be retried. A result that came out the wrong way "
                    "never is. Retrying an experiment until it agrees with you is the "
                    "oldest way to fake a finding, so the two cases are separated by the "
                    "code rather than by whoever is watching.",
                ),
                Section(
                    "Why record the machine",
                    "So that a result which only appears on one particular machine can be "
                    "found out later, instead of being inherited as a fact.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="manifest",
                label="what machine it ran on",
                lead="Every run records the conditions it happened under, not just what it "
                "produced.",
                sections=(
                    Section(
                        "A result is a result on something",
                        "The versions, the platform and what the host could enforce are all "
                        "recorded beside the numbers. A finding that turns out to hold only "
                        "on one particular setup can then be found out, rather than inherited "
                        "as a fact about the world.",
                    ),
                    Section(
                        "It is recorded even when nothing looks wrong",
                        "Especially then. The provenance of a result that surprised nobody is "
                        "exactly the provenance nobody thinks to keep, and exactly the one "
                        "wanted later.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 06
    "analysis": Dept(
        plain=(
            "Where the raw results become a number and a verdict. The person here writes the "
            "sentence; they are not allowed to write the number."
        ),
        steps=(
            "Code computes the effect, the uncertainty around it, and a verdict.",
            "The Analyst writes the claim in words.",
            "The writing is checked for digits. A sentence with a figure typed into it is "
            "rejected before it can be stored.",
        ),
        next_up=(
            "The claim goes to the Challenge Chamber, where somebody is paid to try to break it."
        ),
        desk=Desk(
            tab="numbers",
            label="who writes the numbers",
            lead="The one rule this room exists to enforce.",
            sections=(
                Section(
                    "No statistic passes through a writer",
                    "Every figure is computed by code from the stored results. The prose "
                    "slots the Analyst fills in refuse numerals outright, so a number cannot "
                    "get onto a page by being typed — only by being calculated.",
                ),
                Section(
                    "Why be that strict",
                    "Because the failure this guards against is not lying. It is the "
                    "ordinary, sincere drift of a figure toward the value somebody was "
                    "hoping for, which nobody notices happening and everybody can do.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="verdicts",
                label="what the answers mean",
                lead="The claim that comes out of here is one of a small fixed set, and they are "
                "not ranked.",
                sections=(
                    Section(
                        "Supported, refuted, inconclusive",
                        "A verdict says what the evidence did, not how the work went. An "
                        "experiment that cleanly refutes its own hypothesis has succeeded at "
                        "the thing it was for.",
                    ),
                    Section(
                        "Inconclusive is the one that gets buried",
                        "It is the most common honest outcome and the least published one "
                        "anywhere. It is recorded here identically to the others and drawn "
                        "the same size on the map.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 07
    "challenge": Dept(
        plain=(
            "Where somebody is paid to disagree. A finding that has not survived this room "
            "is not allowed to become something the institution says."
        ),
        steps=(
            "Automatic detectors look for the standard ways a result goes wrong.",
            "The Skeptic raises objections of its own.",
            "Every objection must arrive with an experiment that would settle it. One that "
            "does not is not recorded as an objection.",
        ),
        next_up=(
            "An objection left open and marked serious blocks the claim from being promoted, "
            "however good the original numbers looked."
        ),
        desk=Desk(
            tab="objections",
            label="objections",
            lead="What counts as a disagreement here, and what merely counts as doubt.",
            sections=(
                Section(
                    "It has to come with a test",
                    "An objection carries the experiment that would tell it apart from the "
                    "claim it disputes. Without one there is nothing to run and nothing to "
                    "resolve, and the argument would be settled by whoever spoke last.",
                ),
                Section(
                    "A serious objection stops the claim",
                    "While one is open, the finding cannot be promoted to something the "
                    "institution asserts. The block is mechanical; it does not depend on "
                    "anybody remembering to enforce it.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="detectors",
                label="the automatic critics",
                lead="Some objections do not need anybody to think of them.",
                sections=(
                    Section(
                        "The standard ways a result goes wrong",
                        "Detectors run over every claim looking for the failures that recur: "
                        "the effect that vanishes with one point removed, the comparison that "
                        "was never planned, the interval that does not support the sentence "
                        "written about it.",
                    ),
                    Section(
                        "Why have both",
                        "A detector catches what is known to go wrong. The Skeptic is there "
                        "for what is not on that list yet, which is where the interesting "
                        "failures live.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 08
    "blind": Dept(
        plain=(
            "Where the whole experiment is run again by somebody who is not allowed to see "
            "how it went the first time."
        ),
        steps=(
            "The Replicator is handed the plan, and nothing else.",
            "It registers and runs the experiment again from scratch.",
            "Everything it looked at while doing so is recorded, so afterwards anybody can "
            "check that it never saw the original results.",
        ),
        next_up=(
            "A finding that only appears once is a finding about one afternoon. This room is "
            "how the institution finds that out about itself."
        ),
        desk=Desk(
            tab="blindness",
            label="blindness",
            lead="How the blindfold is checked rather than trusted.",
            sections=(
                Section(
                    "Proven, not promised",
                    "Every read the Replicator makes is logged. Its blindness is a property "
                    "of that log — something you can go and look at — rather than an "
                    "assurance that it behaved.",
                ),
                Section(
                    "Why it matters that it is checkable",
                    "Any actor asked not to peek will report that it did not peek. The only "
                    "version of the claim worth anything is the one that does not depend on "
                    "the actor's own testimony.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="audit",
                label="the read log",
                lead="How you check afterwards that somebody did not look.",
                sections=(
                    Section(
                        "Every read, written down",
                        "The Replicator's access goes through a layer that records what it "
                        "asked for. Blindness is then a query over that log, and anybody can "
                        "run it.",
                    ),
                    Section(
                        "The database, not the honour system",
                        "The record shows the Replicator never read a row belonging to the "
                        "original run. That is a different kind of statement from an "
                        "assurance that it did not, and it is the only kind worth having.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 09
    "review": Dept(
        plain=(
            "Where a finished claim would be scored and signed off, or refused. This room is "
            "built and staffed on paper and has never once been used."
        ),
        steps=(
            "A Reviewer would score the claim against the record behind it.",
            "It would admit the claim, or decline to.",
        ),
        next_up=(
            "Nothing, at present. The role has a contract, an input view and a validator, and "
            "no part of the machinery ever calls it. Its switch is set on the arms and "
            "changes nothing when it is flipped, and the reviews table is empty in every run "
            "this project has produced. That was found by drawing this map, not by reviewing "
            "anything, and the room is drawn locked so the page cannot imply otherwise."
        ),
        desk=Desk(
            tab="unbuilt",
            label="why it is dark",
            lead="This department is a hole in the institution, and the page says so.",
            sections=(
                Section(
                    "The switch is real and does nothing",
                    "There is a setting called reviewer. It is written into every registered "
                    "protocol and set on the institutional arms. Turning it on and turning it "
                    "off produce identical behaviour, because nothing downstream reads it.",
                ),
                Section(
                    "Why the room is still on the map",
                    "Leaving it off would make the institution look complete. A department "
                    "that exists on paper and not in fact is a finding about this project, "
                    "and hiding it would be the single most flattering thing this page could "
                    "do.",
                ),
                Section(
                    "How it was found",
                    "By building this map. Every switch was flipped and the resulting "
                    "machinery compared; the ones that made no difference were reported. "
                    "Nobody set out to audit the reviewer.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="consequences",
                label="what its absence costs",
                lead="A missing department is not a missing feature. It changes what the rest can "
                "claim.",
                sections=(
                    Section(
                        "Nothing here blocks anything",
                        "Because no review ever happens, no claim in this institution has "
                        "ever been refused at this stage. Every result that got this far went "
                        "through, and it went through by default rather than by judgement.",
                    ),
                    Section(
                        "Which makes the earlier rooms load-bearing",
                        "The checks that do run -- the objection rules, the blind "
                        "replication, the refusal to let a writer type a number -- are "
                        "carrying the whole weight. That is worth knowing when reading "
                        "anything this institution says.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 10
    "record": Dept(
        plain=(
            "Where an idea's life ends. There are four ways out and they are drawn the same "
            "size, because 'we were wrong' is a finish, not a failure."
        ),
        steps=(
            "An idea leaves by exactly one of four doors.",
            "The exit is written down permanently.",
            "Leaving also produces a follow-up question, so a dead end becomes the next "
            "generation's starting point.",
        ),
        next_up=(
            "No idea in any run this project has recorded has yet reached any of these four "
            "doors. The counters read zero because the machinery that would write those "
            "exits has not been built, not because nothing happened upstream."
        ),
        desk=Desk(
            tab="leaving",
            label="the four doors",
            lead="Four ways an idea can finish, and why none of them is a failure.",
            sections=(
                Section(
                    "Refuted is a result",
                    "An idea shown to be wrong has done its job: the institution now knows "
                    "something it did not know. That door is drawn the same width as the "
                    "door marked institutional, on purpose.",
                ),
                Section(
                    "Inconclusive is a result too",
                    "Finding out that the experiment could not settle the question is worth "
                    "recording. It is also the honest outcome most institutions are worst at "
                    "publishing.",
                ),
                Section(
                    "Every ending starts something",
                    "Leaving emits a follow-up opportunity. The point is that a refutation "
                    "should cost the institution a question, not a programme.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="followups",
                label="what an ending starts",
                lead="Leaving by any of the four doors produces the next question rather than a "
                "full stop.",
                sections=(
                    Section(
                        "A refutation is expensive to buy and cheap to waste",
                        "Somebody paid for the run that showed the idea was wrong. The "
                        "follow-up is how that purchase is kept: what the result rules out, "
                        "and what it now makes worth asking.",
                    ),
                    Section(
                        "It is emitted, not remembered",
                        "The follow-up is written by the transition itself. An institution "
                        "that relies on somebody thinking to write one down is an institution "
                        "that mostly does not.",
                    ),
                ),
            ),
        ),
    ),
    # ---------------------------------------------------------------- the hub
    "control": Dept(
        plain=(
            "The whole institution at once. Nullius is an attempt to build a research "
            "organisation out of software and then find out, with an answer key, whether it "
            "is any good at research. This room is where you can see all of it before "
            "deciding which part to look at."
        ),
        steps=(
            "A question goes in at one end, and ten departments pass it along: somebody "
            "thinks of an idea, somebody decides whether to fund it, somebody writes the "
            "exact plan and locks it, somebody builds it, it runs, somebody reads the "
            "results, somebody attacks them, somebody repeats the whole thing blind, "
            "somebody would sign it off, and it ends up in the record.",
            "Three rooms sit off to the side and are not part of that walk: the money, the "
            "memory, and the sealed data.",
            "One room holds the answers and the institution can never open it. Afterwards, "
            "a scorer opens it and marks the work.",
            "The same questions are run again and again with individual safeguards switched "
            "off, so that the effect of each one can be seen rather than assumed.",
        ),
        next_up=(
            "The honest summary of where this stands: the machinery runs end to end, the "
            "guardrails are enforced by the database rather than by anybody's good "
            "behaviour, and the results on this page were produced under a stand-in model "
            "whose answers do not depend on what it was asked. The institution is real. "
            "What it has so far been asked to think about is not."
        ),
        desk=Desk(
            tab="institution",
            label="what this is",
            lead=(
                "An unusual thing to build, and the reason for building it that way rather "
                "than another way."
            ),
            sections=(
                Section(
                    "The idea",
                    "Most attempts to make software do research grade themselves on whether "
                    "the output reads well. This one plants the answers first. Every "
                    "question in the bank comes from a small made-up world whose rules were "
                    "fixed in advance, so afterwards there is a right answer to compare "
                    "against rather than a panel of opinions.",
                ),
                Section(
                    "Why an institution rather than an assistant",
                    "The failures worth preventing are not failures of cleverness. They are "
                    "choosing the measurement after seeing the results, quietly dropping the "
                    "runs that went badly, and believing a finding nobody tried to break. "
                    "Those are organisational problems, so the answer is an organisation: "
                    "separate roles, with the rules between them enforced by the machinery "
                    "rather than requested.",
                ),
                Section(
                    "What the map is",
                    "Fourteen departments and the building between them. Every room here "
                    "corresponds to something in the code -- the roles come out of one "
                    "enumeration and the stages out of another -- so a room cannot be added "
                    "here to make the institution look more complete than it is. A room "
                    "drawn dark is a part of this project that does not exist yet.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="crew",
                label="who is in the building",
                lead=(
                    "Eleven kinds of actor. Each one is stationed somewhere, and no room "
                    "may claim a role the code does not give it."
                ),
                sections=(
                    Section(
                        "They are deliberately separated",
                        "The one who writes the plan is not the one who reads the results, "
                        "and neither is the one who tries to break them. Splitting the work "
                        "is not politeness -- it is what makes each of the rules between "
                        "them something a machine can check.",
                    ),
                    Section(
                        "One of them is not a person",
                        "The control plane is drawn as a machine wherever it appears, "
                        "including here. It compiles, runs and records; it decides nothing. "
                        "Drawing it with a face would suggest judgement it does not have.",
                    ),
                    Section(
                        "And one of them has never worked a day",
                        "The reviewer is defined, contracted and stationed, and nothing in "
                        "the machinery ever calls it. Its room is drawn locked. That is a "
                        "finding about this project and it is on the map rather than in a "
                        "footnote.",
                    ),
                ),
            ),
            Desk(
                tab="programme",
                label="what has been run",
                lead=(
                    "The same question bank, run repeatedly with different safeguards "
                    "switched on, which is the only way to find out what each one is worth."
                ),
                sections=(
                    Section(
                        "One pipeline, many settings",
                        "Every arm is the same institution with switches thrown -- a "
                        "custodian or not, an adversary or not, a blind replication or not. "
                        "Comparing them is what turns a safeguard from a good intention into "
                        "a measured effect.",
                    ),
                    Section(
                        "Registered before it runs",
                        "The set of arms and what will be compared is written down and "
                        "hashed before the first one starts. The comparison you are reading "
                        "is the comparison that was promised.",
                    ),
                    Section(
                        "And it is allowed to disappoint",
                        "A safeguard that turns out not to help is reported as not helping. "
                        "The project's own claims are held to the rule it holds its "
                        "experiments to, which is the only version of this worth building.",
                    ),
                ),
            ),
            Desk(
                tab="limits",
                label="what this cannot show",
                lead=(
                    "The most useful page in a building like this is the one listing what "
                    "it is not evidence for."
                ),
                sections=(
                    Section(
                        "A planted world is not the world",
                        "Everything scored here happens inside made-up worlds with known "
                        "answers. That is what makes scoring possible and it is also the "
                        "limit: it shows the machinery finds answers that are there, not "
                        "that it would find one nobody had.",
                    ),
                    Section(
                        "The results here came from a stand-in",
                        "Under a mock model whose reply does not depend on the question. The "
                        "institution's machinery is real and so are the verdicts about "
                        "whether it followed its own rules; the thinking is not being "
                        "measured, because there is not any yet.",
                    ),
                    Section(
                        "One implementation, compared with itself",
                        "Every arm is this pipeline with switches moved. That is what makes "
                        "the comparison clean and it means no arm can show this design "
                        "beating a different design, because there is not a different one "
                        "here to beat.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 11
    "vault": Dept(
        plain=(
            "A locked room holding the part of the data nobody in the institution is allowed "
            "to look at. There is no corridor into it, on this map or in the code."
        ),
        steps=(
            "The Custodian holds the sealed portion of the data in a separate process.",
            "It answers a fixed, agreed-in-advance number of questions about it.",
            "Nothing else in the building may compute a figure about that data at all.",
        ),
        next_up=(
            "The limit on questions is the safeguard. Enough queries against a held-out set "
            "and it stops being held out."
        ),
        desk=Desk(
            tab="custody",
            label="custody",
            lead="Why one room has no door.",
            sections=(
                Section(
                    "The database refuses, not the actor",
                    "A figure about the sealed data computed by anyone other than the "
                    "Custodian is rejected by a constraint. It cannot be written down at all, "
                    "so there is no version of this that depends on good behaviour.",
                ),
                Section(
                    "Why keep anything sealed",
                    "Because a method tuned against every piece of data you have will look "
                    "excellent and generalise to nothing. The sealed part is the only "
                    "evidence that is still evidence at the end.",
                ),
                Section(
                    "Why there is no corridor",
                    "The map draws no route into this room because there is no route. That "
                    "is not stylisation — it is the shape of the code, and drawing a door "
                    "there would be the drawing making a claim the architecture denies.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="budget",
                label="the question budget",
                lead="The sealed data answers a fixed number of questions and then it is spent.",
                sections=(
                    Section(
                        "Held out is a quantity, not a state",
                        "Every query against a held-out set leaks a little of it into the "
                        "decisions that follow. Enough queries and it is no longer held out, "
                        "however scrupulously each one was asked.",
                    ),
                    Section(
                        "So the questions are written first",
                        "The Custodian answers preregistered queries. Deciding what to ask "
                        "after seeing the development results is the ordinary way a holdout "
                        "quietly stops being one.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 12
    "treasury": Dept(
        plain=(
            "Where the money is. What a research programme is allowed to spend, what it "
            "actually spent, and what a correct answer ended up costing."
        ),
        steps=(
            "Every programme is given a budget before it starts.",
            "The budget is enforced when work is handed out, not totted up afterwards.",
            "A programme that runs out stops, keeping its plans and its forecasts, with a "
            "row naming what it lost the money to.",
        ),
        next_up=(
            "Cost per correct answer is the number this room exists to make sayable. A "
            "method that is right more often for ten times the money is a different claim "
            "from a method that is simply better."
        ),
        desk=Desk(
            tab="money",
            label="the money",
            lead="How spending is stopped, and what running out looks like.",
            sections=(
                Section(
                    "Refused, and written down",
                    "Being refused for lack of budget is an event in the record, not a "
                    "silent absence. Otherwise a programme that was starved and a programme "
                    "that was never started look identical afterwards.",
                ),
                Section(
                    "Running out is an ending, not a crash",
                    "A programme that exhausts its budget reaches a proper terminal state "
                    "with its registration and its forecasts intact. Its work stays "
                    "readable.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="cost",
                label="what an answer costs",
                lead="The number this room exists to make sayable, and why it is hard to say "
                "honestly.",
                sections=(
                    Section(
                        "Right more often is not the whole claim",
                        "A method that answers more questions correctly for ten times the "
                        "money is a different proposition from one that is simply better. "
                        "Both are worth knowing and only one is usually reported.",
                    ),
                    Section(
                        "Including the ones it got wrong",
                        "Cost per correct answer counts the spending on the wrong answers "
                        "too. A figure that divides only by the successes flatters every "
                        "method that guesses a lot.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 13
    "archive": Dept(
        plain=(
            "The institution's memory. What it already believes, how each belief came to be "
            "believed, and what every finished question left worth asking next."
        ),
        steps=(
            "Every claim keeps a link to the evidence it came from.",
            "That chain can be walked back from any belief to the runs underneath it.",
            "Each finished question leaves follow-ups behind for the next generation.",
        ),
        next_up=(
            "Nobody is stationed in this room. It is read by the rooms upstream rather than "
            "worked in — which is why it is drawn with the lights on and nobody in it."
        ),
        desk=Desk(
            tab="memory",
            label="what is remembered",
            lead="The rule that stops the memory filling up with things nobody checked.",
            sections=(
                Section(
                    "A conclusion needs a parent",
                    "A claim inferred from something else is rejected unless it names the "
                    "evidence it was inferred from. There is no way to add a belief that "
                    "simply appeared.",
                ),
                Section(
                    "A guess cannot become a fact by ageing",
                    "Speculation is stored, and it is stored as speculation forever. It "
                    "cannot be promoted into evidence later, which is the ordinary way an "
                    "institution's memory rots.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="genealogy",
                label="walking a belief back",
                lead="Any claim here can be traced to the runs underneath it, one link at a time.",
                sections=(
                    Section(
                        "Every link is a row",
                        "A belief points at the evidence it came from, which points at the "
                        "runs it came from, which point at the artifacts they produced. "
                        "Nothing in the chain is a footnote somebody wrote by hand.",
                    ),
                    Section(
                        "Which is what makes a retraction possible",
                        "When something underneath turns out to be wrong, the things resting "
                        "on it can be found. Without the chain a bad result is not retracted "
                        "so much as gradually forgotten, along with everything built on it.",
                    ),
                ),
            ),
        ),
    ),
    # -------------------------------------------------------------------- 14
    "oracle": Dept(
        plain=(
            "The answer key. Every question in the bank has a true answer that was planted "
            "before the institution ever saw the question — and the institution can never "
            "read this room."
        ),
        steps=(
            "The true effect of every intervention is decided in advance, including the many "
            "that are exactly zero.",
            "The institution runs without any access to it.",
            "Only the scorer may open it, afterwards, to mark the work.",
        ),
        next_up=(
            "You can see inside this room and the institution cannot. That asymmetry is the "
            "entire experiment: it is what makes 'was it right?' a question with an answer."
        ),
        desk=Desk(
            tab="truth",
            label="ground truth",
            lead="Why an answer key is the only way to score a research institution.",
            sections=(
                Section(
                    "Planted, not judged",
                    "The truth here was not decided by an expert reading the results "
                    "afterwards. It was written into the world the questions come from "
                    "before anybody asked anything, which is why it cannot be argued with.",
                ),
                Section(
                    "Most of the answers are zero",
                    "A bank in which everything works is a bank that rewards enthusiasm. "
                    "Many interventions here genuinely do nothing, and finding that out is a "
                    "correct answer.",
                ),
                Section(
                    "Sealed by construction",
                    "Ground truth is kept where no role-scoped view can reach it, and a test "
                    "proves the isolation holds. The institution is not asked to avoid "
                    "looking; it is unable to.",
                ),
            ),
        ),
        extra=(
            Desk(
                tab="bank",
                label="where the questions come from",
                lead="The questions and their answers are made together, which is what makes "
                "scoring possible.",
                sections=(
                    Section(
                        "The world is built first",
                        "Each question comes from a small simulated world whose rules were "
                        "written down before the question was asked. The true answer is a "
                        "property of those rules, not an opinion about the result.",
                    ),
                    Section(
                        "Which is also its limit",
                        "A planted world is not the real one. What this measures is whether "
                        "the institution's machinery finds an answer that is there -- not "
                        "whether the same machinery would work on a question nobody had the "
                        "answer to.",
                    ),
                ),
            ),
        ),
    ),
}


def numerals() -> list[str]:
    """Every piece of prose in this module that contains a digit.

    Ordinarily empty, and there is a test that says so. The figures on a
    department's brief are filled in by the page from the record, so a number
    written into this file would be a number nobody could check -- the exact
    thing the rest of the station is built to make impossible.
    """
    offenders: list[str] = []

    def check(text: str) -> None:
        if any(character.isdigit() for character in text):
            offenders.append(text)

    for title, plain in PEOPLE.values():
        check(title)
        check(plain)
    for dept in DEPTS.values():
        check(dept.plain)
        check(dept.next_up)
        for step in dept.steps:
            check(step)
        check(dept.desk.lead)
        for section in dept.desk.sections:
            check(section.heading)
            check(section.body)
    return offenders
