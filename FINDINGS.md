# What Nullius found

*Generated from the committed protocols and results by `nullius paper build --markdown`.
Do not edit by hand: CI regenerates this file and fails if it differs.*

Every number below was produced under the **mock** provider.

## The claim under test

> Institutional structure - preregistration, adversarial challenge, independent replication, and evidence-typed memory - improves the accuracy and calibration of autonomous empirical research relative to an unstructured agent, at a measurable cost in compute and tokens.

Across 7 registered protocols, **3
predictions were refuted** and **3 upheld**.

## Every registered protocol, in order

| protocol | hash | arms | items | outcome |
|---|---|---|---|---|
| v1 | `1d4c76d2561e` | 8 | 20 | upheld |
| v2 | `254be687163b` | 8 | 60 | refuted |
| v3 | `9eb8e1e16e79` | 8 | 60 | refuted |
| v4 | `b46bdef334c9` | 9 | 60 | upheld |
| v5 | `6bfaa13661c6` | 9 | 60 | upheld |
| v6 | `9acad59c27a4` | 10 | 60 | refuted |
| v7 | `0dac6ca41fdb` | 10 | 60 | registered, not yet run |

None was edited after registration. Where running one exposed a flaw in it, the fix is a new
registration and the old protocol stays on disk, still verifying, still wrong in the way it
was wrong.

### Protocol v1 — upheld

> **Registered prediction.** B4 captures most of the gain over B3 - that is, adding preregistration and the Custodian to a role-decomposed pipeline improves verdict accuracy more than adding the Skeptic, replication, review and memory do on top of it. If true, the finding is that cheap mechanisms beat expensive agents.

**mechanism (B4-B3) = +0.0500; everything else (B6-B4) = -0.0500; prediction upheld**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | Oracle-null | 1 | 0.45 | 1.00 | 0.45 | 0.250 | 0.00 | 0.00000 |
| B1 \*  | Single-shot | 1 | 0.20 | 1.00 | 0.20 | 0.200 | 0.45 | 0.00269 |
| B2 \*  | Single-agent + loop | 1 | 0.20 | 1.00 | 0.20 | 0.200 | 0.45 | 0.00761 |
| B3 | Multi-role, no adversary | 1 | 0.90 | 1.00 | 0.90 | 0.283 | 0.00 | 0.00722 |
| B4 | B3 + preregistration + custodian | 1 | 0.95 | 1.00 | 0.95 | 0.242 | 0.00 | 0.00683 |
| B5 | B4 + Skeptic | 1 | 0.95 | 1.00 | 0.95 | 0.302 | 0.00 | 0.00683 |
| B6 | Full institution | 1 | 0.90 | 1.00 | 0.90 | 0.316 | 0.00 | 0.00732 |
| B7 | Full - memory | 1 | 0.95 | 1.00 | 0.95 | 0.311 | 0.00 | 0.00693 |
Against the registered baseline `B1`, corrected with
benjamini-hochberg at alpha 0.05 —
5 of 7 survive:

- `B0 − B1` = +0.250, 95% CI [-0.100, +0.550] *(model-dependent)*
- `B2 − B1` = +0.000, 95% CI [+0.000, +0.000] *(model-dependent)*
- `B3 − B1` = +0.700, 95% CI [+0.450, +0.900] *(model-dependent)*
- `B4 − B1` = +0.750, 95% CI [+0.550, +0.900] *(model-dependent)*
- `B5 − B1` = +0.750, 95% CI [+0.550, +0.900] *(model-dependent)*
- `B6 − B1` = +0.700, 95% CI [+0.450, +0.900] *(model-dependent)*
- `B7 − B1` = +0.750, 95% CI [+0.500, +0.950] *(model-dependent)*

The contrasts the ladder was built to make:

- `B4 − B3` on verdict_accuracy = +0.050, 95% CI [+0.000, +0.150] — spans zero
- `B6 − B4` on verdict_accuracy = -0.050, 95% CI [-0.150, +0.000] — spans zero
- `B6 − B7` on verdict_accuracy = -0.050, 95% CI [-0.150, +0.000] — **not interpretable.** These arms differ only in a switch that acts through the model, and under a mock the interval measures two custody draws

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.

### Protocol v2 — refuted

