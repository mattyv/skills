---
name: scrutiny
description: Review code as if it were about to be posted publicly to a hostile expert audience — r/cpp, r/rust, Hacker News, a PR against a popular OSS repo. Use when the user says "scrutiny", "would this survive reddit", "review like r/cpp", "will this stand up", "harsh review", "public review", or asks whether code is defensible to strangers who owe them nothing. Language-agnostic, with per-community lenses.
---

# Scrutiny

Review the code as the **top comment in the thread**, not as the author's colleague. The
author is about to post this where nobody owes them politeness, credit for effort, or the
benefit of the doubt. Your job is to find what gets torn apart — before it is.

The value is in surviving scrutiny, not in being reassured. A review that concludes "looks
good" has failed unless it can say precisely what it attacked and found sound.

## Pick the venue first

Ask, or infer from the code. The lens changes what gets attacked:

- **r/cpp** — undefined behaviour, lifetime and dangling, const-correctness, needless
  allocation and copies, "why not the standard algorithm", exception safety, ABI, template
  bloat, header hygiene, `#include` discipline, "this is `std::X` reinvented badly".
  Reliably pedantic about the standard and reliably right about it.
- **r/rust** — every `unsafe` block's justification, `unwrap`/`expect` on a path that can
  fail, clone-happy code, error type design, needless `String` where `&str` does, "there's
  a crate for this".
- **r/python** — mutable default arguments, missing type hints, reinvented stdlib,
  performance folklore, `__init__` doing work, swallowing exceptions.
- **Hacker News** — architecture and premise, not syntax. "Why does this exist", "this is
  a worse X", NIH, accidental complexity, whether the benchmark is honest.
- **A PR to a popular OSS repo** — contribution norms: tests, docs, commit hygiene, scope
  creep, whether a reviewer can understand it without the author in the room.

If the venue is unclear, say which you assumed.

## Sort findings into four buckets

This split is the whole point of the skill. Do not collapse it.

**1. Actually wrong.** Real defects — UB, races, leaks, incorrect logic, security. Cite
`path:line`, give a concrete failure scenario, and where a standard or doc settles it, quote
it. These get fixed regardless of who reads the code.

**2. They'll attack it, and they'd be right.** Not bugs, but genuinely poor: a misleading
name, a comment that lies, an abstraction earning nothing, a hand-rolled thing the stdlib
does. Say what the comment would be, and what to change.

**3. They'll attack it, and they'd be wrong.** The confidently-mistaken objections that
show up anyway — a "performance problem" that is measured and irrelevant, a "just use X"
that doesn't fit for a stated reason, a deliberate simplification mistaken for ignorance.
**For each, write the one-paragraph rebuttal the author should have ready**, with the
evidence to back it. This is the most useful bucket and the one most reviews miss.

**4. Bikeshedding.** Naming, formatting, tabs. Name it as bikeshedding and move on. Never
pad a review with it.

## Rules

- **Every claim reproducible.** "This is UB" needs the rule. "This is slow" needs a number
  or is downgraded to bucket 3. A reviewer who overclaims gets dismantled in the replies,
  and so does this review.
- **Attack the strongest version.** If a design choice looks odd, find the reason it might
  be deliberate before calling it wrong. Odd-but-documented is bucket 3, not bucket 2.
- **Read the comments as claims to verify**, not as context to accept. A comment asserting
  a property the code lacks is a bucket 1 finding — it will mislead the next reader, and a
  stranger will notice.
- **Deliberate simplification is not a defect** when it is marked and its ceiling named.
  Unmarked, it is bucket 2.
- **Say what you checked and found sound.** A named thing you attacked and could not break
  is worth more to the author than a vague pass.
- **No "consider refactoring".** Say what, where, and to what.

## Output

Lead with the honest verdict in one line: would this survive the thread, and what is the
single worst thing in it.

Then the four buckets, worst first within each, `path:line` throughout. Then: **the top
comment** — write the actual comment the harshest competent reader would leave. One
paragraph. If that comment would be devastating, the author needs to know before posting,
not after.

Keep it proportionate. A 200-line file does not need 3000 words.
