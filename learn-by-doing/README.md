# learn-by-doing

A skill for when you want to understand the code, not just receive it.

## What it does

The agent turns a coding task into a series of small experiments. Before each one, it asks you to predict the result or choose an approach. Then it runs the experiment, compares the evidence with your prediction, and explains the mechanism once it matters. You write the central logic; the agent handles boilerplate and environment repair.

The workflow is adapted from Terence Tao's hiking analogy for learning: the goal is a route you could walk again alone, not a summit you were carried to.

## When to use it

Say something like:

- "help me learn by doing"
- "don't code it all for me"
- "guide me through implementing this"

Use it when you want the ability, not only the artefact: understanding a closure's captured state, diagnosing why a query ignores an index, learning an unfamiliar subsystem in your own repo.

Do not use it for ordinary delivery work. The agent will not activate it unless you signal a learning intent.

## What to expect

- Turns end when your input is needed. The agent will not ask for a prediction and then reveal the answer in the same message.
- Tool output is pasted verbatim, never tidied into a fake input/output pair.
- File references are clickable links with line anchors, and the lines under discussion are quoted inline.
- Help escalates promptly: a hint, then a pointer to evidence, then a partial scaffold, then a worked example. You will not be made to fail a fixed number of times first.
- "Show me" and "take over this part" are honoured immediately. The learning workflow resumes afterwards unless you say otherwise.

## Contents

- `SKILL.md` — the full workflow: establishing the learning target, running one experiment at a time, adjusting help, checking transfer.
