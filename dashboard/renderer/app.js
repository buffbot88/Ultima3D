// Dashboard renderer: talks to main only through window.dash (preload bridge).
'use strict';

const logEl = document.getElementById('log');
function log(msg) {
  logEl.textContent += `${new Date().toLocaleTimeString()} ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

document.querySelectorAll('nav button').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById(`tab-${b.dataset.tab}`).classList.add('active');
  });
});

function setDot(state, text) {
  const dot = document.getElementById('daemon-dot');
  dot.className = `dot ${state}`;
  document.getElementById('daemon-text').textContent = text;
}

window.dash.onTray((state) => {
  if (state === 'busy') setDot('busy', 'busy — job running');
  else if (state === 'idle') setDot('idle', 'idle');
  else setDot('off', 'daemon off');
});

async function refreshStatus() {
  const s = await window.dash.status();
  if (!s || s.ok === false) { setDot('off', 'daemon off'); return; }
  setDot(s.busy ? 'busy' : 'idle', s.busy ? 'busy — job running' : 'idle');
  const sess = s.session || {};
  document.getElementById('status-card').innerHTML =
    `<h3>Daemon</h3>
     <p>Tools registered: <b>${s.tools}</b> · busy: <b>${s.busy}</b></p>
     <p class="meta">session: ${sess.elapsed_seconds ?? '?'}s elapsed, ` +
    `${sess.remaining_seconds ?? '?'}s remaining (limit ${sess.limit_minutes ?? '?'} min)</p>
     <p class="meta">modules loaded: ${Object.entries((s.modules || {}).loaded || {})
       .map(([k, v]) => `${k}:${v ? 'on' : 'off'}`).join(' ')}</p>`;
}

async function refreshModules() {
  const m = await window.dash.modules();
  const list = document.getElementById('module-list');
  if (!m || m.ok === false) { list.innerHTML = '<div class="card"><p>Daemon unreachable.</p></div>'; return; }
  const flags = m.flags || {}, loaded = m.loaded || {};
  const desc = {
    procedural: 'Built-in Blender worker backend (create_3d, render_asset, finalize_asset…).',
    live: 'Live Blender GUI tools over the user\'s open Blender instance.',
    gimp: 'GIMP batch Script-Fu tools (status, exec, textures).',
    godot: 'Godot editor bridge tools (scene tree, run, screenshots).',
  };
  list.innerHTML = '';
  for (const [name, on] of Object.entries(flags)) {
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `<div class="switch"><button class="${on ? 'on' : ''}">${on ? 'enabled' : 'disabled'}</button></div>
      <h3>${name} <span class="pill">loaded: ${loaded[name] ? 'yes' : 'no'}</span></h3><p>${desc[name] || ''}</p>`;
    div.querySelector('button').addEventListener('click', async () => {
      const r = await window.dash.setModule(name, !on);
      log(`${name} -> ${!on ? 'enabled' : 'disabled'}: ${r.ok ? 'ok (restart daemon to apply)' : r.error}`);
      refreshModules();
    });
    list.appendChild(div);
  }
}

async function refreshSkills() {
  const s = await window.dash.skills();
  const list = document.getElementById('skill-list');
  if (!s || s.ok === false) { list.innerHTML = '<div class="card"><p>Daemon unreachable.</p></div>'; return; }
  list.innerHTML = '';
  for (const sk of s.skills || []) {
    const div = document.createElement('div');
    div.className = 'card';
    const pin = sk.pin ? sk.pin.slice(0, 12) : '—';
    div.innerHTML =
      `<div class="switch">
         <button data-act="toggle" class="${sk.enabled ? 'on' : ''}">${sk.enabled ? 'enabled' : 'disabled'}</button>
         <button data-act="update">update</button>
       </div>
       <h3>${sk.id} <span class="pill">${sk.kind}</span> <span class="pill">${sk.license || '?'}</span></h3>
       <p>${sk.description || ''}</p>
       <p class="meta">pin: ${pin} · installed: ${sk.installed ? 'yes' : 'no'}</p>
       ${sk.note ? `<p class="meta">note: ${sk.note}</p>` : ''}`;
    div.querySelector('[data-act="toggle"]').addEventListener('click', async () => {
      const r = await window.dash.setSkill(sk.id, !sk.enabled);
      log(`${sk.id} -> ${!sk.enabled ? 'enabled' : 'disabled'}: ${r.ok ? 'ok' : r.error}`);
      refreshSkills();
    });
    div.querySelector('[data-act="update"]').addEventListener('click', async () => {
      log(`updating ${sk.id} (dry run)…`);
      const r = await window.dash.updateSkill(sk.id, false);
      log(`${sk.id}: ${JSON.stringify(r).slice(0, 300)}`);
      refreshSkills();
    });
    list.appendChild(div);
  }
}

async function refreshAll() { await refreshStatus(); await refreshModules(); await refreshSkills(); }

document.getElementById('btn-start').addEventListener('click', async () => {
  log('starting daemon…'); const r = await window.dash.daemon('start'); log(JSON.stringify(r)); refreshAll();
});
document.getElementById('btn-restart').addEventListener('click', async () => {
  log('restarting daemon…'); const r = await window.dash.daemon('restart'); log(JSON.stringify(r)); refreshAll();
});
document.getElementById('btn-stop').addEventListener('click', async () => {
  log('stopping daemon…'); const r = await window.dash.daemon('stop'); log(JSON.stringify(r)); refreshAll();
});
document.getElementById('btn-output').addEventListener('click', () => window.dash.openOutput());
document.getElementById('btn-refresh').addEventListener('click', refreshAll);
document.getElementById('btn-update-all-dry').addEventListener('click', async () => {
  log('checking all skills (dry run)…');
  const r = await window.dash.updateAll(false);
  log(JSON.stringify(r).slice(0, 600)); refreshSkills();
});
document.getElementById('btn-update-all-live').addEventListener('click', async () => {
  if (!window.confirm('Fetch live updates for all git skills?')) return;
  log('updating all skills (live)…');
  const r = await window.dash.updateAll(true);
  log(JSON.stringify(r).slice(0, 600)); refreshSkills();
});

refreshAll();
setInterval(refreshStatus, 5000);
