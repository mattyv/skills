# Evaluation status for hunch 1.1.0

This release establishes inspectable challenge records, immutable prospective forecasts, independent target resolution, and correct Brier scoring. Tests establish those software behaviors; they do not establish that required challenges improve diagnostic accuracy or calibration.

## Established behavior

- New and regenerated hypotheses require a challenge, and OTHER requires a separate candidate-set challenge.
- Observed challenges can cite only observations already recorded in the same situation. Challenge revisions preserve prior text and do not alter probabilities.
- Complete likelihood matrices are required by default. Rejected matrices leave the ledger unchanged; explicit compatibility mode records incomplete snapshots.
- Forecasts freeze their probability vector, evidence cutoff, scoring inputs, engine version, and configuration under a stable hash. Later observations and rescoring do not alter them.
- Independently recorded outcomes score only eligible pre-outcome forecasts. Binary Brier loss uses range 0-1; categorical summed Brier loss uses range 0-2, and reports keep these conventions separate.
- Schema-1 ledgers remain readable. Explicit migration creates a backup and labels historical posterior entries without matrices as non-reconstructable.

## Evidence and limits

The completed eight-case Terra pilot reported future-probe Brier loss of 0.2996 for Hunch and 0.2923 for challenged Hunch, while both Hunch variants had 25% diagnosis accuracy. The sample used one conversation per arm, contained no realised cache-race cases and four OTHER cases, and all methods scored worse than a post-hoc constant-50% baseline on the primary binary outcome. This supports the inspectability change and a larger evaluation; it does not establish a general accuracy or calibration benefit.

The elicitation protocol remains a proposal. This release does not claim that reference-class prompts, symmetric frequency questions, blinded elicitation, or sensitivity ranges improve estimates. Probability defaults and reliability weights remain unchanged.

## Next evaluation

Use the same model, information, and investigation budget across ordinary reasoning, a challenged notebook, Hunch, and challenged Hunch. Prespecify the challenged-Hunch versus Hunch contrast, constant and historical-base-rate comparators, sample size, stopping rule, exclusions, and paired case-level analysis. Split by incident family, keep evaluator answers separate, randomise execution order, repeat independent model runs, and preserve actual model versions and usage. Evaluate the elicitation protocol separately on untouched held-out cases.
