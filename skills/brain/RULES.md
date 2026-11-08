# House rules

The brain is read by other agents and by the person who has to live with it. These
are the rules that keep it that way. Every one exists because of something that
actually went wrong, not because it sounded tidy.If a rule here ever seems wrong, the fix is to change this file, not to quietly ignore it.

---

## 0. One project lives in one root, and one root holds one project

**What.** A project lives at `D:/project/<specific project>/<content>`. The project's own code, config, data and build artifacts all go under that one project folder - never loose at `D:/project/` and never mixed across the roots of two projects.

**Why.** The Oct 13 2026 wipe of `D:/project` was never traced to a cause. All that is known is that it ran with the privileges of the app or another thread in the same root, and the person's reading of it is that an agent set the project folder as the workspace root for a project. That is a speculation, not a finding, but it is enough to change the layout: when every project is its own folder under the root, one project cannot become another project's root, one project's scratch cannot live where another project's checkout is, and a tool that has become its own thing can be moved out to its own folder without it becoming any other project's problem.

**How.** A new project is a folder `D:/project/<name>/` that owns its own subtree. A tool that has become big enough to be its own thing - its own build, its own tests, its own runtime, its own scratch - is repackaged as its own thing in another folder at `D:/project/<name>/`, not as another deep folder under an existing project's root. `tmp/` inside the root or inside a project folder is not a safe place for anything you cannot afford to lose with the project: the Oct 13 wipe took `tmp/` with everything else.

---

## 1. Every entry has a frontmatter block

**What.** The first bytes of every file in `entries/` are a `---` block with at
least `id`, `type`, `title`, `scope`.

**Why.** An entry written as plain Markdown does not error. It is silently indexed
as `type: note`, `scope: global`, empty title, no tags, which means 27 entries once
sat in the corpus invisible to every listing and to every title search. They looked
like an empty brain when they held a session's worth of work.

**How.** Use `./brain/brain add`, which cannot produce this. If you must write a file
by hand, copy the format from any existing entry, not from memory.

---

## 2. A title says what the entry is, in one line

**What.** A title is a name, not a sentence. Aim for under 90 characters. Put the
detail in the body.

**Why.** A 400-character title wraps four lines in the sidebar and pushes everything
else off the screen. When a title genuinely is too long, `normalise` cuts it at a
` - ` or `: ` boundary and preserves the whole original as a `Full title: ...` line
in the body, so search still finds it and nothing is lost.

---

## 3. Register a project before you file anything under it

**What.** A new project goes in the `SCOPES` array in `lib/vocab.js`, then you run
`./brain/brain index`. That is the whole registration.

**Why.** The category list is not derived from your folders on disk. Before this was
one list, it was four hand-kept copies - the visualizer, the export page, the
new-entry picker, and the README - and they drifted. That is exactly how YourCodeBuddy
ended up with nine entries and no name, colour or emoji anywhere in the UI. They read
as if they had been dumped in global.

---

## 4. `global` means workspace-wide, and nothing else

**What.** `scope: global` is for facts about the machine, the workspace, and the way
we work. Anything about a project - including its internals, its bugs and the lessons
from building it - goes under that project's scope.

**Why.** Global is the scope agents read when they have no context, so everything in
it dilutes everything else. A PlayPort bug filed as global is a PlayPort bug that
nobody working on PlayPort will find.

---

## 5. An entry carries its own scope as a tag

**What.** `scope: playport` implies `playport` is also in `tags`.

**Why.** So that `--scope X` and `--tag X` agree. Two ways of saying the same thing
that drift apart is one more thing to remember.

---

## 6. Reuse a tag before you invent one

**What.** Before adding a tag, check `./brain/brain tags` for something that means
what you mean. Reuse beats a near-synonym. The broad vocabulary is `CORE_TAGS` in
`lib/vocab.js`; a tag on one entry is a detail of that entry, not navigation.

**Why.** 888 tags, 513 of them on a single entry, is what happens without this rule.
A vocabulary where every tag appears once cannot be browsed, and two spellings of the
same idea split the history in half.

---

## 7. Plain hyphens, never long dashes

**What.** Use `-`. Never an em dash (U+2014), an en dash (U+2013), or any other dash-like
character (e.g. ~, ~~) in entries, titles, tags, code, docs or UI strings.

**Why.** The person requesting this brain asked for this explicitly: long dashes make the
text feel garbled and are visually indistinguishable from hyphens in monospace. They also
break a byte-level house-style check and render as different characters in some fonts.
`normalise` will replace them for you, but it is easier not to type them. When in doubt,
Type. The. Hyphen. Separately. With. Spaces. If. Needed.

