---
id: 20261024-210000-welcome
type: knowledge
title: How this brain works
tags: [agent, brain, meta]
scope: global
created: 2026-10-24T21:00:00Z
updated: 2026-10-24T21:00:00Z
source: Joshtified brain starter kit
---

This is your brain. It starts empty on purpose.

- Entries are plain Markdown files with YAML frontmatter in `entries/`. The files are the database.
- `type` is one of `knowledge`, `memory`, `decision` or `note`.
- `scope` ties an entry to a project. Add a project to `SCOPES` in `lib/vocab.js` before filing under it.
- Read with `node cli.js search <query>` or `node cli.js list`.
- Write with `node cli.js add "Title" "Body" --type knowledge --scope global`.
- Read `RULES.md` before your second entry. Search before you invent, update before you duplicate.
- Never put a secret in an entry. Write down where the secret lives instead.