> **Registered prediction.** The mechanism contrast B4 - B3 is positive and its 95% interval excludes zero. Adding preregistration and the Custodian to a role-decomposed pipeline improves verdict accuracy by a margin this design can actually resolve. If it holds, cheap mechanism beats expensive agents; if the interval spans zero, the prediction fails regardless of the point estimate.

**mechanism (B4-B3) = +0.0667, 95% CI [-0.0333, +0.1667]; interval does not exclude zero; prediction refuted**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | Oracle-null | 1 | 0.45 | 1.00 | 0.45 | — | 0.00 | 0.00000 |
| B1 \*  | Single-shot | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00293 |
| B2 \*  | Single-agent + loop | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00830 |
| B3 | Multi-role, no adversary | 1 | 0.60 | 1.00 | 0.60 | 0.203 | 0.00 | 0.01081 |
| B4 | B3 + preregistration + custodian | 1 | 0.67 | 1.00 | 0.67 | 0.164 | 0.00 | 0.00973 |
| B5 | B4 + Skeptic | 1 | 0.62 | 1.00 | 0.62 | 0.151 | 0.00 | 0.01052 |
| B6 | Full institution | 1 | 0.72 | 1.00 | 0.72 | 0.110 | 0.00 | 0.00919 |
| B7 | Full - memory | 1 | 0.70 | 1.00 | 0.70 | 0.101 | 0.00 | 0.00940 |
Against the registered baseline `B0`, corrected with
benjamini-hochberg at alpha 0.05 —
4 of 7 survive:

- `B1 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B2 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B3 − B0` = +0.150, 95% CI [-0.067, +0.350] — spans zero
- `B4 − B0` = +0.217, 95% CI [+0.000, +0.417] — spans zero
- `B5 − B0` = +0.167, 95% CI [-0.033, +0.383] — spans zero
- `B6 − B0` = +0.267, 95% CI [+0.050, +0.450] — excludes zero
- `B7 − B0` = +0.250, 95% CI [+0.050, +0.467] — excludes zero

The contrasts the ladder was built to make:

- `B4 − B3` on verdict_accuracy = +0.067, 95% CI [-0.033, +0.167] — spans zero
- `B6 − B4` on verdict_accuracy = +0.050, 95% CI [-0.050, +0.167] — spans zero
- `B6 − B7` on verdict_accuracy = +0.017, 95% CI [-0.050, +0.083] — **not interpretable.** These arms differ only in a switch that acts through the model, and under a mock the interval measures two custody draws

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.

### Protocol v3 — refuted

> **Registered prediction.** Separating abstention from finding lowers every arm's verdict accuracy, because v2 credited an arm that could say nothing with having said the right thing whenever the truth happened to be 'inconclusive'. The institutional arms will separate on coverage: B6 answers more of the bank than B3 does, and the interval on that difference excludes zero. If coverage does not separate, the institution's advantage is in what it says and not in how much it is able to say.

**mechanism (B4-B3) = +0.0333, 95% CI [-0.0500, +0.1167]; interval does not exclude zero; prediction refuted**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | Oracle-null | 1 | 0.45 | 1.00 | 0.45 | — | 0.00 | 0.00000 |
| B1 \*  | Single-shot | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00293 |
| B2 \*  | Single-agent + loop | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00830 |
| B3 | Multi-role, no adversary | 1 | 0.48 | 0.75 | 0.64 | 0.203 | 0.00 | 0.01341 |
| B4 | B3 + preregistration + custodian | 1 | 0.52 | 0.77 | 0.67 | 0.142 | 0.00 | 0.01255 |
| B5 | B4 + Skeptic | 1 | 0.55 | 0.75 | 0.73 | 0.132 | 0.00 | 0.01179 |
| B6 | Full institution | 1 | 0.57 | 0.78 | 0.72 | 0.117 | 0.00 | 0.01161 |
| B7 | Full - memory | 1 | 0.57 | 0.73 | 0.77 | 0.118 | 0.00 | 0.01161 |
Against the registered baseline `B0`, corrected with
benjamini-hochberg at alpha 0.05 —
2 of 7 survive:

- `B1 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B2 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B3 − B0` = +0.033, 95% CI [-0.167, +0.233] — spans zero
- `B4 − B0` = +0.067, 95% CI [-0.117, +0.250] — spans zero
- `B5 − B0` = +0.100, 95% CI [-0.100, +0.300] — spans zero
- `B6 − B0` = +0.117, 95% CI [-0.083, +0.317] — spans zero
- `B7 − B0` = +0.117, 95% CI [-0.067, +0.300] — spans zero

