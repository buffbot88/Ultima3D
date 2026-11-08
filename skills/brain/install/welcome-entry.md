---
id: 00000000-0000-0000-0000-000000000001
type: memory
title: "This brain was installed on {DATE}"
tags: [brain]
scope: global
created: {STAMP}
updated: {STAMP}
---

This brain started empty. It is the storage closet behind {NAME}: the project folder
is the desk, and this is where anything worth keeping goes, because a conversation
can be compacted and these files cannot.

## How to use it

- **Read before working.** `./brain/brain search "<topic>"` and
  `./brain/brain list --scope <project>`.
- **Write once you learn something durable.** A fact you verified, a choice you made
  and why, a bug that cost you an hour.
- **Register a project before filing anything under it**: add it to
  `brain/lib/vocab.js`, then run `./brain/brain index`.
- **Check it is healthy**: `./brain/brain doctor`. It exits non-zero when something
  is actually wrong.
- **Browse it**: `./brain/brain serve`, then open http://127.0.0.1:{PORT}/

## What belongs here and what does not

| | |
|---|---|
| Here | Verified facts, decisions with their reasons, gotchas, current state, conventions |
| The project | Anything the shipped product needs at runtime: source, config, build output |

If a future session would have to rediscover it, it belongs here. If the product
needs it, it belongs in the code.

The house rules are in `brain/RULES.md`. Each one exists because it was broken once,
not because it reads well.
---
id: 00000000-0000-0000-0000-000000000001
type: memory
title: "This brain was installed on {DATE}"
tags: [brain]
scope: global
created: {STAMP}
updated: {STAMP}
---

This brain started empty. It is the storage closet behind {NAME}: the project folder
is the desk, and this is where anything worth keeping goes, because a conversation
can be compacted and these files cannot.

## How to use it

- **Read before working.** `./brain/brain search "<topic>"` and
  `./brain/brain list --scope <project>`.
- **Write once you learn something durable.** A fact you verified, a choice you made
  and why, a bug that cost you an hour.
- **Register a project before filing anything under it**: add it to
  `brain/lib/vocab.js`, then run `./brain/brain index`.
- **Check it is healthy**: `./brain/brain doctor`. It exits non-zero when something
  is actually wrong.
- **Browse it**: `./brain/brain serve`, then open http://127.0.0.1:{PORT}/

## What belongs here and what does not

| | |
|---|---|
| Here | Verified facts, decisions with their reasons, gotchas, current state, conventions |
| The project | Anything the shipped product needs at runtime: source, config, build output |

If a future session would have to rediscover it, it belongs here. If the product
needs it, it belongs in the code.

The house rules are in `brain/RULES.md`. Each one exists because it was broken once,
not because it reads well.
