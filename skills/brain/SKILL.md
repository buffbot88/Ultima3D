---
name: brain
description: >
  Filesystem-backed shared memory for coding agents: durable facts, decisions
  and session state as plain Markdown files (no database, no dependencies).
  Use when the user says "brain", or to record/retrieve project knowledge:
  read with `node skills/brain/cli.js list|search|get`, write with `add`,
  serve the HTTP API + visualiser with `serve` (default
  http://127.0.0.1:8791). Zero dependencies, Node 18+.
license: unknown
---

# Brain

This workspace's shared memory. Four entry kinds: `knowledge` (durable facts,
how-tos, conventions), `memory` (working context, current state), `decision`
(choices plus why), `note` (anything else). One Markdown file per entry under
`entries/`, frontmatter block first — write through the CLI so the format is
always right.

## CLI (run from the repo root)

```sh
node skills/brain/cli.js list [--scope <s>] [--type <t>]
node skills/brain/cli.js search "<query>" [--scope <s>]
node skills/brain/cli.js get <id>
node skills/brain/cli.js add --type knowledge --scope <s> --tags "a,b" "Title" --body @file.md
node skills/brain/cli.js update <id> --body @file.md
node skills/brain/cli.js delete <id>
node skills/brain/cli.js tags
node skills/brain/cli.js doctor     # self-check, non-zero when wrong
node skills/brain/cli.js serve [--port N]   # HTTP API + visualiser
```

Windows: `skills\brain\brain.cmd <cmd> ...`. Git Bash: `./skills/brain/brain <cmd> ...`.

## HTTP API (`serve`, default http://127.0.0.1:8791)

`GET /health`, `GET /api/entries?type=&tag=&scope=`, `GET /api/entries/:id`,
`POST /api/entries`, `PATCH /api/entries/:id`, `DELETE /api/entries/:id`,
`GET /api/search?q=`, `GET /api/tags`, plain-text docs at `/api-docs`.
Override the port with `BRAIN_PORT` or `--port`. Each brain records its own
port in `brain.json`, so several brains on one machine do not collide.

## Agent etiquette (from the starter's house rules)

- Read before acting: `list` / `search` first; update an existing entry
  rather than adding a near-duplicate.
- Write after learning: durable fact -> `knowledge`, what you tried plus the
  result -> `memory`, a choice plus its why -> `decision`.
- Always pass `--scope`; register a new project scope in `lib/vocab.js`
  before filing entries under it, then `node skills/brain/cli.js index`.
- Keep bodies small (under ~5 KB); big artifacts belong in the project.
- Full rules: `skills/brain/RULES.md`. Agent notes: `skills/brain/AGENTS.md`.
- Visualiser: `skills/brain/README-visualizer.md`.

## Provenance

Vendored starter from joshtified.com (files dated 2026-10-24; see
`README-FORK.md`). No license file ships with the starter — add one that
matches how this fork is shared. The starter's `entries/` holds one welcome
entry only; everything added here belongs to this desk.
