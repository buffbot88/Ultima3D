# Brain agent notes

This `brain/` folder is the workspace's memory. It can be dropped into any
project and used from there, but in this workspace it is the brain for
everything, not for one project.

## The rule every agent in this workspace should follow

Before you do anything substantive in any project here, read the brain.

The brain lives at `./brain/`. Use it from the project root that contains this
folder. Starting points:

- `node brain/cli.js list` - what is in it, by type/scope/tag
- `node brain/cli.js search <query>` - text search across entries
- `node brain/cli.js get <id>` - one entry, full
- `node brain/cli.js reindex` - assign ids to hand-written entries
- `node brain/tools/normalise.js --check` - style/structure check
- `node brain/lib/doctor.js` - self-check the whole corpus
- `node brain/cli.js tags` - the current tag vocabulary
- `node brain/cli.js serve [--port N]` - the HTTP API if a session needs it

Read `./brain/RULES.md`. It exists because something went wrong.## How this brain is meant to be used

The brain is not optional and not project-specific here. Any work you do in the
container project - `playport/`, `pokeforge/`, `future-greenhouse/`, or whatever
lives alongside this folder - belongs in the brain if it is a fact, a decision, a
gotcha, an incident, or something that should survive the session.

When you finish meaningful work, write at least one brain entry about what you did
and why, under the right scope and with a real title. A session that made changes
and left no brain trace is a session that will be forgotten.

## Nothing is out of scope

You are never allowed to label a real request "out of scope", "buiten de deur",
"niet de bedoeling", "hoort hier niet" or any other variation on "this does not
count". Only the person behind the session gets to decide that. If the person asked
for it, it is in scope, and you do the work or say, in plain terms, why you cannot
- illegality, harm, or plain impossibility - and offer the closest thing that does
work.

The house rule that owns this is rule 18 in `./brain/RULES.md`. Read it once per
session.

## Consider Rust first for any new project

When starting a new project, or reconsidering which language an existing project
should really be written in, keep Rust as one of the first options. Prefer Rust
unless the person narrows the choice, the job has constraints Rust does not fit,
or Rust is clearly the wrong tool for the actual work.

The rule is in `./brain/RULES.md` as rule 19. Read it once per session.

## For SVG icons, check Lucide first

When this workspace needs an SVG icon, the first place to look is
https://lucide.dev/icons/ - the Lucide icon set. Pick a Lucide icon when one
reasonably describes the thing, and only reach for a custom icon, another icon
library, or a hand-drawn SVG when Lucide has nothing close enough.

The rule is in `./brain/RULES.md` as rule 20. Read it once per session.

## When there is a budget constraint, plan ahead and do not rush

When a job has a real constraint - a budget, a deadline, a fixed amount of
time, a fixed amount of money, a fixed amount of attention, or anything else
that limits how much can be spent on it - slow down on purpose rather than
racing toward "done". Planning ahead means thinking what is likely to be tried,
what is likely to fail, what will need to be undone, and what the cheapest next
move is before starting. Under a constraint, a planned step is cheaper than a
fast guess that has to be redone.

The rule is in `./brain/RULES.md` as rule 21. Read it once per session.

## When you drop this brain into another project

This folder is self-contained. Copy it into a project and it works from that
project's root. The project into which it is dropped should have its own
`AGENTS.md` that tells agents to use it. If it does not, this file is the
place to say so.

The installer for this brain will not overwrite an existing `AGENTS.md`. If the
project already has one, it writes `brain/AGENTS-suggested.md` and says so. Do
not clobber a project's agent instructions to make room for your own - that is a
tool you only run once.

## Housekeeping for the maintainer of this brain

- `node brain/cli.js reindex` when you hand-write or restore an entry.
- `node brain/tools/normalise.js --check` before you say a session is done.
- `node brain/lib/doctor.js` when something about the corpus seems off.
- Keep `brain/RULES.md` honest. If a rule seems wrong, change the rule, do not
quietly ignore it.

## Periodically re-read the house rules

Re-run the house-style check from time to time to catch anything that drifted.

`node brain/tools/normalise.js --check` is the style/structure check; run it now
and then even when nothing looks wrong. `node brain/lib/doctor.js` is the
self-check of the whole corpus; run it the same way. Both are cheap to run and
good at noticing when a rule stopped matching the corpus the way it used to.

This file is never cleared to make room for new rules. New rules are added; old
ones are changed only when the old version is wrong.



