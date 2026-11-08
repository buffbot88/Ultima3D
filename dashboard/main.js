// Ultima3D Dashboard — Electron main process.
//
// Owns the loopback daemon child (python -m ultima3d.server --http), polls
// /status for the tray icon (green idle / orange busy / gray off), hides the
// window to the tray on minimize/close, and proxies renderer IPC to the daemon
// so the UI never touches the network stack directly (no CORS surface).
'use strict';

const { app, BrowserWindow, Tray, Menu, ipcMain, shell, nativeImage } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

const daemon = require('./daemon-client');

const ROOT = path.resolve(__dirname, '..');          // Ultima3D checkout root
const PYTHON = process.env.ULTIMA3D_PYTHON || 'python';
const OUT_DIR = process.env.ULTIMA3D_OUT || path.join(ROOT, 'output');

const POLL_MS = 5000;
const ICONS = {
  idle: path.join(__dirname, 'icons', 'tray-idle.png'),
  busy: path.join(__dirname, 'icons', 'tray-busy.png'),
  off: path.join(__dirname, 'icons', 'tray-off.png'),
};

let win = null;
let tray = null;
let daemonProc = null;
let quitting = false;
let lastState = 'off';   // off | idle | busy

function log(...args) { console.log('[dashboard]', ...args); }

function iconFor(state) {
  try {
    return nativeImage.createFromPath(ICONS[state] || ICONS.off);
  } catch (e) {
    return undefined;
  }
}

// -- daemon child -----------------------------------------------------------
function daemonRunning() {
  return daemonProc !== null && daemonProc.exitCode === null;
}

function startDaemon() {
  if (daemonRunning()) return Promise.resolve(true);
  return new Promise((resolve) => {
    // The daemon child runs with cwd=ROOT so it finds blender/worker.py, but
    // its outputs must not mix into the checkout: forward OUT_DIR explicitly
    // (explicit ULTIMA3D_OUT in the environment wins over the default).
    const childEnv = { ...process.env };
    childEnv.ULTIMA3D_OUT = childEnv.ULTIMA3D_OUT || OUT_DIR;
    // The dashboard is already open: the child daemon must not open another one.
    childEnv.ULTIMA3D_NO_DASHBOARD = '1';
    daemonProc = spawn(PYTHON, ['-m', 'ultima3d.server', '--http', `${daemon.HOST}:${daemon.PORT}`],
      { cwd: ROOT, stdio: 'ignore', windowsHide: true, env: childEnv });
    daemonProc.on('error', (e) => { log('spawn failed:', e.message); daemonProc = null; });
    daemonProc.on('exit', () => { daemonProc = null; updateTray('off'); });
    const deadline = Date.now() + 60000;
    (function wait() {
      daemon.health().then(() => { log('daemon up'); resolve(true); })
        .catch(() => {
          if (!daemonRunning()) return resolve(false);
          if (Date.now() > deadline) return resolve(false);
          setTimeout(wait, 500);
        });
    })();
  });
}

function stopDaemon() {
  return daemon.shutdown().catch(() => {}).then(() => {
    if (daemonProc) {
      try { daemonProc.kill(); } catch (e) { /* already gone */ }
      daemonProc = null;
    }
    updateTray('off');
    return true;
  });
}

// -- tray -------------------------------------------------------------------
function updateTray(state) {
  if (state === lastState && tray) {
    tray.setToolTip(`Ultima3D — ${state}`);
    return;
  }
  lastState = state;
  if (tray) {
    const img = iconFor(state);
    if (img) tray.setImage(img);
    tray.setToolTip(`Ultima3D — ${state}`);
  }
  if (win && !win.isDestroyed()) win.webContents.send('dash:tray', state);
}

async function pollStatus() {
  try {
    const { status, body } = await daemon.status();
    if (status !== 200 || body.ok === false) return updateTray('off');
    updateTray(body.busy ? 'busy' : 'idle');
  } catch (e) {
    updateTray('off');
  }
}

function buildTray() {
  tray = new Tray(iconFor('off'));
  tray.setToolTip('Ultima3D — off');
  const menu = Menu.buildFromTemplate([
    { label: 'Show dashboard', click: () => { win.show(); win.focus(); } },
    { type: 'separator' },
    { label: 'Start daemon', click: () => startDaemon().then(pollStatus) },
    { label: 'Restart daemon', click: () => stopDaemon().then(() => startDaemon()).then(pollStatus) },
    { label: 'Stop daemon', click: () => stopDaemon() },
    { type: 'separator' },
    { label: 'Quit', click: () => { quitting = true; stopDaemon().then(() => app.quit()); } },
  ]);
  tray.setContextMenu(menu);
  tray.on('double-click', () => { win.show(); win.focus(); });
}

// -- window -----------------------------------------------------------------
function createWindow() {
  win = new BrowserWindow({
    width: 1020, height: 720,
    title: 'Ultima3D Dashboard',
    webPreferences: { preload: path.join(__dirname, 'preload.js') },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.on('minimize', (e) => { e.preventDefault(); win.hide(); });
  win.on('close', (e) => {
    if (!quitting) { e.preventDefault(); win.hide(); }
  });
}

// -- IPC: renderer -> main -> daemon ------------------------------------------
ipcMain.handle('dash:status', () => daemon.status().then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:tools', () => daemon.tools().then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:skills', () => daemon.skills().then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:modules', () => daemon.modules().then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:set-module', (_e, module, enabled) =>
  daemon.setModule(module, enabled).then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:set-skill', (_e, id, enabled) =>
  daemon.setSkill(id, enabled).then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:update-skill', (_e, id, live) =>
  daemon.updateSkill(id, live).then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:update-all', (_e, live) =>
  daemon.updateAll(live).then((r) => r.body).catch((e) => ({ ok: false, error: e.message })));
ipcMain.handle('dash:daemon', async (_e, action) => {
  if (action === 'start') return { ok: await startDaemon() };
  if (action === 'stop') { await stopDaemon(); return { ok: true }; }
  if (action === 'restart') { await stopDaemon(); return { ok: await startDaemon() }; }
  return { ok: false, error: `unknown action ${action}` };
});
ipcMain.handle('dash:open-output', () => shell.openPath(OUT_DIR).then((e) => ({ ok: !e, error: e })));

// -- boot --------------------------------------------------------------------
const single = app.requestSingleInstanceLock();
if (!single) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { win.show(); win.focus(); } });
  app.whenReady().then(async () => {
    createWindow();
    buildTray();
    await startDaemon();
    await pollStatus();
    setInterval(pollStatus, POLL_MS);
  });
  app.on('window-all-closed', () => { /* stay in tray; Quit comes from the tray menu */ });
  app.on('before-quit', () => { quitting = true; });
}
