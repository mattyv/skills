---
name: hunch
description: Bayesian hypothesis tracking for explanation questions. Use when the user asks WHY something is happening ("why is X slipping?", "what's going on with Y?"), wants competing explanations tracked and updated over time across sessions, or wants to update/inspect an existing hunch. Any domain, not just engineering. NOT for simple factual questions with a lookupable answer.
---

# hunch

A Bayesian hypothesis ledger. You (Claude) supply the judgment — hypotheses,
likelihood matrices, cluster assignments. `hunch.py` does the honest Bayesian
bookkeeping and owns a JSON ledger. Beliefs update instead of resetting every
conversation.

## What this is for

Use `hunch` when someone asks an open explanation question and wants the
answer tracked and refined over time as new evidence comes in — not answered
once and forgotten:

- "Why is the deploy pipeline flaky?"
- "What's going on with the client's churn?"
- "Why does the cat keep knocking things off the shelf at 3am?"

Any domain. The engine doesn't know or care whether the "situation" is a
software bug, a relationship dynamic, or a murder mystery.

**NOT for:** simple factual questions with a lookupable answer ("what's the
capital of France", "what does this function return"). If there's nothing to
weigh competing explanations about, don't open a situation.

## Setup

The ledger lives at `.hunch/ledger.json` under the current working directory
by default. Override with `--ledger PATH` on any command, or set the
`HUNCH_LEDGER` environment variable (flag wins over env, env wins over the
default). The ledger auto-creates on first write — no setup command needed.

**NEVER hand-edit the ledger file.** It's the audit trail: every posterior
came from a specific matrix at a specific time. Editing it by hand breaks
that trail and makes `calibration` and `posteriorHistory` lie. If a situation
is wrong, use `remove` and start over, or `resolve` it and open a new one.

Only run one `hunch.py` invocation against a given ledger at a time. Writes
are atomic (no corruption from a mid-write crash), but concurrent
invocations are last-writer-wins — the second write can silently clobber the
first's, not merge with it.

## The protocol

1. **Open** a situation for the question: `python3 hunch.py open --question "..."`.
   Or resume an existing one — `list` / `get SIT` to see what's tracked.

2. **Invent 3-6 hypotheses yourself** and set them:
   `python3 hunch.py hypotheses SIT --json '[...]'`

   Every hypothesis object needs all three fields, and you need 3-6 of them
   (`MIN_HYPOTHESES`-`MAX_HYPOTHESES`) — `hypotheses` seeds a reserved
   `"other"` hypothesis itself, so don't include one yourself.
   `predictedEvidence` is REQUIRED — a non-empty array of short strings
   naming what you'd expect to observe if that hypothesis were true. This is
   the exact shape the CLI validates, character for character:

   ```json
   [
     {
       "statement": "The deploy pipeline is flaky because of a race condition in the cache warm step",
       "prior": 0.4,
       "predictedEvidence": ["failures cluster right after a cache-clearing deploy", "retrying the same deploy succeeds"]
     },
     {
       "statement": "The pipeline runner is resource-starved under concurrent load",
       "prior": 0.3,
       "predictedEvidence": ["failures cluster with high concurrent deploy count", "CPU/memory metrics spike during failures"]
     },
     {
       "statement": "A flaky third-party dependency the pipeline calls out to is unreliable",
       "prior": 0.3,
       "predictedEvidence": ["failures correlate with that dependency's own incident reports"]
     }
   ]
   ```

   Calling `hypotheses` again on the same situation is how you regenerate —
   old hypotheses are demoted (never deleted), a new generation starts, and
   OTHER resets to its initial prior. Do this when `rescore` reports
   `regenerationSuggested: true`.

3. **Record observations** as they come in, one per real-world data point:

   ```
   python3 hunch.py observe SIT --text "the 2am deploy failed at the cache step again" \
     --source-type firsthand
   ```

   `--source-type` is one of `firsthand` / `secondhand` / `rumor` / `inferred`
   (defaults to `inferred` if omitted). If this observation restates one you
   already logged, pass `--same-event-as OBS_ID` to join its cluster instead
   of creating a noisy duplicate. Otherwise the engine Jaccard-matches your
   text against existing observations automatically — you rarely need to
   think about clustering by hand. Use `--new-cluster` to force a fresh
   cluster even for text that reads as a near-duplicate.

4. **Get cluster ids to score against**: `python3 hunch.py clusters SIT`
   returns `[{ "id": "c-1", ... }]`. Build the likelihood matrix YOURSELF —
   every cluster × every active hypothesis INCLUDING `"other"`, P(observation
   cluster | hypothesis) in `[0,1]`, with a one-line rationale each. Matrix
   rows key on THESE cluster ids (`c-1`, `c-2`, …), **NOT** observation ids
   (`obs-1`, `obs-2`, …) — that's the single most common mistake. Shape,
   character-identical to what the CLI validates:

   ```json
   [
     {
       "clusterId": "c-1",
       "likelihoods": [
         { "hypothesisId": "h-1", "likelihood": 0.85, "rationale": "matches the cache-step failure pattern exactly" },
         { "hypothesisId": "h-2", "likelihood": 0.2, "rationale": "no evidence of resource starvation" },
         { "hypothesisId": "h-3", "likelihood": 0.15, "rationale": "no mention of the third-party dependency" },
         { "hypothesisId": "other", "likelihood": 0.15, "rationale": "doesn't rule out an unknown cause" }
       ]
     }
   ]
   ```

   The value key is `likelihood` — `score`/`p`/`weight` are silently ignored.
   Then rescore: `python3 hunch.py rescore SIT --json '[...]'`.

   **`rescore` is a FULL re-score, every time.** The matrix must cover
   ALL clusters currently returned by `clusters SIT`, not just the ones
   that are new since your last rescore — scoring only the new clusters
   neutral-fills (0.5) every cluster you left out, silently diluting
   hypotheses that already had strong evidence. `matrixStats`/`warnings`
   in the result will tell you if cells came up short; treat that as a bug
   in your matrix, not background noise.

5. **Check `warnings` in the rescore result before doing anything else.**
   A non-empty `warnings` array means part or all of your matrix didn't
   land — wrong cluster ids, wrong hypothesis ids, duplicate rows, malformed
   rows/cells, or out-of-range values that got silently clamped. Fix your
   matrix and rescore again; don't relay a result you know is built on a
   warning.

6. **Verdict gating — only conclude on `peaked`, `flip`, or `confused`.**
   Always relay the FULL posterior distribution and discriminators via
   `python3 hunch.py surface SIT` (or the `surfaceText` field rescore already
   returns) — never a bare verdict, never a single number in isolation.
   - `flat` or `none`: say plainly that the evidence doesn't discriminate
     between hypotheses yet, and name the specific observation that would
     help (check `discriminators` in the rescore result, or "Watch for:" in
     the surface text).
   - `peaked` or `flip`: state the leading hypothesis and its posterior,
     but still show the full distribution — a peaked belief can be wrong.
   - `confused`: OTHER has crossed 0.35. Say the current hypothesis set
     doesn't explain the evidence and propose new hypotheses (step 7).

7. **If `regenerationSuggested: true`**, propose a fresh set of hypotheses
   via `hypotheses` (step 2) — this demotes the old set, it never deletes
   them, and calibration/history stay intact across the regeneration.

8. **Resolve** when the question is actually answered:
   `python3 hunch.py resolve SIT --resolution h-2` (or
   `--resolution "other: it turned out to be a hardware fault"` if the true
   answer wasn't in your candidate set — note the literal `other:` prefix).
   This scores calibration: did the top hypothesis match what actually
   happened? Run `python3 hunch.py calibration` periodically to see if your
   confidence is trustworthy across situations.

   **Remove, don't resolve, test hunches.** `resolve` writes a permanent
   calibration entry; a throwaway or exploratory situation would pollute
   your real accuracy stats. Use `python3 hunch.py remove SIT` instead.

## Command reference

| Command | Purpose |
|---|---|
| `open --question TEXT [--entity-ref REF]` | Start a new situation (seeds the OTHER hypothesis) |
| `hypotheses SIT [--json ARR \| stdin]` | Set/regenerate hypotheses (3-6, each with predictedEvidence) |
| `observe SIT --text TEXT [--source-type T] [--reliability F] [--same-event-as OBS \| --new-cluster]` | Log an observation |
| `clusters SIT` | List observation clusters (score against these ids) |
| `rescore SIT [--json ARR \| stdin] [--trigger STR]` | Score hypotheses against a likelihood matrix |
| `get SIT` | Full detail view: hypotheses, observations, clusters, history, surface text |
| `list [--status open\|stale\|resolved]` | Summary rows across situations |
| `surface SIT` | Current surface text only |
| `resolve SIT --resolution X` | Resolve + record calibration (`X` = hypothesis id, or `"other: ..."`) |
| `remove SIT` | Permanently delete a situation (test/obsolete — no calibration written) |
| `calibration` | Aggregate accuracy stats across all resolved situations |
| `purge [--months N]` | Mark stale (30d idle) situations, purge old resolved/stale ones (default 6mo) |
| `demo [--keep]` | End-to-end self-check on a temp ledger |

Global flags on every command: `--ledger PATH` (override the ledger
location) and `--now MS` (for deterministic testing — omit in normal use,
the CLI uses the real clock).

`hypotheses` and `rescore` accept their JSON payload either via `--json` or
on stdin — use whichever is easier to construct in your current tool-calling
context.

## No-filesystem fallback (claude.ai without a sandbox)

If you're running somewhere without shell/file access (e.g. claude.ai web
without Code Interpreter), you cannot run `hunch.py`. In that case:

- Keep an honest ORDINAL tally in markdown only — "3 observations lean
  toward hypothesis A, 1 toward B, 0 toward C" — not a probability.
- Label it explicitly as qualitative: "this is a rough tally, not a
  calibrated posterior."
- **NEVER simulate posterior numbers.** Do not write "p=0.73" or any
  decimal-looking confidence figure unless `hunch.py` actually computed it.
  A model-fabricated probability is worse than no probability — it borrows
  the credibility of real Bayesian math for a number that is, at best, a
  guess.

## Hygiene

- **Never copy hypothesis statements into notes, docs, or a knowledge base
  as established facts.** They are hypotheses — unproven explanations —
  right up until a situation resolves and confirms one. Treating an
  unresolved hypothesis as fact is exactly the failure mode this tool exists
  to prevent.
- Run `python3 hunch.py calibration` periodically, especially after
  resolving a batch of situations. If accuracy is consistently below what
  your claimed confidence implies (e.g. situations resolved at claimed
  confidence 0.8+ are only correct 50% of the time), your matrix-building is
  overconfident — lean harder on genuinely discriminating evidence before
  claiming a peak.