The contrasts the ladder was built to make:

- `B4 − B3` on verdict_accuracy = +0.033, 95% CI [-0.050, +0.117] — spans zero
- `B6 − B4` on verdict_accuracy = +0.050, 95% CI [-0.067, +0.167] — spans zero
- `B6 − B7` on verdict_accuracy = +0.000, 95% CI [-0.117, +0.117] — **not interpretable.** These arms differ only in a switch that acts through the model, and under a mock the interval measures two custody draws

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.

### Protocol v4 — upheld

> **Registered prediction.** Adaptive seeding raises coverage. B8 abstains on fewer bank items than B6 does, and the 95% interval on that difference excludes zero.

**coverage (B8-B6) = +0.1833, 95% CI [+0.0833, +0.3000]; interval excludes zero; prediction upheld**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | Oracle-null | 1 | 0.45 | 1.00 | 0.45 | — | 0.00 | 0.00000 |
| B1 \*  | Single-shot | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00293 |
| B2 \*  | Single-agent + loop | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00830 |
| B3 | Multi-role, no adversary | 1 | 0.48 | 0.75 | 0.64 | 0.203 | 0.00 | 0.01341 |
| B4 | B3 + preregistration + custodian | 1 | 0.62 | 0.80 | 0.77 | 0.155 | 0.00 | 0.01051 |
| B5 | B4 + Skeptic | 1 | 0.62 | 0.83 | 0.74 | 0.150 | 0.00 | 0.01051 |
| B6 | Full institution | 1 | 0.55 | 0.75 | 0.73 | 0.101 | 0.00 | 0.01196 |
| B7 | Full - memory | 1 | 0.55 | 0.75 | 0.73 | 0.118 | 0.00 | 0.01196 |
| B8 | Full + adaptive seeding | 1 | 0.73 | 0.93 | 0.79 | 0.088 | 0.00 | 0.00944 |
Against the registered baseline `B0`, corrected with
benjamini-hochberg at alpha 0.05 —
3 of 8 survive:

- `B1 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B2 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B3 − B0` = +0.033, 95% CI [-0.167, +0.233] — spans zero
- `B4 − B0` = +0.167, 95% CI [-0.017, +0.350] — spans zero
- `B5 − B0` = +0.167, 95% CI [-0.017, +0.367] — spans zero
- `B6 − B0` = +0.100, 95% CI [-0.100, +0.283] — spans zero
- `B7 − B0` = +0.100, 95% CI [-0.083, +0.283] — spans zero
- `B8 − B0` = +0.283, 95% CI [+0.083, +0.483] — excludes zero

The contrasts the ladder was built to make:

- `B4 − B3` on verdict_accuracy = +0.133, 95% CI [+0.033, +0.233] — excludes zero
- `B6 − B4` on verdict_accuracy = -0.067, 95% CI [-0.183, +0.050] — spans zero
- `B6 − B7` on verdict_accuracy = +0.000, 95% CI [-0.117, +0.117] — **not interpretable.** These arms differ only in a switch that acts through the model, and under a mock the interval measures two custody draws
- `B8 − B6` on coverage = +0.183, 95% CI [+0.083, +0.300] — excludes zero

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.

### Protocol v5 — upheld

> **Registered prediction.** Replication narrows the ladder rather than reordering it. Averaging three custody draws per arm leaves B8 - B6 on coverage positive with an interval still excluding zero, and leaves B4 - B3 on verdict accuracy spanning zero - the contrast that flipped between the v3 and v4 single draws. If B4 - B3 separates under replication, the v4 reading was right and this protocol's caution was wrong.

