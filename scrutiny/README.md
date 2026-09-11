# scrutiny

Review code as the harshest competent stranger would — before you post it where strangers can.

## What it does

The agent reviews your code as the top comment in a hostile expert thread: r/cpp, r/rust, r/python, Hacker News, or a PR against a popular open-source repo. It picks the venue, attacks the code through that community's lens, and sorts every finding into one of four buckets:

1. **Actually wrong** — real defects, with `path:line` and a concrete failure scenario.
2. **They'll attack it, and they'd be right** — not bugs, but genuinely poor: a lying comment, an abstraction earning nothing, a hand-rolled stdlib function.
3. **They'll attack it, and they'd be wrong** — the confident objections that will appear anyway, each with the one-paragraph rebuttal you should have ready.
4. **Bikeshedding** — named as such and dismissed.

It closes with the actual top comment the thread would produce. If that comment is devastating, you find out now.

Bucket 3 is the point. Most reviews only tell you what to fix; this one also arms you for the objections you should not concede.

## When to use it

Say something like:

- "scrutiny"
- "would this survive reddit"
- "review like r/cpp"
- "harsh review" or "public review"

Use it before publishing, open-sourcing, or submitting to a repo whose maintainers owe you nothing. Name the venue if you have one; otherwise the agent will infer it and say which it assumed.

## What to expect

- Every claim is reproducible. "This is UB" cites the rule; "this is slow" carries a number or is downgraded to bucket 3.
- Comments in the code are treated as claims to verify, not context to accept.
- Marked, deliberate simplifications with a stated ceiling are not defects. Unmarked ones are.
- The review says what it attacked and could not break. A bare "looks good" is a failed review.
- Length is proportionate. A 200-line file does not get 3000 words.

## Contents

- `SKILL.md` — the venue lenses, the four buckets, the rules, and the output format.
