# Brain - this workspace's shared memory

A filesystem-backed store that any agent (and the user) can read from and write to. It holds
four kinds of thing:

| Kind | `type` | Purpose |
|---|---|---|
| Knowledge | `knowledge` | Durable facts, how-tos, architecture, conventions. Lives forever. |
| Memory | `memory` | Working context: what was tried, current state, open threads. Read before acting. |
| Decision | `decision` | Choices made and *why*, so they do not get re-litigated. |
| Note | `note` | Anything else. |

## Where this brain came from

It is a copy of a brain template (`brain.zip`) that was built in another workspace. It was
extracted here and its inherited entries were cleared on Sep 15 2026, so everything in
`entries/` now belongs to this desk (D:\localbuff). The CLI, server and visualizer are the
template's code.

## Layout



brain/
README.md            this file
README-visualizer.md how to start the visualizer
cli.js               agent CLI (add / search / list / get / update / delete / tags / reindex / serve)
server.js            HTTP API server (started by: cli.js serve)
lib/store.js         core store logic (parses and writes markdown entries)
start-visualizer.bat Windows: starts the server and opens the dashboard
brain.cmd            Windows launcher for cli.js
brain                bash launcher for cli.js
ui/                  the visualizer (index.html, export.html)
entries/             one markdown file per entry - the actual database


## Entry format

Every entry is a plain Markdown file with YAML frontmatter, and **the frontmatter block must be
the first bytes of the file**:

```markdown
---
id: 20260915-210940-1b0882
type: knowledge
title: "BridgeTest build command"
tags: [localbuff, build, windows]
scope: localbuff
created: 2026-09-15T21:09:40.000Z
updated: 2026-09-15T21:09:40.000Z
source: session with user, Sep 15 2026
---
Body starts here.


A file that starts with # Heading instead of --- does not error - it is silently indexed as
an untitled note in global. Write entries with the CLI (brain\brain.cmd add) and that
cannot happen.
Rules:

id is unique; the CLI generates YYYYMMDD-HHMMSS-xxxxxx.
type is one of knowledge, memory, decision, note.
scope is localbuff (the project on this desk) or global (the brain, the workspace and
machine-wide facts). A brand new scope must be registered in SCOPE_EMOJI
(brain/ui/index.html) and scopeEmoji (brain/ui/export.html) or its entries render with no
name or colour.
Keep the body under about 5 KB. Big artifacts belong in the project, not the brain.
Bump updated when you change an entry.
Plain hyphens only. Never em dashes or en dashes, in entries, titles, tags or code.
Anything in entries/ whose name starts with a dot is a template, not an entry.

Using the CLI
node is on PATH in this workspace, so either launcher works. Run from the workspace root:

cmd / PowerShell: brain\brain.cmd <cmd> ...
bash / Git Bash: ./brain/brain <cmd> ...
direct: node brain/cli.js <cmd> ...


:: write (use --body @file for anything multi-paragraph)
brain\brain.cmd add --type knowledge --tags "localbuff,build" --scope localbuff "BridgeTest build command" --body @body.md

:: read / search
brain\brain.cmd search "build" --scope localbuff
brain\brain.cmd search "build" --type knowledge
brain\brain.cmd list --scope localbuff
brain\brain.cmd list --type memory
brain\brain.cmd get <id>
brain\brain.cmd tags

:: update / delete
brain\brain.cmd update <id> --body @body.md
brain\brain.cmd delete <id>

:: visualizer + HTTP API
brain\brain.cmd serve


Short flags: -t type, -s scope, -g tag, -j json, -p port.
Multi-paragraph bodies: write the body to a scratch file and pass --body @file. Inline quotes on
a command line mangle backslashes and braces; a file does not. Verify an important write with
brain\brain.cmd get <id>.
Visualizer and HTTP API

brain\brain.cmd serve                 ->  http://127.0.0.1:8791/


Port 8791 is deliberate: the brain this template came from runs on 8765, so this one stays clear
of it. Override with set BRAIN_PORT=9999 or serve --port 9999.
The dashboard shows every scope and what the agents have learned: entry counts by type, search,
tag filters, full entry view, add/edit/delete, and a per-scope export
(http://127.0.0.1:8791/ui/export.html) that turns one scope into a single portable markdown
file. Plain-text API reference at http://127.0.0.1:8791/api-docs.
MethodPathPurposeGET/healthliveness checkGET/api/entrieslist (?type=&tag=&scope=)GET/api/entries/:idone entry, full bodyPOST/api/entriescreate - {type,title,tags[],scope,body,source}PATCH/api/entries/:idupdateDELETE/api/entries/:iddeleteGET/api/search?q=&tag=&type=&scope=GET/api/tagsevery tag with a count
Agent etiquette

Read before you act: list --scope localbuff and search <topic> --scope localbuff first.
Write after you learn: durable fact -> knowledge; what you tried plus the result ->
memory; a choice plus its why -> decision.
Always pass --scope (the CLI defaults to global).
Update an existing entry rather than adding a near-duplicate. Search first.
Entries are shared: write them like a teammate reads them tomorrow.

The brain is just files
There is no database to corrupt and no lock files. Writes are atomic (temp file plus rename), so
concurrent agents do not clobber each other. If a script cannot run Node, an agent can still read
and write entries/*.md by hand - carefully, keeping the frontmatter block first.
