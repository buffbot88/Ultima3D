# Joshtified Brain - starter kit

This is the tooling half of the brain: the CLI, the HTTP API, the visualiser, the
templates and the house rules. The memory half is deliberately absent. `entries/`
holds one welcome entry and nothing else, because a brain belongs to the person
who writes it.

## Run it

```sh
cd brain
node cli.js list
node cli.js add "First fact" "What is true, and how you know it." --type knowledge --scope global
node cli.js serve
```

Requires Node.js. There is no database, no build step and no dependency to
install: every entry is a file on disk.

## What is inside

- `brain/cli.js` and `brain/server.js` - command line and HTTP API.
- `brain/lib/` - store, vocabulary and configuration.
- `brain/ui/` - the browser visualiser.
- `brain/templates/` - starter shapes for entries.
- `brain/RULES.md` - the rules that keep a brain findable and honest.
- `brain/AGENTS.md` - the short version for an agent landing in the folder.

## Forking

Take it, rename it, change the scopes and the rules. No license file ships with
this starter; add the one that matches how you want to share your fork.