**coverage (B8-B6) = +0.1222, 95% CI [+0.0444, +0.2111]; interval excludes zero; prediction upheld**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | Oracle-null | 1 | 0.45 | 1.00 | 0.45 | — | 0.00 | 0.00000 |
| B1 \*  | Single-shot | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00293 |
| B2 \*  | Single-agent + loop | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00830 |
| B3 | Multi-role, no adversary | 1 | 0.48 | 0.75 | 0.64 | 0.203 | 0.00 | 0.01343 |
| B4 | B3 + preregistration + custodian | 3 | 0.52 | 0.74 | 0.70 | 0.136 | 0.00 | 0.01242 |
| B5 | B4 + Skeptic | 3 | 0.55 | 0.78 | 0.71 | 0.150 | 0.00 | 0.01179 |
| B6 | Full institution | 3 | 0.53 | 0.74 | 0.71 | 0.111 | 0.00 | 0.01249 |
| B7 | Full - memory | 3 | 0.57 | 0.76 | 0.76 | 0.121 | 0.00 | 0.01151 |
| B8 | Full + adaptive seeding | 3 | 0.72 | 0.87 | 0.83 | 0.090 | 0.00 | 0.00971 |
Against the registered baseline `B0`, corrected with
benjamini-hochberg at alpha 0.05 —
3 of 8 survive:

- `B1 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B2 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B3 − B0` = +0.033, 95% CI [-0.167, +0.233] — spans zero
- `B4 − B0` = +0.072, 95% CI [-0.106, +0.244] — spans zero
- `B5 − B0` = +0.100, 95% CI [-0.078, +0.289] — spans zero
- `B6 − B0` = +0.078, 95% CI [-0.117, +0.261] — spans zero
- `B7 − B0` = +0.122, 95% CI [-0.056, +0.311] — spans zero
- `B8 − B0` = +0.267, 95% CI [+0.094, +0.439] — excludes zero

The contrasts the ladder was built to make:

- `B4 − B3` on verdict_accuracy = +0.039, 95% CI [-0.056, +0.133] — spans zero
- `B6 − B4` on verdict_accuracy = +0.006, 95% CI [-0.056, +0.067] — spans zero
- `B6 − B7` on verdict_accuracy = -0.044, 95% CI [-0.106, +0.011] — **not interpretable.** These arms differ only in a switch that acts through the model, and under a mock the interval measures two custody draws
- `B8 − B6` on coverage = +0.122, 95% CI [+0.044, +0.211] — excludes zero

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.

### Protocol v6 — refuted

> **Registered prediction.** Sizing the escalation from an upper bound on the noise rather than a point estimate raises coverage. B9 abstains on fewer bank items than B8 does, and the 95% interval on that difference excludes zero. It should also cost more per item, because a bound that errs towards more data buys more data; if cost per correct claim rises without coverage improving, the bound is only expensive.

**coverage (B9-B8) = +0.0333, 95% CI [-0.0111, +0.0889]; interval does not exclude zero; prediction refuted**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
| B0 | Oracle-null | 1 | 0.45 | 1.00 | 0.45 | — | 0.00 | 0.00000 |
| B1 \*  | Single-shot | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00293 |
| B2 \*  | Single-agent + loop | 1 | 0.18 | 1.00 | 0.18 | 0.197 | 0.45 | 0.00830 |
| B3 | Multi-role, no adversary | 1 | 0.48 | 0.75 | 0.64 | 0.203 | 0.00 | 0.01343 |
| B4 | B3 + preregistration + custodian | 3 | 0.52 | 0.74 | 0.70 | 0.136 | 0.00 | 0.01243 |
| B5 | B4 + Skeptic | 3 | 0.55 | 0.78 | 0.71 | 0.150 | 0.00 | 0.01181 |
| B6 | Full institution | 3 | 0.53 | 0.74 | 0.71 | 0.111 | 0.00 | 0.01250 |
| B7 | Full - memory | 3 | 0.57 | 0.76 | 0.76 | 0.121 | 0.00 | 0.01151 |
| B8 | Full + adaptive seeding | 3 | 0.72 | 0.87 | 0.83 | 0.090 | 0.00 | 0.00971 |
| B9 | Adaptive + conservative sizing | 3 | 0.67 | 0.90 | 0.75 | 0.096 | 0.00 | 0.01035 |
Against the registered baseline `B0`, corrected with
benjamini-hochberg at alpha 0.05 —
3 of 9 survive:

