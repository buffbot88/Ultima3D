#!/usr/bin/env node
'use strict';
/**
 * brain/cli.js - command-line interface to the shared brain.
 *
 *   node brain/cli.js add "Title" "Body..." --type knowledge --tags a,b --scope playport
 *   node brain/cli.js search "query" --tag x --type y --scope z
 *   node brain/cli.js list --type memory
 *   node brain/cli.js get <id>
 *   node brain/cli.js update <id> --body "..." 
 *   node brain/cli.js delete <id>
 *   node brain/cli.js tags
 *   node brain/cli.js reindex
 *   node brain/cli.js serve [--port 8791]
 *
 * Run from the workspace root so the relative path `brain/cli.js` resolves.
 */

const fs = require('fs');
const store = require('./lib/store');

/* ------------------------------------------------------------------ */
/* tiny argument parser                                                */
/* ------------------------------------------------------------------ */

const FLAG_ALIASES = {
 '-t': '--type',
 '-s': '--scope',
 '-g': '--tag',
 '-p': '--port',
 '-j': '--json',
};

function parseArgs(argv) {
 const flags = {};
 const positional = [];
 for (let i = 0; i < argv.length; i++) {
 let a = argv[i];
 if (FLAG_ALIASES[a]) a = FLAG_ALIASES[a];
 if (a.startsWith('--')) {
 const name = a.slice(2);
 const eq = name.indexOf('=');
 if (eq >= 0) {
 flags[name.slice(0, eq)] = name.slice(eq + 1);
 } else {
 const next = argv[i + 1];
 if (next !== undefined && !next.startsWith('--')) {
 flags[name] = next;
 i++;
 } else {
 flags[name] = true;
 }
 }
 } else {
 positional.push(a);
 }
 }
 // @file expansion: --body @path (or --title @path) reads the value from a file.
 // Plain-text bodies with quotes/backslashes are painful to escape in bash; a file is safe.
 for (const key of ['body', 'title', 'source']) {
 const v = flags[key];
 if (typeof v === 'string' && v.startsWith('@') && v.length > 1) {
 const p = v.slice(1);
 try {
 flags[key] = fs.readFileSync(p, 'utf8').replace(/\r\n/g, '\n').replace(/\n+$/, '');
 } catch (e) {
 throw new Error('cannot read @' + key + ' file ' + p + ': ' + e.message);
 }
 }
 }
 return { flags, positional };
}

/* ------------------------------------------------------------------ */
/* output helpers                                                      */
/* ------------------------------------------------------------------ */

function oneLine(e) {
 const tags = e.tags.length ? ' [' + e.tags.join(', ') + ']' : '';
 return e.id + '  ' + e.type.padEnd(9) + ' ' + e.scope.padEnd(16) + tags + '  ' + e.title;
}

function snippetOf(e, q, len) {
 const body = (e.body || '').replace(/\s+/g, ' ').trim();
 if (!body) return '';
 const toks = String(q || '')
 .toLowerCase()
 .split(/\s+/)
 .filter((t) => t.length > 2);
 if (toks.length) {
 const lower = body.toLowerCase();
 let at = -1;
 for (const t of toks) {
 const i = lower.indexOf(t);
 if (i >= 0) {
 at = i;
 break;
 }
 }
 if (at >= 0) {
 const start = Math.max(0, at - 40);
 return (start > 0 ? '…' : '') + body.slice(start, start + len) + (start + len < body.length ? '…' : '');
 }
 }
 return body.slice(0, len) + (body.length > len ? '…' : '');
}

function printList(entries, { json, q }) {
 if (json) {
 console.log(JSON.stringify(entries, null, 2));
 return;
 }
 if (!entries.length) {
 console.log('(no entries found)');
 return;
 }
 for (const e of entries) {
 const snip = snippetOf(e, q, 120);
 console.log(oneLine(e) + (snip ? '\n    ' + snip : ''));
 }
 console.log('\n' + entries.length + ' entr' + (entries.length === 1 ? 'y' : 'ies'));
}

/* ------------------------------------------------------------------ */
/* commands                                                            */
/* ------------------------------------------------------------------ */

const HELP = `brain - shared memory & knowledge for all agents

Usage:
 node brain/cli.js add <title> [body] [options]
 --type knowledge|memory|decision|note   (default: note)
 --tags a,b,c        comma-separated lowercase tags
 --scope <project>   project scope (default: global); e.g. playport
 --source <text>     where this came from
 --body <text>       body (alt: second positional, piped stdin, or @file - see below)
 node brain/cli.js search <query> [--tag t] [--type t] [--scope s] [--json]
 node brain/cli.js list [--type t] [--tag t] [--scope s] [--json]
 node brain/cli.js get <id>
 node brain/cli.js update <id> [--title t] [--body b] [--tags t] [--type t] [--scope s]
 node brain/cli.js delete <id>
 node brain/cli.js tags
 node brain/cli.js reindex          assign ids to hand-written entries
 node brain/cli.js serve [--port 8791]   start the HTTP API server

Short flags: -t type, -s scope, -g tag, -p port, -j json

@file syntax: --body @path reads the body from a file (same for --title/--source).
This is the RECOMMENDED way to write multi-paragraph bodies from bash - no escaping
of quotes/backslashes needed. Verify afterwards with: get <id> --json
`;

