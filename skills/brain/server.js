#!/usr/bin/env node
'use strict';
/**
 * brain/server.js - HTTP API for the shared brain.
 *
 * Start with:  node brain/cli.js serve [--port 8791]
 * or:          node brain/server.js [port]
 *
 * Binds to 127.0.0.1 by default (override with BRAIN_HOST). CORS is open so
 * webviews and browser-based agents can call it directly.
 *
 *   GET    /health
 *   GET    /api/entries?type=&tag=&scope=
 *   GET    /api/entries/:id
 *   POST   /api/entries                {type,title,tags[],scope,body,source}
 *   PATCH  /api/entries/:id            any of {title,tags,type,scope,body,source}
 *   DELETE /api/entries/:id
 *   GET    /api/search?q=&tag=&type=&scope=
 *   POST   /api/search                 JSON body with the same fields
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const store = require('./lib/store');

const MAX_BODY = 1024 * 1024; // 1 MB
const UI_DIR = path.join(__dirname, 'ui');

function json(res, code, data) {
 const body = JSON.stringify(data);
 res.writeHead(code, {
 'Content-Type': 'application/json; charset=utf-8',
 'Content-Length': Buffer.byteLength(body),
 });
 res.end(body);
}

function readBody(req) {
 return new Promise((resolve, reject) => {
 let size = 0;
 const chunks = [];
 req.on('data', (c) => {
 size += c.length;
 if (size > MAX_BODY) {
 reject(new Error('body too large (max 1 MB)'));
 req.destroy();
 return;
 }
 chunks.push(c);
 });
 req.on('end', () => {
 if (!chunks.length) return resolve({});
 try {
 resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
 } catch (e) {
 reject(new Error('invalid JSON body: ' + e.message));
 }
 });
 req.on('error', reject);
 });
}

function serveFile(res, file, contentType) {
 fs.readFile(file, (err, buf) => {
 if (err) {
 res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
 return res.end(JSON.stringify({ error: 'not found' }));
 }
 res.writeHead(200, { 'Content-Type': contentType + '; charset=utf-8' });
 res.end(buf);
 });
}

function handle(req, res) {
 // CORS
 res.setHeader('Access-Control-Allow-Origin', '*');
 res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS');
 res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
 res.setHeader('Access-Control-Max-Age', '86400');
 if (req.method === 'OPTIONS') {
 res.writeHead(204);
 return res.end();
 }

 const url = new URL(req.url, 'http://localhost');
 const p = url.pathname;

 if (req.method === 'GET' && p === '/health') return json(res, 200, { ok: true, service: 'brain', time: new Date().toISOString() });

 /* the visualizer UI (brain/ui/index.html) */
 if (p === '/' && req.method === 'GET') {
 return serveFile(res, path.join(UI_DIR, 'index.html'), 'text/html');
 }

 /* static assets under /ui/ (index.html, export.html, css, js, etc.) */
 if (p.startsWith('/ui/')) {
 const rel = decodeURIComponent(p.slice(4)); // e.g. 'export.html'
 const file = path.resolve(UI_DIR, rel);
 if (file !== UI_DIR && !file.startsWith(UI_DIR + path.sep)) return json(res, 403, { error: 'forbidden' });
 return serveFile(res, file, 'text/html');
 }

 /* documentation homepage served at /docs */
 if ((p === '/docs' || p === '/docs/') && req.method === 'GET') {
 return serveFile(res, path.join(UI_DIR, 'home.html'), 'text/html');
 }

 /* sales page served at /get */
 if ((p === '/get' || p === '/get/') && req.method === 'GET') {
 return serveFile(res, path.join(UI_DIR, 'get.html'), 'text/html');
 }

 /* plain-text API reference, linked from the UI */
 if (p === '/api-docs' && req.method === 'GET') {
 const page = `<!doctype html><html><head><meta charset="utf-8"><title>Brain API</title></head>
<body style="font-family:monospace;padding:2rem">
<h1>🧠 Brain API</h1>
<p>Shared memory &amp; knowledge for all agents. Store: <code>${store.ENTRIES_DIR}</code></p>
<p><a href="/">← back to the visualizer</a></p>
<pre>GET    /health
GET    /api/entries?type=&amp;tag=&amp;scope=
GET    /api/entries/:id
POST   /api/entries                {type,title,tags[],scope,body,source}
PATCH  /api/entries/:id            {title,tags,type,scope,body,source}
DELETE /api/entries/:id
GET    /api/search?q=&amp;tag=&amp;type=&amp;scope=
POST   /api/search                 JSON body, same fields</pre>
<p>Try: <a href="/api/search?q=build">/api/search?q=build</a></p>
</body></html>`;
 res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
 return res.end(page);
 }

 const api = p.startsWith('/api/') ? p.slice(5) : null;

 try {
 if (api === 'entries' && req.method === 'GET') {
 const q = url.searchParams;
 return json(res, 200, store.searchEntries({ tag: q.get('tag'), type: q.get('type'), scope: q.get('scope') }));
 }

 if (api === 'entries' && req.method === 'POST') {
 return readBody(req)
 .then((b) => {
 const e = store.addEntry({
 type: b.type,
 title: b.title,
 tags: b.tags,
 scope: b.scope,
 body: b.body,
 source: b.source,
 });
 json(res, 201, e);
 })
 .catch((err) => json(res, 400, { error: err.message }));
 }

 if (api === 'search' && req.method === 'GET') {
 const q = url.searchParams;
 return json(res, 200, store.searchEntries({ q: q.get('q'), tag: q.get('tag'), type: q.get('type'), scope: q.get('scope') }));
 }

 if (api === 'search' && req.method === 'POST') {
 return readBody(req)
 .then((b) => json(res, 200, store.searchEntries({ q: b.q, tag: b.tag, type: b.type, scope: b.scope })))
 .catch((err) => json(res, 400, { error: err.message }));
 }

 if (api && api.startsWith('entries/')) {
 const id = decodeURIComponent(api.slice('entries/'.length));
 if (!id) return json(res, 400, { error: 'missing id' });

 if (req.method === 'GET') {
 const e = store.getEntry(id);
 return e ? json(res, 200, e) : json(res, 404, { error: 'no entry with id: ' + id });
 }
 if (req.method === 'PATCH') {
 return readBody(req)
 .then((b) => json(res, 200, store.updateEntry(id, b)))
 .catch((err) => {
 if (/no entry/.test(err.message)) return json(res, 404, { error: err.message });
 json(res, 400, { error: err.message });
 });
 }
 if (req.method === 'DELETE') {
 try {
 store.deleteEntry(id);
 return json(res, 200, { deleted: id });
 } catch (err) {
 return json(res, 404, { error: err.message });
 }
 }
 }

 if (api === 'tags' && req.method === 'GET') return json(res, 200, store.allTags());

 json(res, 404, { error: 'not found: ' + req.method + ' ' + p });
 } catch (err) {
 json(res, 500, { error: err.message });
 }
}

function start(port, host) {
 port = parseInt(port || process.env.BRAIN_PORT || '8791', 10);
 host = host || process.env.BRAIN_HOST || '127.0.0.1';
 store.ensureDirs();
 const server = http.createServer(handle);
 server.on('error', (e) => {
 if (e.code === 'EADDRINUSE') {
 console.error('brain server: port ' + port + ' already in use (set BRAIN_PORT or pass --port)');
 } else {
 console.error('brain server error:', e.message);
 }
 process.exit(1);
 });
 server.listen(port, host, () => {
 console.log('🧠 brain API listening on http://' + host + ':' + port);
 console.log('   entries: ' + store.ENTRIES_DIR);
 });
 return server;
}

if (require.main === module) start(process.argv[2]);

module.exports = { start, handle };