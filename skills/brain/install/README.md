# Installing a brain

A brain is this project's long-term memory: durable facts, decisions, gotchas and
current state, kept in plain files so that a compaction cannot summarise it away.

There are three ways to get one. All three end with the same thing: a `brain/`
folder in your project that any agent can read and write.

---

## 1. Drag the file into a chat (easiest)

Drag **`brain-install.js`** into a Freebuff chat and send something like:

> Install this brain into my project. Save the attached file as `brain-install.js`
> in the root of my project folder and run it with `node brain-install.js`.

The model saves the file and runs it. It picks a free port, writes one entry
explaining the setup, and then runs `doctor` and `index` to prove it actually works
before reporting success.

That is the whole interaction. If the model asks where to put it, say the project
root: the brain folder becomes `brain/` next to your source.

---

## 2. Run the installer yourself

If you have the file, drop it in your project folder and run it:

```bash
node brain-install.js
```

| Flag | What it does |
|---|---|
| `--to <dir>` | Install somewhere other than `./brain` |
| `--name <name>` | Name it something other than the folder name |
| `--force` | Install over an existing brain. Refuses without it |
| `--keep-scope` | Keep an existing `lib/vocab.js` instead of the starter |

It will not touch an existing `AGENTS.md`. If your project already has one, it writes
`brain/AGENTS-suggested.md` instead and tells you.

---

## 3. Just copy the folder

`brain/` is self-contained. Copy it into a project and it works. It looks for Node in
`../.runtime/node-*/` and then on your PATH, so the launcher works either way.

Copying the folder copies **your** entries along with it, which is usually what you
want when moving a brain between machines, and usually not what you want when sharing
one. To share a copy of the code without somebody's memories, use the installer.

---

## What you get

```
brain/
├── brain, brain.cmd   launchers that find Node themselves
├── cli.js             the command line everything else goes through
├── server.js          HTTP API and the site
├── brain.json         this brain's name and port
├── RULES.md           the house rules, and why each one exists
├── INDEX.md           generated: every entry as a readable catalogue
├── index.json         generated: the same, for scripts
├── entries/           one Markdown file per entry. Your data, hand-editable
├── lib/
│   ├── store.js       reads and writes entries, atomically
│   ├── vocab.js       THE project registry. Add a project here.
│   ├── index.js       builds the catalogue
│   ├── doctor.js      the health checks
│   └── config.js      this brain's own port and name
├── ui/                the homepage, the visualizer, the export page
├── tools/             normalise.js and its tests
└── templates/         entry templates
```

Plus an `AGENTS.md` in your project telling agents the brain is there and how to use
it, so future sessions read and write it without being asked.

---

## First five minutes

```bash
cd brain
node cli.js serve
```

Open the URL it prints. The homepage explains everything: the commands, the files,
the API, and the house rules. Then:

```bash
node cli.js add --scope global --type knowledge "How we build this" "Run build.sh. Output: dist/app.exe."
```

Register your project before filing anything under it. Add it to `lib/vocab.js`:

```js
{ key: 'myproject', label: 'My Project', emoji: '\u{1F3AE}', kind: 'project',
  blurb: 'One line a newcomer would want to know about it.' },
```

then `node cli.js index`. Skipping this is the single most common mistake: entries in
an unregistered scope still store, but they render with no name and no colour, so
they look like they were dumped in global.

Check on it whenever you like with `node cli.js doctor`. It exits non-zero when
something is genuinely wrong.

---

## Several brains on one machine

Each brain picks and records its own free port in `brain.json`, so they do not
collide. `node cli.js info` prints the port. `node cli.js serve --port 9000`
overrides it.

---

## Requirements

Node 18 or newer. Nothing else. No database, no dependencies, no network access.
