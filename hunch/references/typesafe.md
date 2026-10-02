# Optional TypeSafe workflow

Use the installed typesafe-ai skill and its current documentation for API calls.
Jev supplies typed judgments, not explanations; the host agent still supplies
hypotheses, challenges, and evidence-grounded rationales.

## Default: a separate forecast

At a useful evidence checkpoint, ask one Choice over the fixed, mutually exclusive
candidate explanations, including OTHER. Supply their statements and the same
cutoff-limited observations, with provenance and event grouping. Omit the current
posterior, preferred answer, and any known outcome to reduce anchoring. When causes
can coexist, define exclusive joint scenarios or choose a binary target instead.
For a binary observable target, use Noul and map its returned value to
{"yes": p, "no": 1-p}. Do not use Score or confidence as an event probability.

Validate that returned values are finite numbers in [0,1], labels match exactly,
and the full distribution sums to one. Missing labels, malformed values, or service
failure trigger the normal Hunch fallback, not invented or neutral-filled answers.
Batch independent checks sharing the same evidence where useful; do not loop until
the service agrees with the leading hypothesis.

For an unresolved target with a defined horizon and verification rule, save the
actual returned distribution through the existing forecast command:
- kind: categorical for Choice, binary for Noul.
- outcomeLabels and probabilities: the exact target labels and validated values.
- probabilitySource: modelElicited, never empiricallyEstimated.
- targetId, target, horizon, resolutionCriteria: define before seeing the outcome.
- cutoffAt and evidenceCutoffAt: the actual evidence checkpoint.
- elicitationNotes: record provider TypeSafe, the returned model version, exact
  state/questions/raw response (or durable artifact paths and hashes), observation
  IDs, usage when available, and any assumptions. The snapshot's engineVersion
  identifies Hunch, not Jev.

Keep the Jev forecast separate from Hunch's posterior and label its source when
presenting it. Do not average them or use one as extra evidence for the other.
For comparisons, use the same target, labels, evidence, and checkpoint; retain the
forecast IDs and compare their individual scores. The current forecast-report
pools records and does not provide provider-stratified or paired evaluation.
Do not interpret that pooled average as evidence that TypeSafe improves Hunch.

If the outcome is already known or timing cannot be established, do not call the
result prospective. Mark any historical import retrospectiveImport: true.
TypeSafe's agreement is not ground truth: target resolution still needs observed
outcome evidence. Confidence summarizes distribution concentration and is not a
separate truth signal or a source-reliability weight.

## Evidence checks

Use focused Choice questions such as: does this source support, contradict, or
leave this challenge unresolved? Include the actual source and observation IDs.
Inspect the cited source before updating a challenge with the challenge command.
Jev's answer is a model judgment, never a new firsthand observation. Multiple
questions or models examining one event do not create independent evidence.

## Conditional likelihoods: experimental only

Do not place a diagnosis distribution P(H|E) in a rescore matrix, which expects
P(E|H). The default optional workflow leaves likelihood elicitation with the host
agent. If the user requests a likelihood-estimation experiment, define the event,
measurement process, reference class, and conditioning hypothesis explicitly.
Ask symmetric questions under each candidate, without presenting the event as
already observed in the hypothetical trial. Record assumptions and model provenance
in cell rationales; do not invent an OTHER likelihood or normalize likelihoods
across hypotheses. Use only a complete validated matrix with rescore.

## Sources

- [Choice](https://docs.typesafe.ai/primitives/choice)
- [Noul](https://docs.typesafe.ai/primitives/noul)
- [Confidence](https://docs.typesafe.ai/confidence)
- [API](https://docs.typesafe.ai/api)
- [Citation checks](https://docs.typesafe.ai/cookbooks/citation_check)
