# {NAME}

## This project has a brain

A **brain** in `brain/` is this project's long-term memory: durable facts, decisions,
gotchas and current state. It lives in files, outside the conversation, so that
compaction cannot summarise it away.

**Read it before you work.** Anything already recorded here should not be
rediscovered, and an existing entry should be updated rather than duplicated.

```bash
./brain/brain search "build command"        # find something
./brain/brain list --scope <project>        # everything about a project
./brain/brain tags                          # tags already in use, with counts
./brain/brain doctor                        # is the brain healthy?
```

On Windows use `brain\brain.cmd`. The launcher finds Node itself, so it works
whether or not Node is on your PATH. Every command accepts `--json`.

**Write to it once you learn something durable** - a fact you verified, a choice you
made and why, a bug that cost you an hour. Anything the shipped product needs at
runtime belongs in the project, not here; this is for what you learned.

```bash
./brain/brain add --scope <project> --type knowledge "Short title" "What is true, and how you know."
cat body.md | ./brain/brain add --scope <project> --type decision "Choice"   # long body
```

A body containing quotes or backslashes is far easier to pass with `--body @file`
than to escape in a shell.

**Two things that go wrong most often:**

- Every entry needs a real `--scope`. Register the project in `brain/lib/vocab.js`
  first, then run `./brain/brain index`, or its entries render with no name and no
  colour in the UI.
- Reuse tags that already exist (`./brain/brain tags`) rather than inventing a
  near-synonym, which splits the history in two.

**Secrets:** record where a key lives, never the key itself.

The house rules are in `brain/RULES.md` - each one exists because it was broken
once. To browse instead of reading: `./brain/brain serve`, then open
http://127.0.0.1:{PORT}/