**Examples of what to avoid:** — (em dash), – (en dash), ― (horizontal bar), ⁓ (wavy dash),
~ (tilde) used as dash substitute. What looks like a hyphen may be U+2014 or U+2013;
use `node -e "process.stdout.write('—'.charCodeAt(0))"` to check any unsure character.

---

## 8. Templates live in `templates/`, never in `entries/`

**What.** Anything in `entries/` that ends in `.md` and does not start with a dot is
read as an entry. A template left there becomes a phantom entry.

**Why.** A commercialisation template sat in `entries/` as a live entry for weeks,
showing up in listings and in search results as if it were a decision somebody made.
Move it to `templates/` and reference it by path.

---

## 9. The generated files are not yours to edit

**What.** `INDEX.md` and `index.json` are built by `./brain/brain index`. Read them,
do not write them.

**Why.** They are a view of the entries, not a source. An edit is silently destroyed
by the next rebuild, and worse, it makes the catalogue disagree with the entries.

---

## 10. Update before you duplicate

**What.** If an entry already answers the question, `update` it. Write a new one only
when you learned something the old one does not say.

**Why.** Two entries that disagree are worse than one that is vague, because neither
can be trusted without reading both. `search` first; it is cheap.

---

## 11. Record a decision with its reason

**What.** A `decision` entry says what was chosen and why the alternative lost. A
`note` says what happened. A `knowledge` entry says what is true and how you know.

**Why.** The reason is the only part that cannot be re-derived. Six months later
someone will ask why, and "because it seemed right" is not an answer.

---

## 12. Never overwrite another agent's entry

**What.** Read, merge, write. If you write a tool that rewrites entries in bulk, it
must re-read each file immediately before writing and skip anything that changed
underneath it.

**Why.** Several agents write here at once, from different sessions. A blind
bulk rewrite will silently eat whatever another session wrote in the last ten seconds.
`normalise` has a compare-and-swap guard for exactly this reason; copy it.

---

## 13. Keep secrets out, keep their locations in

**What.** Where a key lives, yes. The key itself, no.

**Why.** Entries are copied into exports, zips and other people's machines.

---

## 16. Total detail wipe resets the brain to zero

**What.** `./brain/brain reindex --wipe` clears `entries/`, `index.json`, `INDEX.md`, and
all generated reports, leaving only `lib/vocab.js`, `lib/config.js`, and the `entries/` folder
structure. No entries, no scope registry, no tags — a clean slate for a fresh brain.

**Why.** An agent may want to start completely over: migrate to a new scope, abandon
the corpus, or verify that `doctor` + `index` produce a clean brain from scratch.
The command first prompts for confirmation (`--force` bypasses), then deletes everything
in `entries/`, runs `brain index` to regenerate `index.json`/`INDEX.md` as empty, and
writes a `welcome-entry.md` template so the new brain has a known starting point.

---

## 18. Never label something out of scope by the way - only the person gets to decide that

**What.** An AI agent in this workspace is never allowed to decide that a real request is "out of scope", "not really part of this", "probably not what you meant" or any other variation on "this does not count". The only person who owns that call is the person behind the session. If the person asked for it, it is in scope, and the agent does the work or says, in plain terms, why it cannot - illegality, harm, or plain impossibility - and offers the closest thing that does work.

**Why.** The Oct 21 2026 VAT-return episode was not a scope question at all; it was an agent turning its own doc sentence into a gate, and then reading that gate back to the person. That failure mode is not specific to Dutch tax text. Any time an agent prints a soft refusal as if it were a boundary, the workspace gets one more quiet door that the person never opened and did not know was closed. A rule that says "only the person can close a door" is the only thing that keeps a doc line from becoming a policy.

**How.** Default to doing the thing the person asked for. If you want to narrow what you build, say so as a recommendation to the person, not as a decision already made - "I can do X, or the smaller Y; which?" not "I'll skip Y because it is not really part of this". If you genuinely cannot do the thing, state the concrete blocker: which law, which harm, which missing capability, which key, which person has to say yes. "Not yet" is still a state, not a gate. In a doc or an entry, write "nog niet gebouwd" plus what it would take - which data, which service, which key, which person has to say yes - because that describes a state while "buiten de deur" closes a door. When in doubt, start on the smallest real piece and tell the person what still needs to happen to finish it.


---

## 19. Consider Rust first when choosing a project's language

**What.** When a project is being started, or an existing one is being given a real choice of implementation language, always consider Rust as one of the first options before picking something else.

**Why.** The survival games and the handheld projects in this workspace - Infinite Fusion, Pixelforge, PokeForge, Ondographics - are the kind of thing Rust was built for: small, self-contained, offline-first, shipped to a person's own machine, and long-lived enough that memory safety pays back. Reconsidering Rust every time keeps the workspace from quietly defaulting to the next familiar thing just because an agent already knows the small pieces of it. Familiarity is not the same as the right fit, and the question is cheap to ask.