- `B1 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B2 − B0` = -0.267, 95% CI [-0.450, -0.067] *(model-dependent)*
- `B3 − B0` = +0.033, 95% CI [-0.167, +0.233] — spans zero
- `B4 − B0` = +0.072, 95% CI [-0.106, +0.244] — spans zero
- `B5 − B0` = +0.100, 95% CI [-0.078, +0.289] — spans zero
- `B6 − B0` = +0.078, 95% CI [-0.117, +0.261] — spans zero
- `B7 − B0` = +0.122, 95% CI [-0.056, +0.311] — spans zero
- `B8 − B0` = +0.267, 95% CI [+0.094, +0.439] — excludes zero
- `B9 − B0` = +0.222, 95% CI [+0.033, +0.411] — excludes zero

The contrasts the ladder was built to make:

- `B4 − B3` on verdict_accuracy = +0.039, 95% CI [-0.056, +0.133] — spans zero
- `B6 − B4` on verdict_accuracy = +0.006, 95% CI [-0.056, +0.067] — spans zero
- `B6 − B7` on verdict_accuracy = -0.044, 95% CI [-0.106, +0.011] — **not interpretable.** These arms differ only in a switch that acts through the model, and under a mock the interval measures two custody draws
- `B9 − B8` on coverage = +0.033, 95% CI [-0.011, +0.089] — spans zero

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.

### Protocol v7 — registered, not yet run

> **Registered prediction.** V6's prediction, re-registered against an arm that implements it. Sizing the escalation from an upper bound on the noise rather than a point estimate raises coverage: B9 abstains on fewer bank items than B8 does, and the 95% interval on that difference excludes zero. It should also cost more per item, because a bound that errs towards more data buys more data; if cost per correct claim rises without coverage improving, the bound is only expensive.

Registered, not yet run.

## The question bank

Ground truth is planted, not judged. The oracle measures each item's true effect at forty
seeds of twenty thousand samples and resolves it to about 0.0008; an experiment gets five
seeds of two thousand and resolves it to about 0.00348. An item can therefore sit
inside one *experiment* standard error of a verdict boundary while staying several *oracle*
standard errors clear of it — hard to answer, and not in doubt.

| bank | items | null | within 1 SE | within 2 SE | metric resolution |
|---|---|---|---|---|---|
| v1 | 20 | 45% | 3 | 6 | 0.050 |
| v2 | 60 | 45% | 20 | 37 | 0.017 |

## What running a protocol found wrong with it

Each was discovered by executing a preregistered plan rather than by reviewing one. This is
the section a written-up-afterwards paper would not have, because in that genre the flaws are
fixed before anything is published.

1. **The baseline arm was model-dependent.** v1 registered B1, a single-shot agent, as the arm everything was compared against. Under a mock provider B1's behaviour is a property of the mock, so every comparison in the registered family was uninterpretable as evidence about mechanism. v2 moved the baseline to B0, which answers without looking and cannot depend on a model at all. (M12b)
2. **The prediction was adjudicated on two point estimates.** v1's rule compared B4 minus B3 against B6 minus B4 and returned 'upheld' for a one-item difference on a twenty-item bank, where one item is 0.05. v2 required the interval to exclude zero, and the same data then refuted the prediction v1 had upheld. (M12b)
3. **Calibration was scored on a quantity the rubric does not measure.** The confidence rubric measures evidence for an effect, so a correct 'no effect' answer necessarily carries weak evidence and was scored as gross underconfidence. v2 restricted Brier and calibration error to items where the arm asserted an effect, which is the subpopulation where the rubric's quantity and the scored outcome are the same quantity. (M12b)
4. **Abstention was scored as an answer, and sometimes as a correct one.** One verdict value meant both 'the effect is real and smaller than claimed' and 'the interval is too wide to say anything'. Because the first is a real truth value in this bank, an arm that could say nothing was credited with a correct answer whenever the truth happened to be that value. Every arm's accuracy was inflated, unevenly, by four to nine items in sixty. v3 split the verdict; 'underpowered' is never a truth, so an abstention can no longer be scored correct by accident. (M13)
5. **A prediction and its adjudication rule described different quantities.** v3 registered a prediction about coverage and inherited a rule that tested accuracy, so the run reported a verdict after measuring something the prediction did not mention. It was right by accident. v4 stores the adjudicated contrast as data — treatment, baseline, quantity, direction — and derives the verdict from it, so the two cannot be edited apart. (M13b)
6. **A single custody draw cannot support the contrasts being measured.** Arms B0 to B7 ran twice, under v3 and again under v4. The four uncustodied arms returned identical results to three decimals; every custodied arm moved, by up to 0.100 — six times the metric's resolution — because the Custodian derives its evaluation seed from the registration id and draws a fresh holdout each run. One contrast, B4 minus B3, flipped from spanning zero to excluding it on the same bank. v5 replicates every custodied arm three times. (M14b)
7. **A null result was reported for a mechanism that could not have acted.** Memory adds recalled claims to the Theorist's view, so it can only act by changing what a model writes. The mock's response is byte-identical with and without them. B6 minus B7 therefore measured the difference between two custody draws, and was reported as memory's contribution across protocols v1 to v5 — four registered protocols carrying a null result for a switch that was delivered and discarded. B1 and B2 were labelled model-dependent from the start for exactly this reason; memory was not. Contrasts whose arms differ only in a model-mediated switch are now labelled uninterpretable rather than printed as intervals. (M20)
8. **A protocol was adjudicated against an arm that did not implement its mechanism.** B9 is B8 with conservative escalation sizing. The switch was declared on the arm, hashed into v6, translated into the kernel's mechanism set and handed to a parameter that no line read; neither call to the escalation passed it at all. So B9 sized every escalation from the point estimate exactly as B8 did, and bought an identical number of seeds on all one hundred and eighty outcomes — not similar, identical. V6's refutation of conservative sizing is therefore a measurement of two custody draws of one arm, and stands on the record as that. Its one gain is unplanned: B9 minus B8 is a negative control with a true difference of zero, which puts a number on this ladder's noise floor. V7 re-registers the prediction against a switch that is connected, and the wiring is now checked by lint over every argument the kernel accepts and by a test that runs both arms and compares what they bought. (M23)

