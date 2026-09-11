# skills

A personal collection of Claude skills. Each one lives in its own folder,
is self-contained, and can be installed individually — a skill folder never
depends on its siblings in this repo.

## What's here

| Skill | What it does |
|---|---|
| [hunch](hunch/README.md) | Bayesian hypothesis tracking for explanation questions — Claude proposes and scores explanations, a zero-dependency script owns the honest math and a JSON ledger, so beliefs update instead of resetting every conversation. |
| [learn-by-doing](learn-by-doing/README.md) | Hands-on coding for when you want the ability, not just the artefact — Claude runs one small experiment at a time, asks you to predict before it reveals, and you write the central logic yourself. |
| [scrutiny](scrutiny/README.md) | Code review as the top comment in a hostile expert thread (r/cpp, r/rust, HN, an OSS PR) — findings sorted into real bugs, fair attacks, unfair attacks with the rebuttal you should have ready, and bikeshedding. |

More skills get added as their own row here as they're built.

## Install

From a clone of this repo:

```bash
git clone <this-repo>
cd skills
```

Then, per skill, either:

```bash
./<skill>/install.sh          # if the skill ships one
```

or install by hand:

```bash
cp -r <skill> ~/.claude/skills/<skill>
# or, to track updates via git pull:
ln -s "$(pwd)/<skill>" ~/.claude/skills/<skill>
```

Check the skill's own README for anything beyond that — options, smoke
tests, prerequisites.

## What is a Claude skill?

A skill is a folder Claude can discover and load on demand. Its `SKILL.md`
frontmatter (`name` + `description`) tells Claude *when* to engage it —
Claude reads the description against the conversation and decides whether
to pull the skill in, without you having to invoke it by name. Any bundled
scripts do the deterministic, non-negotiable work (math, file I/O,
validation) that a language model shouldn't be trusted to do by vibes alone.

## License

MIT. See each skill's own `LICENSE` file.
