# hunch

A hunch ledger. Claude proposes explanations, `hunch.py` does the Bayesian
bookkeeping, so beliefs update instead of resetting every conversation.

`hunch` is a portable Claude skill: one Markdown file describing the
protocol (`SKILL.md`) and one zero-dependency Python script that owns the
math and the data (`hunch.py`). No build step, no external packages, no
server.

## Install

Clone this repo (or copy the `hunch/` directory) anywhere. For Claude Code:

```bash
git clone <this-repo> hunch
cp -r hunch ~/.claude/skills/hunch
```

Or symlink it if you want to track updates:

```bash
ln -s "$(pwd)/hunch" ~/.claude/skills/hunch
```

Smoke-test the install:

```bash
cd ~/.claude/skills/hunch
python3 hunch.py demo
```

A clean exit with `"ok": true` means the engine, the ledger I/O, and the
CLI wiring all work. Nothing else to configure — the ledger auto-creates on
first write.

## 60-second example

```bash
$ python3 hunch.py open --question "Why does the deploy keep failing on Tuesdays?"
{ "id": "sit-1", "question": "Why does the deploy keep failing on Tuesdays?", ... }

$ python3 hunch.py hypotheses sit-1 --json '[
    {"statement": "A scheduled Tuesday job contends for the same DB connections",
     "prior": 0.4, "predictedEvidence": ["failures correlate with the weekly report job", "connection pool exhaustion in logs"]},
    {"statement": "Tuesday deploys happen to use a stale cached config",
     "prior": 0.3, "predictedEvidence": ["config timestamp older than deploy time", "fresh config fixes it on retry"]},
    {"statement": "A flaky external dependency has higher load on Tuesdays",
     "prior": 0.3, "predictedEvidence": ["third-party status page shows Tuesday incidents", "retries eventually succeed"]}
  ]'

$ python3 hunch.py observe sit-1 --text "the pool exhaustion error showed up again this Tuesday" --source-type firsthand
{ "observationId": "obs-1", "clusterId": "c-1", "clusterMethod": "new" }

$ python3 hunch.py clusters sit-1
[ { "id": "c-1", "reliability": 1.0, "representativeText": "the pool exhaustion error showed up again this Tuesday", ... } ]

$ python3 hunch.py rescore sit-1 --json '[
    {"clusterId": "c-1", "likelihoods": [
      {"hypothesisId": "h-1", "likelihood": 0.9, "rationale": "exact match on pool exhaustion"},
      {"hypothesisId": "h-2", "likelihood": 0.1, "rationale": "no config staleness evidence"},
      {"hypothesisId": "h-3", "likelihood": 0.1, "rationale": "no external dependency evidence"},
      {"hypothesisId": "other", "likelihood": 0.1, "rationale": "doesn't rule out something else"}
    ]}
  ]'
{ "verdict": "peaked", "topId": "h-1", "surfaceText": "...", "warnings": [], ... }
```

One observation was already enough to peak here because it directly matched
a predicted discriminator. In practice you'll usually need a few rounds of
observe → rescore before the distribution moves anywhere meaningful.

## How it works

**You reason, the script counts.** Claude is good at generating plausible
competing explanations and judging how well a piece of evidence fits each
one — that's the hard, unstructured part. `hunch.py` is good at doing the
arithmetic honestly every single time: full re-normalization from priors,
consistent floor and clamp rules, an audit trail that can't drift. Splitting
the work this way means the belief update is trustworthy even though the
inputs (the hypotheses, the matrix) are subjective judgment calls.

**Every situation carries a reserved "OTHER" hypothesis** that starts at 15%
prior and can never drop below a 5% posterior floor. This is deliberate
epistemic humility: no matter how confident the visible hypothesis set
looks, there's always room in the math for "none of the above." If OTHER's
posterior climbs past 35%, that's a loud signal the current hypothesis set
doesn't actually explain the evidence — `rescore` flags this as
`regenerationSuggested`, and the protocol says to propose fresh hypotheses
rather than keep scoring a set that's wrong.

**Observations cluster before they're scored.** Two people describing the
same event shouldn't count as two independent pieces of evidence — that
would double-count and artificially inflate confidence. `hunch` clusters
observations (by an explicit hint, or a Jaccard token-overlap fallback) and
scores per-cluster, with cluster reliability set to the most reliable member.

**The ledger is the only source of truth, and it's append-only in spirit.**
Hypotheses are never deleted, only demoted when superseded by a new
generation — so `posteriorHistory` and `calibration` stay meaningful even
after you've completely changed your mind about what's going on. Nothing
about the ledger is meant for hand-editing; every number in it came from a
specific matrix at a specific timestamp; that's the whole point.

## FAQ

**Why does the model build the likelihood matrix instead of the script
inferring it?** Because "how likely is this evidence under this hypothesis"
is a judgment call that requires understanding the evidence, not a
computation. The script's job is to make sure that once you've made the
judgment call, the resulting belief update is done correctly and
consistently — not to make the judgment call for you.

**Why does OTHER never die?** Because a hypothesis set that was invented
before seeing much evidence is very often incomplete, and a Bayesian model
that lets its "something else" bucket go to zero is a model that's
structurally incapable of admitting it forgot a possibility. The 5% floor
costs you a little top-hypothesis confidence in exchange for the model
never being mathematically certain about an incomplete hypothesis set.

**Why no hand-editing the ledger?** Every posterior in the ledger is the
output of a specific likelihood matrix at a specific point in time —
`posteriorHistory` and `lastScoring` are an audit trail, not just a cache.
Hand-editing a posterior (or a calibration entry) breaks the link between
"what evidence was observed" and "what was believed," which is the entire
value of tracking this over time instead of just asking the model to
reason fresh each session.

## Testing

```bash
python3 -m unittest test_hunch -v
python3 -m pytest test_hunch.py -q   # if pytest is installed
python3 hunch.py demo                # end-to-end smoke test
```

No external dependencies — Python 3.9+ standard library only.

## License

MIT — see `LICENSE`.
