/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};
const has = (name) => process.argv.includes(name);

function say(msg) {
  process.stdout.write(msg + '\n');
}

/**
 * Ask the OS for a free port rather than guessing one.
 *
 * Several brains can live on one machine. If they all defaulted to 8765 the
 * second one to start would die with EADDRINUSE, so this asks for a real free
 * port and records it in brain.json for next time.
 */
function findFreePort(start, attemptsLeft) {
  attemptsLeft = attemptsLeft === undefined ? 40 : attemptsLeft;
  return new Promise((resolve, reject) => {
    if (attemptsLeft <= 0) return reject(new Error('could not find a free port'));
    const srv = net.createServer();
    srv.once('error', () => resolve(findFreePort(start + 1, attemptsLeft - 1)));
    srv.listen(start, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

/** Fill {NAME} style placeholders. Deliberately not a template engine. */
function fill(text, vars) {
  return text.replace(/\{(\w+)\}/g, (m, key) => (vars[key] === undefined ? m : vars[key]));
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

async function main() {
  const cwd = process.cwd();
  const target = path.resolve(cwd, has('--to') ? arg('--to', 'brain') : 'brain');
  const force = has('--force');
  const name = arg('--name', path.basename(cwd));

  say('');
  say('  Installing a brain into ' + target);
  say('  ' + '-'.repeat(62));

  if (fs.existsSync(target)) {
    const existing = fs.readdirSync(target);
    if (existing.length && !force) {
      say('  Something is already at ' + target + ' (' + existing.length + ' item(s)).');
      say('  Re-run with --force to install over it. Nothing has been touched.');
      process.exitCode = 1;
      return;
    }
    say('  Installing over what is there (--force).');
  }

  let written = 0;
  for (const rel of Object.keys(FILES)) {
    const full = path.join(target, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, FILES[rel], 'utf8');
    written++;
  }

  // A fresh brain starts with a starter vocabulary, not somebody else's projects.
  const vocabPath = path.join(target, 'lib', 'vocab.js');
  if (has('--keep-scope') && fs.existsSync(vocabPath)) {
    say('  lib/vocab.js  kept your existing one (--keep-scope)');
  } else {
    fs.writeFileSync(vocabPath, STARTER_VOCAB, 'utf8');
    say('  lib/vocab.js  starter list, just "global" - add your projects');
  }

  fs.mkdirSync(path.join(target, 'entries'), { recursive: true });
  const gitkeep = path.join(target, 'entries', '.gitkeep');
  if (!fs.existsSync(gitkeep)) fs.writeFileSync(gitkeep, '');

  let port = 8765;
  try {
    port = await findFreePort(8765);
  } catch (e) {
    say('  port          could not probe for a free one, falling back to 8765');
  }
  fs.writeFileSync(
    path.join(target, 'brain.json'),
    JSON.stringify({ name: name, port: port, installed: new Date().toISOString() }, null, 2) + '\n',
    'utf8'
  );

  // One entry, so a new brain is never an empty room nobody writes in.
  fs.writeFileSync(
    path.join(target, 'entries', '00000000-0000-0000-0000-000000000001.md'),
    fill(WELCOME_ENTRY, { NAME: name, PORT: port, DATE: new Date().toISOString().slice(0, 10), STAMP: new Date().toISOString() }),
    'utf8'
  );

  // Agent instructions - but never over the user's own.
  const agentsPath = path.join(cwd, 'AGENTS.md');
  const agents = fill(AGENTS_TEMPLATE, { NAME: name, PORT: port });
  let agentsStatus;
  if (fs.existsSync(agentsPath)) {
    fs.writeFileSync(path.join(target, 'AGENTS-suggested.md'), agents, 'utf8');
    agentsStatus = 'you already have one - suggested copy at brain/AGENTS-suggested.md';
  } else {
    fs.writeFileSync(agentsPath, agents, 'utf8');
    agentsStatus = 'created, so agents here know to use it';
  }

  /* ---- verify, do not just claim ---- */
  let verified = false;
  let output = '';
  try {
    const { execFileSync } = require('child_process');
    const cli = path.join(target, 'cli.js');
    execFileSync(process.execPath, [cli, 'doctor'], { stdio: 'pipe' });
    execFileSync(process.execPath, [cli, 'index'], { stdio: 'pipe' });
    verified = true;
  } catch (e) {
    output = String(e.stdout || '') + String(e.stderr || '');
  }

  say('  files         ' + written + ' written');
  say('  entries/      empty, plus one entry that explains how to use this');
  say('  brain.json    name "' + name + '", port ' + port);
  say('  AGENTS.md     ' + agentsStatus);
  say('  doctor        ' + (verified ? 'passed - the brain works' : 'FAILED'));
  if (output) say('');
  if (!verified) say(output.split('\n').map((l) => '    ' + l).join('\n'));

  say('');
  say('  Next:');
  say('    node brain/cli.js serve');
  say('    then open   http://127.0.0.1:' + port + '/');
  say('');
  say('    node brain/cli.js add --scope global --type knowledge "First entry" "Something worth keeping."');
  say('');

  if (!verified) {
    say('  The files are in place but the brain did not verify. Read the output above.');
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((e) => {
    process.stderr.write('brain-install failed: ' + ((e && e.stack) || e) + '\n');
    process.exit(1);
  });
}

module.exports = { main };
/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
};
const has = (name) => process.argv.includes(name);

function say(msg) {
  process.stdout.write(msg + '\n');
}

/**
 * Ask the OS for a free port rather than guessing one.
 *
 * Several brains can live on one machine. If they all defaulted to 8765 the
 * second one to start would die with EADDRINUSE, so this asks for a real free
 * port and records it in brain.json for next time.
 */
function findFreePort(start, attemptsLeft) {
  attemptsLeft = attemptsLeft === undefined ? 40 : attemptsLeft;
  return new Promise((resolve, reject) => {
    if (attemptsLeft <= 0) return reject(new Error('could not find a free port'));
    const srv = net.createServer();
    srv.once('error', () => resolve(findFreePort(start + 1, attemptsLeft - 1)));
    srv.listen(start, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

/** Fill {NAME} style placeholders. Deliberately not a template engine. */
function fill(text, vars) {
  return text.replace(/\{(\w+)\}/g, (m, key) => (vars[key] === undefined ? m : vars[key]));
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

async function main() {
  const cwd = process.cwd();
  const target = path.resolve(cwd, has('--to') ? arg('--to', 'brain') : 'brain');
  const force = has('--force');
  const name = arg('--name', path.basename(cwd));

  say('');
  say('  Installing a brain into ' + target);
  say('  ' + '-'.repeat(62));

  if (fs.existsSync(target)) {
    const existing = fs.readdirSync(target);
    if (existing.length && !force) {
      say('  Something is already at ' + target + ' (' + existing.length + ' item(s)).');
      say('  Re-run with --force to install over it. Nothing has been touched.');
      process.exitCode = 1;
      return;
    }
    say('  Installing over what is there (--force).');
  }

  let written = 0;
  for (const rel of Object.keys(FILES)) {
    const full = path.join(target, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, FILES[rel], 'utf8');
    written++;
  }

  // A fresh brain starts with a starter vocabulary, not somebody else's projects.
  const vocabPath = path.join(target, 'lib', 'vocab.js');
  if (has('--keep-scope') && fs.existsSync(vocabPath)) {
    say('  lib/vocab.js  kept your existing one (--keep-scope)');
  } else {
    fs.writeFileSync(vocabPath, STARTER_VOCAB, 'utf8');
    say('  lib/vocab.js  starter list, just "global" - add your projects');
  }

  fs.mkdirSync(path.join(target, 'entries'), { recursive: true });
  const gitkeep = path.join(target, 'entries', '.gitkeep');
  if (!fs.existsSync(gitkeep)) fs.writeFileSync(gitkeep, '');

  let port = 8765;
  try {
    port = await findFreePort(8765);
  } catch (e) {
    say('  port          could not probe for a free one, falling back to 8765');
  }
  fs.writeFileSync(
    path.join(target, 'brain.json'),
    JSON.stringify({ name: name, port: port, installed: new Date().toISOString() }, null, 2) + '\n',
    'utf8'
  );

  // One entry, so a new brain is never an empty room nobody writes in.
  fs.writeFileSync(
    path.join(target, 'entries', '00000000-0000-0000-0000-000000000001.md'),
    fill(WELCOME_ENTRY, { NAME: name, PORT: port, DATE: new Date().toISOString().slice(0, 10), STAMP: new Date().toISOString() }),
    'utf8'
  );

  // Agent instructions - but never over the user's own.
  const agentsPath = path.join(cwd, 'AGENTS.md');
  const agents = fill(AGENTS_TEMPLATE, { NAME: name, PORT: port });
  let agentsStatus;
  if (fs.existsSync(agentsPath)) {
    fs.writeFileSync(path.join(target, 'AGENTS-suggested.md'), agents, 'utf8');
    agentsStatus = 'you already have one - suggested copy at brain/AGENTS-suggested.md';
  } else {
    fs.writeFileSync(agentsPath, agents, 'utf8');
    agentsStatus = 'created, so agents here know to use it';
  }

  /* ---- verify, do not just claim ---- */
  let verified = false;
  let output = '';
  try {
    const { execFileSync } = require('child_process');
    const cli = path.join(target, 'cli.js');
    execFileSync(process.execPath, [cli, 'doctor'], { stdio: 'pipe' });
    execFileSync(process.execPath, [cli, 'index'], { stdio: 'pipe' });
    verified = true;
  } catch (e) {
    output = String(e.stdout || '') + String(e.stderr || '');
  }

  say('  files         ' + written + ' written');
  say('  entries/      empty, plus one entry that explains how to use this');
  say('  brain.json    name "' + name + '", port ' + port);
  say('  AGENTS.md     ' + agentsStatus);
  say('  doctor        ' + (verified ? 'passed - the brain works' : 'FAILED'));
  if (output) say('');
  if (!verified) say(output.split('\n').map((l) => '    ' + l).join('\n'));

  say('');
  say('  Next:');
  say('    node brain/cli.js serve');
  say('    then open   http://127.0.0.1:' + port + '/');
  say('');
  say('    node brain/cli.js add --scope global --type knowledge "First entry" "Something worth keeping."');
  say('');

  if (!verified) {
    say('  The files are in place but the brain did not verify. Read the output above.');
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((e) => {
    process.stderr.write('brain-install failed: ' + ((e && e.stack) || e) + '\n');
    process.exit(1);
  });
}

module.exports = { main };
