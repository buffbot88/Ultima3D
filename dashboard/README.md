# Ultima3D Dashboard (Electron)

Control surface for the Ultima3D skill platform: daemon lifecycle, backend
module toggles, and managed-skill enable/update — plus a system-tray icon
that shows green when the daemon is idle, orange while a job runs, gray when
the daemon is down.

## Run

```sh
cd dashboard
npm install   # once; pulls Electron
npm start
```

The window opens on launch, then minimizes and closes to the tray
(right-click the icon for Show / Start / Restart / Stop / Quit;
double-click restores). One instance only — a second `npm start` just
focuses the running window.

Starting Ultima3D pops the dashboard up by itself — any fresh daemon boot
opens the window, which then attaches to the daemon on its own:

```sh
python -m ultima3d.server --http   # daemon + dashboard window
python -m ultima3d.skill start     # background daemon + dashboard window
python -m ultima3d.skill call ...  # first call boots both when none runs
```

Opt out (headless rigs, tests): `--no-dashboard` on either command, or
`ULTIMA3D_NO_DASHBOARD=1` in the environment. `python -m ultima3d.skill
dashboard` opens the window alone at any time.

Requirements: Node 18+, Python on PATH (`ULTIMA3D_PYTHON` overrides),
and the Ultima3D checkout one directory up (the daemon runs
`python -m ultima3d.server --http 127.0.0.1:8791` with cwd at the repo root).

## Layout

- `main.js` — daemon child lifecycle, 5s `/status` poll, tray, IPC handlers.
- `preload.js` — the only renderer bridge (`window.dash`); the UI never
  touches node APIs or the network directly.
- `daemon-client.js` — pure-node loopback HTTP client. Self-test without a
  display: start any daemon, then `node daemon-client.js [port]`.
- `renderer/` — plain HTML/JS UI (Daemon / Modules / Skills tabs).
- `icons/` — tray PNGs; regenerate with `python icons/generate_icons.py`.

## Behaviour notes

- Module toggles write `state/config.json` and take effect on daemon
  restart (the Modules tab says so; the Daemon tab has the Restart button).
  Procedural calls are refused while disabled; import-gated modules
  (live/gimp/godot) vanish from `/tools` after the restart.
- Skill "update" buttons dry-run by default (report only); Update-all (live)
  asks for confirmation, then fast-forwards the git checkouts under `skills/`.
- `brain` stays disabled until its starter kit is dropped into
  `skills/brain/` (see that folder's README) — the updater only verifies it.
- `pullstack` artifact targets need Docker + gVisor on Linux; on this
  Windows machine repo targets work and artifact work stops with a message.
- Packaging (`electron-builder` installer) is deliberately deferred; run
  from source with `npm start`.