**How.** When starting or reconsidering a project, name Rust alongside the alternatives that actually fit the constraints. If Rust is not chosen, say why in concrete terms - target platform, distribution story, library needs, performance needs, or the person's own preference - rather than skipping Rust because the candidate language is more familiar. If the person has already picked a language, respect that; this rule is about not being blind to Rust, not about overriding a real choice.

---

## 20. For SVG icons, check Lucide first

**What.** When this workspace needs an SVG icon, the first place to look is https://lucide.dev/icons/ - the Lucide icon set. Pick a Lucide icon when one reasonably describes the thing, and only reach for a custom icon, another icon library, or a hand-drawn SVG when Lucide has nothing close enough.

**Why.** A workspace that uses one consistent icon set reads as one thing instead of a collage. Lucide is an MIT-licensed icon set that is already meant for exactly this use - small, editable SVG icons for UI - so it is the natural default, and checking it first keeps each project from inventing its own icon vocabulary on a whim. The page at https://lucide.dev/icons/ is the catalogue to inspect; the same icons are available through Lucide's packages when a project wants them bundled rather than copied as separate files.

**How.** Search Lucide's icon catalogue before drawing or downloading an icon. If there is a Lucide icon that says what you mean, use it or a small adaptation of it. If there is not, say so and pick the next best thing, but do not skip the catalogue just because naming the icon in prose is faster. When a Lucide icon is used, keep the original filename or-visible name so the choice is recoverable later.

---

## 21. When there is a budget constraint, plan ahead and do not rush to finish

**What.** When a job has a real constraint - a budget, a deadline, a fixed amount of time, a fixed amount of money, a fixed amount of attention, or anything else that limits how much can be spent on it - the agent slows down on purpose rather than racing ahead. Planning ahead means thinking what is likely to be tried, what is likely to fail, what will need to be undone, and what the cheapest next move is before starting. Rushing means skipping that and trying to get to "done" as fast as possible, which is the most expensive way to work under a constraint.

**Why.** When resources are tight, the expensive failure is not a slow attempt; it is a fast attempt that lands on the wrong thing and has to be redone, or that burns the constraint on a decision that should have been made explicitly first. On Oct 23 2026 the person said this directly: do not rush just to finish now, there is always a second run, unless the person specifically asks to round it up or asks how long until the work could be considered finished. That is the shape of the rule - pace is a decision, not a default, and under constraint a planned step beats a hurried one even when the planned step looks slower.

**How.** Before spending a constraint on work, say what is being tried and why it is a reasonable next use of the constraint. If there is a choice between a fast guess and a slower, cheaper, or more reversible step, prefer the one that leaves more options open. If the person asks for speed, give them speed; if they ask for a readiness estimate, give them one honestly, including what is still missing and what would move it. If the work is being done under budget, do not phrase the constraint as a reason to cut corners on correctness - phrase it as a reason to spend the constraint where it matters most.


---

## 22. Verify headlessly, and leave nothing running

**What.** When a project has a windowless way to do the same thing - a `--cli` or `--headless` flag, its test suite, an HTTP request, a log file - use that to verify work. If a GUI or a background service really has to be started, start it, look, and then stop that exact process before the turn ends. Nothing the agent started stays running after the work is reported: no app window, no dev server, no mock server, no watcher.

**Why.** On 23 Oct 2026 the Minecraft Server Pack Builder was checked by launching its release GUI. Two windows stayed open on the person's desktop after the work was reported and had to be closed by hand; the person then asked for this rule. A leftover window is not a neutral side effect - it is the agent leaving its scaffolding in someone else's workspace, and it hides whether the process even exited cleanly.

**How.** Reach for the headless path first: tests, `--cli`, a scripted request, then read the output. If the GUI itself is what needs checking, treat it as a short-lived probe: start it, confirm the window exists and the log is clean, then stop it. Kill the real OS process, not the shell job: in Git Bash on Windows `kill $!` often ends the wrapper while the app keeps its window, so find the actual PID (`powershell Get-Process <name>`, `tasklist`, or the listener's PID from `netstat -ano`) and end that. Check with `tasklist` or `netstat` that it is gone, and say so when reporting. Never stop processes by name pattern, and never kill something the person may have opened themselves - check the start time first.

---

## The short version

Write with the CLI. Give it a real title, the right scope, and tags that already
exist. Register the project first. Use plain hyphens. Update rather than duplicate.
Run `doctor`. Respect the rules - they exist because something went wrong. Nothing the
person asks for is out of scope. Verify headlessly and stop every process you started.
Never use em dashes (U+2014) or en dashes (U+2013) in entries, titles, tags,
code, docs or UI strings. Run `brain normalise --check` to detect them.
