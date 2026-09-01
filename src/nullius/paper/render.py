"""Rendering the paper.

Everything numeric comes from :mod:`nullius.paper.model`, which reads it from
committed protocols and results. Two lists here are prose — the flaws and the
limitations — and they are the only hand-written content in the document.

They are declared as data rather than woven into the template so that they can
be read, counted and checked in one place, and each names the commit that
records the event it describes. A reader who doubts one can go and look.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from nullius.paper.model import MEASURED_EXPERIMENT_SE, Paper, assemble

__all__ = [
    "FLAWS",
    "LIMITATIONS",
    "Flaw",
    "environment",
    "render_findings",
    "write_findings",
    "write_paper",
]


@dataclass(frozen=True, slots=True)
class Flaw:
    """One thing running a protocol revealed about that protocol."""

    title: str
    body: str


#: The flaws, in the order they were found. Each is recorded in ``BUILD_PLAN.md``
#: and in the commit named at the end of its entry.
FLAWS: tuple[Flaw, ...] = (
    Flaw(
        "The baseline arm was model-dependent.",
        "v1 registered B1, a single-shot agent, as the arm everything was compared "
        "against. Under a mock provider B1's behaviour is a property of the mock, so "
        "every comparison in the registered family was uninterpretable as evidence "
        "about mechanism. v2 moved the baseline to B0, which answers without looking "
        "and cannot depend on a model at all. (M12b)",
    ),
    Flaw(
        "The prediction was adjudicated on two point estimates.",
        "v1's rule compared B4 minus B3 against B6 minus B4 and returned 'upheld' for "
        "a one-item difference on a twenty-item bank, where one item is 0.05. v2 "
        "required the interval to exclude zero, and the same data then refuted the "
        "prediction v1 had upheld. (M12b)",
    ),
    Flaw(
        "Calibration was scored on a quantity the rubric does not measure.",
        "The confidence rubric measures evidence <em>for an effect</em>, so a correct "
        "'no effect' answer necessarily carries weak evidence and was scored as gross "
        "underconfidence. v2 restricted Brier and calibration error to items where the "
        "arm asserted an effect, which is the subpopulation where the rubric's quantity "
        "and the scored outcome are the same quantity. (M12b)",
    ),
    Flaw(
        "Abstention was scored as an answer, and sometimes as a correct one.",
        "One verdict value meant both 'the effect is real and smaller than claimed' and "
        "'the interval is too wide to say anything'. Because the first is a real truth "
        "value in this bank, an arm that could say nothing was credited with a correct "
        "answer whenever the truth happened to be that value. Every arm's accuracy was "
        "inflated, unevenly, by four to nine items in sixty. v3 split the verdict; "
        "'underpowered' is never a truth, so an abstention can no longer be scored "
        "correct by accident. (M13)",
    ),
    Flaw(
        "A prediction and its adjudication rule described different quantities.",
        "v3 registered a prediction about coverage and inherited a rule that tested "
        "accuracy, so the run reported a verdict after measuring something the "
        "prediction did not mention. It was right by accident. v4 stores the adjudicated "
        "contrast as data — treatment, baseline, quantity, direction — and derives the "
        "verdict from it, so the two cannot be edited apart. (M13b)",
    ),
    Flaw(
        "A single custody draw cannot support the contrasts being measured.",
        "Arms B0 to B7 ran twice, under v3 and again under v4. The four uncustodied arms "
        "returned identical results to three decimals; every custodied arm moved, by up "
        "to 0.100 — six times the metric's resolution — because the Custodian derives its "
        "evaluation seed from the registration id and draws a fresh holdout each run. "
        "One contrast, B4 minus B3, flipped from spanning zero to excluding it on the "
        "same bank. v5 replicates every custodied arm three times. (M14b)",
    ),
    Flaw(
        "A null result was reported for a mechanism that could not have acted.",
        "Memory adds recalled claims to the Theorist's view, so it can only act by "
        "changing what a model writes. The mock's response is byte-identical with "
        "and without them. B6 minus B7 therefore measured the difference between "
        "two custody draws, and was reported as memory's contribution across "
        "protocols v1 to v5 — four registered protocols carrying a null result for "
        "a switch that was delivered and discarded. B1 and B2 were labelled "
        "model-dependent from the start for exactly this reason; memory was not. "
        "Contrasts whose arms differ only in a model-mediated switch are now "
        "labelled uninterpretable rather than printed as intervals. (M20)",
    ),
)

#: What this document cannot support, stated where a reader will meet it.
LIMITATIONS: tuple[str, ...] = (
    "Every result was produced under a mock provider. The institution's machinery — "
    "the compiler, the sandbox, the Custodian, the statistics, the confidence rubric — "
    "is real and so are the verdicts, but the prose each role emits is canned. Arms B1 "
    "and B2 are dominated by that prose and are reported as describing the mock, and "
    "any contrast whose arms differ only in a switch that acts through the model — "
    "memory, iteration — is labelled uninterpretable rather than reported as a "
    "measurement. Nothing here says whether memory helps a real institution; it says "
    "this benchmark cannot find out without a real provider.",
    "The bank is sixty synthetic items from one data generating process. The population "
    "these results generalise to is 'questions like these', which is the only population "
    "sixty items of one family can speak for.",
    "Cost is measured in real token counts priced as if a named model had produced them, "
    "because the mock is free and a cost-per-correct-claim whose numerator is identically "
    "zero ranks nothing. Compute cost is not substituted; those seconds were burned.",
    "The comparison holds the science fixed and varies the mechanism. It therefore "
    "measures what each mechanism buys given a fixed research design, and not how a "
    "mechanism might change the design an institution chooses in the first place.",
    "No result here has been replicated across independent implementations. The "
    "replication reported is of runs, not of the system.",
)


def _num(value: float, places: int = 3) -> str:
    """A number, or an em dash where the quantity is genuinely undefined."""
    if value != value or value in (float("inf"), float("-inf")):
        return "—"
    return f"{value:.{places}f}"


def _note(comparison: object) -> str:
    """The annotation that follows a contrast, as a plain string.

    A string rather than an inline ``{% if %}`` because Jinja's ``trim_blocks``
    eats the newline after a block tag, and a Markdown bullet whose line ends
    with one loses its line break. Every contrast list in the committed
    findings had been rendering as a single run-on line since M18 for exactly
    that reason, and the CI drift check could not see it: that check verifies
    the file matches the generator, and both were wrong in the same way.
    A consistency check is not a correctness check.
    """
    if getattr(comparison, "model_mediated", False):
        return (
            " — **not interpretable.** These arms differ only in a switch that acts "
            "through the model, and under a mock the interval measures two custody draws"
        )
    if getattr(comparison, "model_dependent", False):
        return " *(model-dependent)*"
    return " — excludes zero" if getattr(comparison, "separates", False) else " — spans zero"


def environment() -> Environment:
    env = Environment(
        loader=PackageLoader("nullius.paper", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["num"] = _num
    env.globals["note"] = _note
    return env


def _context(paper: Paper) -> dict[str, object]:
    return {
        "paper": paper,
        "flaws": FLAWS,
        "limitations": LIMITATIONS,
        "measured_se": f"{MEASURED_EXPERIMENT_SE:.5f}",
    }


def render_findings(paper: Paper | None = None, *, strict: bool = True) -> str:
    """The findings as Markdown, for the front of the repository.

    A second rendering of the same assembled record rather than a second
    account of it. The HTML paper and this file cannot disagree, because
    neither of them contains a number that the other had to be told about.
    """
    paper = paper or assemble(strict=strict)
    body = environment().get_template("findings.md").render(**_context(paper))
    # Collapse the runs of blank lines that block-level Jinja tags leave
    # behind, so the committed file is stable enough for CI to diff.
    lines = body.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def write_findings(out: Path, *, paper: Paper | None = None, strict: bool = True) -> Path:
    """Write ``FINDINGS.md``. CI regenerates this and fails on any difference."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_findings(paper, strict=strict), encoding="utf-8")
    return out


def write_paper(out: Path, *, paper: Paper | None = None, strict: bool = True) -> Path:
    """Render the paper to ``out`` and return the path written."""
    paper = paper or assemble(strict=strict)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        environment().get_template("paper.html").render(**_context(paper)),
        encoding="utf-8",
    )
    return out
