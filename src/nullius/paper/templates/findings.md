# What Nullius found

*Generated from the committed protocols and results by `nullius paper build --markdown`.
Do not edit by hand: CI regenerates this file and fails if it differs.*

Every number below was produced under the **{{ paper.provider }}** provider.

## The claim under test

> {{ paper.claim }}

Across {{ paper.chapters | length }} registered protocols, **{{ paper.predictions_refuted }}
predictions were refuted** and **{{ paper.predictions_upheld }} upheld**.

## Every registered protocol, in order

| protocol | hash | arms | items | outcome |
|---|---|---|---|---|
{% for c in paper.chapters -%}
| v{{ c.version }} | `{{ c.protocol.protocol_hash[:12] }}` | {{ c.n_arms }} | {{ c.protocol.bank['n_items'] }} | {{ c.verdict }} |
{% endfor %}

None was edited after registration. Where running one exposed a flaw in it, the fix is a new
registration and the old protocol stays on disk, still verifying, still wrong in the way it
was wrong.

{% for c in paper.chapters %}
### Protocol v{{ c.version }} — {{ c.verdict }}

> **Registered prediction.** {{ c.protocol.prediction }}

{% if c.report -%}
**{{ c.report.prediction_reason }}**

| arm | | n | acc | coverage | answered | brier | fdr | $/correct |
|---|---|---|---|---|---|---|---|---|
{% for m in c.report.metrics -%}
| {{ m.arm_id }}{% if m.model_dependent %} \* {% endif %} | {{ m.label }} | {{ m.n_replicates }} | {{ num(m.verdict_accuracy, 2) }} | {{ num(m.coverage, 2) }} | {{ num(m.assertion_accuracy, 2) }} | {{ num(m.brier) }} | {{ num(m.false_discovery_rate, 2) }} | {{ num(m.usd_per_correct_claim, 5) }} |
{% endfor %}
{% if c.report.comparisons -%}
Against the registered baseline `{{ c.protocol.statistics['baseline_arm'] }}`, corrected with
{{ c.protocol.statistics['multiplicity'] }} at alpha {{ c.protocol.statistics['alpha'] }} —
{{ c.report.correction.n_rejected }} of {{ c.report.comparisons | length }} survive:

{% for comp in c.report.comparisons -%}
- `{{ comp.arm_id }} − {{ comp.baseline_arm_id }}` = {{ '%+.3f' | format(comp.difference) }}, 95% CI [{{ '%+.3f' | format(comp.ci_low) }}, {{ '%+.3f' | format(comp.ci_high) }}]{{ note(comp) }}
{% endfor %}
{%- endif %}
{% if c.report.prediction_contrasts %}

The contrasts the ladder was built to make:

{% for comp in c.report.prediction_contrasts -%}
- `{{ comp.arm_id }} − {{ comp.baseline_arm_id }}` on {{ comp.metric }} = {{ '%+.3f' | format(comp.difference) }}, 95% CI [{{ '%+.3f' | format(comp.ci_low) }}, {{ '%+.3f' | format(comp.ci_high) }}]{{ note(comp) }}
{% endfor %}
{%- endif %}

\* behaviour dominated by the language model. Under a mock provider these arms describe the
mock and not a model, and no claim about mechanism rests on them.
{%- else %}
Registered, not yet run.
{% endif %}
{% endfor %}

## The question bank

Ground truth is planted, not judged. The oracle measures each item's true effect at forty
seeds of twenty thousand samples and resolves it to about 0.0008; an experiment gets five
seeds of two thousand and resolves it to about {{ measured_se }}. An item can therefore sit
inside one *experiment* standard error of a verdict boundary while staying several *oracle*
standard errors clear of it — hard to answer, and not in doubt.

| bank | items | null | within 1 SE | within 2 SE | metric resolution |
|---|---|---|---|---|---|
{% for b in paper.banks -%}
| {{ b.name }} | {{ b.n_items }} | {{ '%.0f' | format(b.null_fraction * 100) }}% | {{ b.within_one_se }} | {{ b.within_two_se }} | {{ '%.3f' | format(b.resolution) }} |
{% endfor %}

## What running a protocol found wrong with it

Each was discovered by executing a preregistered plan rather than by reviewing one. This is
the section a written-up-afterwards paper would not have, because in that genre the flaws are
fixed before anything is published.

{% for flaw in flaws %}
{{ loop.index }}. **{{ flaw.title }}** {{ flaw.body | striptags }}
{% endfor %}

## Limitations

{% for limitation in limitations %}
- {{ limitation }}
{% endfor %}

## Provenance

| protocol | hash | bank items | truth lock |
|---|---|---|---|
{% for c in paper.chapters -%}
| v{{ c.version }} | `{{ c.protocol.protocol_hash[:24] }}` | `{{ c.protocol.bank['items_hash'][:16] }}` | `{{ c.protocol.bank['truth_lock_hash'][:16] }}` |
{% endfor %}

Every figure above is read from a results file whose stored summary re-scores from its own
per-item rows, and every prediction from a protocol whose hash is in the git history. The
generator refuses to run if any of that stops checking out.
