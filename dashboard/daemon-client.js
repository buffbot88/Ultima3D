// Pure-node HTTP client for the Ultima3D loopback daemon (no Electron APIs,
// so it can be smoke-tested with plain `node`). All calls target 127.0.0.1.
'use strict';

const http = require('http');

const HOST = '127.0.0.1';
const PORT = 8791;

function call(path, body, port) {
  const target = port || PORT;
  const data = body === undefined ? null : JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: HOST, port: target, path, method: data === null ? 'GET' : 'POST',
      headers: data === null ? {} : {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data),
      },
      timeout: 15000,
    }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(raw || '{}') });
        } catch (e) {
          reject(new Error(`bad JSON from ${path}: ${e.message}`));
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(new Error(`timeout calling ${path}`)); });
    if (data !== null) req.write(data);
    req.end();
  });
}

module.exports = {
  HOST, PORT,
  status: (p) => call('/status', undefined, p),
  health: (p) => call('/health', undefined, p),
  tools: (p) => call('/tools', undefined, p),
  skills: (p) => call('/skills', undefined, p),
  modules: (p) => call('/modules', undefined, p),
  setModule: (module, enabled, p) => call('/modules', { module, enabled }, p),
  setSkill: (id, enabled, p) => call('/skills/enable', { id, enabled }, p),
  updateSkill: (id, live, p) => call('/skills/update', { id, dry_run: !live }, p),
  updateAll: (live, p) => call('/skills/update', { all: true, dry_run: !live }, p),
  shutdown: (p) => call('/shutdown', {}, p),
};

// node daemon-client.js <port> — self-test: prints /status keys and exits.
if (require.main === module) {
  const port = parseInt(process.argv[2] || String(PORT), 10);
  module.exports.status(port).then(({ status, body }) => {
    console.log(JSON.stringify({ http: status, keys: Object.keys(body),
      busy: body.busy, tools: body.tools }, null, 2));
  }).catch((e) => { console.error(`SELF-TEST FAILED: ${e.message}`); process.exit(1); });
}
