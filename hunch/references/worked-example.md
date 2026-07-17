# Worked example: "Who killed Dr. Black?"

This walks the full protocol end to end on the toy Cluedo scenario also used
by `python3 hunch.py demo` (see `hunch.py`'s `_cmd_demo` for the executable
version). Read this when you want to see WHY each step exists, not just
what command to type.

## 1. Open the situation

```bash
python3 hunch.py open --question "Who killed Dr. Black?"
```

```json
{
  "id": "sit-1",
  "question": "Who killed Dr. Black?",
  "status": "open",
  "hypotheses": [
    { "id": "other", "statement": "The true explanation is not in the current candidate set", "prior": 0.15, "posterior": 0.15, "status": "active" }
  ],
  ...
}
```

Notice OTHER already exists at 15% before you've proposed anything. That
mass is reserved for "the answer isn't one of my hypotheses" — it's not
something you set, it's structural.

## 2. Propose hypotheses

Four suspects, each with a `statement` and `predictedEvidence` — the things
you'd expect to actually observe if that suspect did it. The priors below
deliberately don't sum to `0.85` (they're all `0.5`) to demonstrate that the
engine renormalizes them, not you:

```bash
python3 hunch.py hypotheses sit-1 --json '[
  {"statement": "Colonel Mustard did it in the library with the candlestick",
   "prior": 0.5, "predictedEvidence": ["Mustard seen near library", "candlestick moved"]},
  {"statement": "Professor Plum did it in the study with the revolver",
   "prior": 0.5, "predictedEvidence": ["Plum seen near study", "revolver fired"]},
  {"statement": "Miss Scarlett did it in the lounge with the rope",
   "prior": 0.5, "predictedEvidence": ["Scarlett seen near lounge", "rope missing"]},
  {"statement": "Mrs. Peacock did it in the kitchen with the lead pipe",
   "prior": 0.5, "predictedEvidence": ["Peacock seen near kitchen", "lead pipe missing"]}
]'
```

Each hypothesis ends up with prior `0.85 / 4 = 0.2125` (equal weights,
renormalized to sum to `1 - 0.15`). OTHER stays at `0.15`. Total: `1.0`.

## 3. Log mixed-quality observations

Real evidence isn't uniformly reliable. Log it honestly:

```bash
python3 hunch.py observe sit-1 --text "Someone heard a scream near the library" --source-type secondhand
# -> obs-1, c-1 (new cluster)

python3 hunch.py observe sit-1 --text "A rumor says Mustard was seen with a candlestick" --source-type rumor
# -> obs-2, c-2 (new cluster)

python3 hunch.py observe sit-1 --text "Someone heard a scream close to the library" --same-event-as obs-1
# -> obs-3, c-1 (SAME cluster as obs-1 — this is a restatement, not new evidence)
```

The third call uses `--same-event-as obs-1` because it's clearly the same
underlying event described twice — without that hint, letting it count as
independent evidence would double-weight one scream into two data points.

## 4. Score an ambiguous matrix — expect flat/none

At this point the evidence (a secondhand scream, a rumor) is weak and
doesn't clearly favor anyone. Score everyone at 0.5 across both clusters:

```bash
python3 hunch.py clusters sit-1
# -> [{"id": "c-1", ...}, {"id": "c-2", ...}]

python3 hunch.py rescore sit-1 --json '[
  {"clusterId": "c-1", "likelihoods": [
    {"hypothesisId": "h-1", "likelihood": 0.5, "rationale": "inconclusive"},
    {"hypothesisId": "h-2", "likelihood": 0.5, "rationale": "inconclusive"},
    {"hypothesisId": "h-3", "likelihood": 0.5, "rationale": "inconclusive"},
    {"hypothesisId": "h-4", "likelihood": 0.5, "rationale": "inconclusive"},
    {"hypothesisId": "other", "likelihood": 0.5, "rationale": "inconclusive"}
  ]},
  {"clusterId": "c-2", "likelihoods": [ ... same pattern ... ]}
]'
```

Result: `"verdict": "flat"` (or `"none"`). **Correct behavior here is to say
nothing conclusive** — report the flat distribution and name what evidence
would help discriminate (check `discriminators` in the result, or the
"Watch for:" line in `surfaceText`). This is the single most important
discipline the tool enforces: a flat verdict is not a failure, it's the
honest answer, and reporting a confident-sounding conclusion anyway would be
worse than reporting nothing.

## 5. A discriminating observation arrives

```bash
python3 hunch.py observe sit-1 --text "Mustard was seen holding the bloody candlestick firsthand" --source-type firsthand
# -> obs-4, a NEW cluster (this is materially different from the scream/rumor)
```

`--source-type firsthand` matters — reliability `1.0` means this evidence
gets full weight in the likelihood blend, not the damped weight a rumor or
secondhand account would get.

## 6. Score a decisive matrix — expect peaked

```bash
python3 hunch.py rescore sit-1 --json '[
  {"clusterId": "c-3", "likelihoods": [
    {"hypothesisId": "h-1", "likelihood": 0.95, "rationale": "direct firsthand match"},
    {"hypothesisId": "h-2", "likelihood": 0.02, "rationale": "no connection to Plum"},
    {"hypothesisId": "h-3", "likelihood": 0.02, "rationale": "no connection to Scarlett"},
    {"hypothesisId": "h-4", "likelihood": 0.02, "rationale": "no connection to Peacock"},
    {"hypothesisId": "other", "likelihood": 0.02, "rationale": "strong direct evidence for Mustard"}
  ]},
  {"clusterId": "c-1", "likelihoods": [ ... neutral 0.5s, unrelated ... ]},
  {"clusterId": "c-2", "likelihoods": [ ... neutral 0.5s, unrelated ... ]}
]'
```

Now `"verdict": "peaked"`, `"topId": "h-1"` (Mustard), and OTHER's posterior
gets floored at exactly `0.05` if the raw math pushed it lower — epistemic
humility never fully disappears even at high confidence.

## 7. Surface and resolve

```bash
python3 hunch.py surface sit-1
```

Relay the full text — question, every hypothesis's posterior, evidence for
the top one, and what would distinguish it from the runner-up. Never just
say "it was Mustard, 92% confident" without the surrounding distribution.

```bash
python3 hunch.py resolve sit-1 --resolution h-1
python3 hunch.py calibration
```

`calibration` now shows 1 entry, 100% accuracy — the top claimed hypothesis
matched the actual resolution. Over many resolved situations, this number
tells you (and the person you're working with) whether your confidence
levels are trustworthy or systematically over/under-confident.

## What this example deliberately shows

- **Priors get renormalized, not trusted verbatim** — you don't have to do
  the arithmetic to make hypotheses sum correctly.
- **Restated evidence doesn't double-count** — `--same-event-as` clustering.
- **Weak evidence produces a flat verdict, and that's the correct, honest
  output** — not a bug to work around.
- **Source reliability actually changes the math** — a rumor and a firsthand
  account with the same likelihood score don't move the posterior the same
  amount.
- **OTHER never fully disappears** — even a 92%-confident peaked verdict
  leaves room for "actually, something else."