## Limitations

- Every result was produced under a mock provider. The institution's machinery — the compiler, the sandbox, the Custodian, the statistics, the confidence rubric — is real and so are the verdicts, but the prose each role emits is canned. Arms B1 and B2 are dominated by that prose and are reported as describing the mock, and any contrast whose arms differ only in a switch that acts through the model — memory, iteration — is labelled uninterpretable rather than reported as a measurement. Nothing here says whether memory helps a real institution; it says this benchmark cannot find out without a real provider.
- The bank is sixty synthetic items from one data generating process. The population these results generalise to is 'questions like these', which is the only population sixty items of one family can speak for.
- Cost is measured in real token counts priced as if a named model had produced them, because the mock is free and a cost-per-correct-claim whose numerator is identically zero ranks nothing. Compute cost is not substituted; those seconds were burned.
- The comparison holds the science fixed and varies the mechanism. It therefore measures what each mechanism buys given a fixed research design, and not how a mechanism might change the design an institution chooses in the first place.
- No result here has been replicated across independent implementations. The replication reported is of runs, not of the system.
- Protocol v6 is reported with the outcome it produced, and that outcome is not evidence about the mechanism v6 names. Its treatment arm did not implement conservative escalation sizing, so the contrast it adjudicated is a null one. The row is kept because deleting a registered protocol's result is the thing this project exists to make impossible; read it as the noise floor it measures and not as the finding it was registered to make.

## Provenance

| protocol | hash | bank items | truth lock |
|---|---|---|---|
| v1 | `1d4c76d2561e61e3c77998a1` | `c4d90bb633190a86` | `4c2ac5de66e0b751` |
| v2 | `254be687163bf805ff9573f9` | `d4d1766b0f87c89d` | `8b459a6cc67b41ae` |
| v3 | `9eb8e1e16e793b64250c5607` | `d4d1766b0f87c89d` | `8b459a6cc67b41ae` |
| v4 | `b46bdef334c9e6d4f298388a` | `d4d1766b0f87c89d` | `8b459a6cc67b41ae` |
| v5 | `6bfaa13661c63f3d5aca3c33` | `d4d1766b0f87c89d` | `8b459a6cc67b41ae` |
| v6 | `9acad59c27a47affc0354911` | `d4d1766b0f87c89d` | `8b459a6cc67b41ae` |
| v7 | `0dac6ca41fdb90c4825f5939` | `d4d1766b0f87c89d` | `8b459a6cc67b41ae` |

Every figure above is read from a results file whose stored summary re-scores from its own
per-item rows, and every prediction from a protocol whose hash is in the git history. The
generator refuses to run if any of that stops checking out.