function main() {
 const argv = process.argv.slice(2);
 if (!argv.length || argv[0] === 'help' || argv[0] === '--help' || argv[0] === '-h') {
 console.log(HELP);
 return;
 }
 const cmd = argv[0];
 const { flags, positional } = parseArgs(argv.slice(1));

 try {
 switch (cmd) {
 case 'add': {
 const title = flags.title || positional[0];
 let body = flags.body !== undefined ? flags.body : positional.slice(1).join(' ');
 if (!body && !process.stdin.isTTY) body = fs.readFileSync(0, 'utf8').trim();
 const e = store.addEntry({
 type: flags.type,
 title,
 tags: flags.tags,
 scope: flags.scope,
 body,
 source: flags.source,
 });
 console.log('added ' + e.id + ' (' + e.type + ' / ' + e.scope + ')');
 if (flags.json) console.log(JSON.stringify(e, null, 2));
 break;
 }
 case 'search': {
 const q = flags.q || positional.join(' ');
 printList(
 store.searchEntries({ q, tag: flags.tag, type: flags.type, scope: flags.scope }),
 { json: !!flags.json, q }
 );
 break;
 }
 case 'list': {
 const entries = store.searchEntries({ tag: flags.tag, type: flags.type, scope: flags.scope });
 printList(entries, { json: !!flags.json });
 break;
 }
 case 'get': {
 const id = positional[0];
 if (!id) throw new Error('get needs an entry id');
 const e = store.getEntry(id);
 if (!e) throw new Error('no entry with id: ' + id);
 if (flags.json) {
 console.log(JSON.stringify(e, null, 2));
 } else {
 console.log(
 'id:      ' + e.id + '\n' +
 'type:    ' + e.type + '\n' +
 'scope:   ' + e.scope + '\n' +
 'tags:    ' + (e.tags.length ? e.tags.join(', ') : '-') + '\n' +
 'created: ' + e.created + '\n' +
 'updated: ' + e.updated +
 (e.source ? '\nsource:  ' + e.source : '') +
 '\n\n' + e.title + '\n\n' + (e.body || '')
 );
 }
 break;
 }
 case 'update': {
 const id = positional[0];
 if (!id) throw new Error('update needs an entry id');
 const patch = {};
 if (flags.title !== undefined) patch.title = flags.title;
 if (flags.body !== undefined) patch.body = flags.body;
 if (flags.tags !== undefined) patch.tags = flags.tags;
 if (flags.type !== undefined) patch.type = flags.type;
 if (flags.scope !== undefined) patch.scope = flags.scope;
 if (flags.source !== undefined) patch.source = flags.source;
 if (!Object.keys(patch).length) throw new Error('update needs at least one --title/--body/--tags/--type/--scope/--source');
 const e = store.updateEntry(id, patch);
 console.log('updated ' + e.id);
 if (flags.json) console.log(JSON.stringify(e, null, 2));
 break;
 }
 case 'delete': {
 const id = positional[0];
 if (!id) throw new Error('delete needs an entry id');
 store.deleteEntry(id);
 console.log('deleted ' + id);
 break;
 }
 case 'tags': {
 const tags = store.allTags();
 if (!tags.length) {
 console.log('(no tags yet)');
 break;
 }
 const w = Math.max(...tags.map((t) => t.tag.length));
 for (const { tag, count } of tags) {
 console.log(tag.padEnd(w + 2) + String(count).padStart(3));
 }
 break;
 }
 case 'reindex': {
 const fixed = store.reindex();
 console.log(fixed.length ? 'assigned ids: ' + fixed.join(', ') : 'all entries already have ids');
 break;
 }
 case 'serve': {
 require('./server').start(parseInt(flags.port || process.env.BRAIN_PORT || '8791', 10));
 break;
 }
 case 'init': {
 store.ensureDirs();
 console.log('brain ready at ' + store.ENTRIES_DIR);
 break;
 }
 default:
 console.error('unknown command: ' + cmd);
 console.error(HELP);
 process.exitCode = 1;
 }
 } catch (e) {
 console.error('error: ' + e.message);
 process.exitCode = 1;
 }
}

if (require.main === module) main();
module.exports = { main };