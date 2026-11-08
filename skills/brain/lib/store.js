#!/usr/bin/env node
'use strict';
/**
 * brain/lib/store.js - core store for the shared brain.
 *
 * The source of truth is plain Markdown files with YAML frontmatter in
 * `brain/entries/`. This module parses, writes, searches and deletes those
 * files. Zero dependencies; works on any Node >= 18.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const BRAIN_DIR = path.join(__dirname, '..');
const ENTRIES_DIR = path.join(BRAIN_DIR, 'entries');

const TYPES = ['knowledge', 'memory', 'decision', 'note'];
const SCOPE_GLOBAL = 'global';

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

function ensureDirs() {
 fs.mkdirSync(ENTRIES_DIR, { recursive: true });
}

function nowIso() {
 return new Date().toISOString();
}

function genId() {
 const d = new Date();
 const stamp =
 d.getFullYear().toString() +
 String(d.getMonth() + 1).padStart(2, '0') +
 String(d.getDate()).padStart(2, '0') +
 '-' +
 String(d.getHours()).padStart(2, '0') +
 String(d.getMinutes()).padStart(2, '0') +
 String(d.getSeconds()).padStart(2, '0');
 return stamp + '-' + crypto.randomBytes(3).toString('hex');
}

function unquote(v) {
 if (v.length >= 2 && (v[0] === '"' || v[0] === "'") && v[v.length - 1] === v[0]) {
 v = v.slice(1, -1);
 v = v.replace(/\\"/g, '"').replace(/\\'/g, "'");
 }
 return v;
}

function needsQuote(v) {
 return /[:,#\[\]"']/.test(v) || v !== v.trim() || v === '';
}

function quote(v) {
 if (!needsQuote(v)) return v;
 return '"' + v.replace(/"/g, '\\"') + '"';
}

function parseTags(raw) {
 if (typeof raw !== 'string') return [];
 let inner = raw.trim();
 if (inner.startsWith('[') && inner.endsWith(']')) inner = inner.slice(1, -1);
 return inner
 .split(',')
 .map((t) => t.trim().toLowerCase())
 .filter(Boolean);
}

/* ------------------------------------------------------------------ */
/* frontmatter parsing / serialization                                 */
/* ------------------------------------------------------------------ */

const FM_KEYS = ['id', 'type', 'title', 'tags', 'scope', 'created', 'updated', 'source'];

/**
 * Parse one entry file's text into { meta, body }.
 * meta always contains id/type/title/tags/scope/created/updated/source
 * (defaults filled in).
 */
function parseEntryText(text, fallbackId) {
 const meta = {
 id: fallbackId || '',
 type: 'note',
 title: '',
 tags: [],
 scope: SCOPE_GLOBAL,
 created: '',
 updated: '',
 source: '',
 };

 let body = text;
 const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
 if (m) {
 body = (m[2] || '').replace(/^\r?\n/, '');
 for (const line of m[1].split(/\r?\n/)) {
 const idx = line.indexOf(':');
 if (idx <= 0) continue;
 const key = line.slice(0, idx).trim().toLowerCase();
 const val = line.slice(idx + 1).trim();
 if (!FM_KEYS.includes(key)) continue;
 if (key === 'tags') meta.tags = parseTags(val);
 else if (key === 'type') meta.type = unquote(val).toLowerCase();
 else meta[key] = unquote(val);
 }
 }
 if (!meta.created) meta.created = nowIso();
 if (!meta.updated) meta.updated = meta.created;
 if (!meta.type) meta.type = 'note';
 if (!meta.scope) meta.scope = SCOPE_GLOBAL;
 return { meta, body };
}

function serializeEntry(meta, body) {
 const lines = ['---'];
 lines.push('id: ' + meta.id);
 lines.push('type: ' + meta.type);
 lines.push('title: ' + quote(meta.title || ''));
 lines.push('tags: [' + (meta.tags || []).join(', ') + ']');
 lines.push('scope: ' + quote(meta.scope || SCOPE_GLOBAL));
 lines.push('created: ' + (meta.created || nowIso()));
 lines.push('updated: ' + (meta.updated || nowIso()));
 if (meta.source) lines.push('source: ' + quote(meta.source));
 lines.push('---');
 const b = (body || '').trim();
 if (b) lines.push('', b);
 return lines.join('\n') + '\n';
}

/* ------------------------------------------------------------------ */
/* file access                                                         */
/* ------------------------------------------------------------------ */

function listFiles() {
 ensureDirs();
 return fs
 .readdirSync(ENTRIES_DIR)
 .filter((f) => f.endsWith('.md') && !f.startsWith('.'))
 .sort();
}

function fileForId(id) {
 for (const f of listFiles()) {
 const text = fs.readFileSync(path.join(ENTRIES_DIR, f), 'utf8');
 const { meta } = parseEntryText(text, path.basename(f, '.md'));
 if (meta.id === id) return path.join(ENTRIES_DIR, f);
 }
 return null;
}

/** Atomic write: temp file + rename, so concurrent writers never corrupt an entry. */
function atomicWrite(file, content) {
 const tmp = file + '.' + process.pid + '.' + Date.now() + '.tmp';
 fs.writeFileSync(tmp, content, 'utf8');
 try {
 fs.renameSync(tmp, file);
 } catch (e) {
 fs.unlinkSync(tmp);
 throw e;
 }
}

function readEntryFile(file) {
 const full = path.join(ENTRIES_DIR, file);
 const text = fs.readFileSync(full, 'utf8');
 const { meta, body } = parseEntryText(text, path.basename(file, '.md'));
 meta.file = file;
 return { meta, body };
}

/* ------------------------------------------------------------------ */
/* public API                                                          */
/* ------------------------------------------------------------------ */

function listEntries() {
 return listFiles()
 .map(readEntryFile)
 .map(({ meta, body }) => ({ ...meta, body }))
 .sort((a, b) => (a.created < b.created ? 1 : a.created > b.created ? -1 : 0));
}

function getEntry(id) {
 const file = fileForId(id);
 if (!file) return null;
 const { meta, body } = parseEntryText(
 fs.readFileSync(file, 'utf8'),
 path.basename(file, '.md')
 );
 return { ...meta, body };
}

function addEntry({ type, title, tags, scope, body, source }) {
 ensureDirs();
 if (!title || !String(title).trim()) throw new Error('title is required');
 type = (type || 'note').toLowerCase();
 if (!TYPES.includes(type)) {
 throw new Error('type must be one of: ' + TYPES.join(', '));
 }
 const meta = {
 id: genId(),
 type,
 title: String(title).trim(),
 tags: Array.isArray(tags) ? tags.map((t) => String(t).trim().toLowerCase()).filter(Boolean) : parseTags(String(tags || '')),
 scope: String(scope || SCOPE_GLOBAL).trim() || SCOPE_GLOBAL,
 created: nowIso(),
 updated: nowIso(),
 source: source ? String(source) : '',
 };
 atomicWrite(path.join(ENTRIES_DIR, meta.id + '.md'), serializeEntry(meta, body || ''));
 return getEntry(meta.id);
}

function updateEntry(id, patch) {
 const file = fileForId(id);
 if (!file) throw new Error('no entry with id: ' + id);
 const parsed = parseEntryText(fs.readFileSync(file, 'utf8'), path.basename(file, '.md'));
 const meta = parsed.meta;
 let body = parsed.body;
 if (patch.type !== undefined) {
 const t = String(patch.type).toLowerCase();
 if (!TYPES.includes(t)) throw new Error('type must be one of: ' + TYPES.join(', '));
 meta.type = t;
 }
 if (patch.title !== undefined) meta.title = String(patch.title).trim();
 if (patch.tags !== undefined) {
 meta.tags = Array.isArray(patch.tags)
 ? patch.tags.map((t) => String(t).trim().toLowerCase()).filter(Boolean)
 : parseTags(String(patch.tags));
 }
 if (patch.scope !== undefined) meta.scope = String(patch.scope).trim() || SCOPE_GLOBAL;
 if (patch.source !== undefined) meta.source = String(patch.source);
 if (patch.body !== undefined && typeof patch.body === 'string') body = patch.body;
 meta.updated = nowIso();
 atomicWrite(file, serializeEntry(meta, body));
 return getEntry(id);
}

function deleteEntry(id) {
 const file = fileForId(id);
 if (!file) throw new Error('no entry with id: ' + id);
 fs.unlinkSync(file);
 return true;
}

/** Tokenize a query into lowercase words. */
function tokenize(q) {
 return String(q || '')
 .toLowerCase()
 .split(/[^\p{L}\p{N}_-]+/u)
 .filter(Boolean);
}

/**
 * Search entries. `q` requires every token to appear somewhere in
 * title+tags+body; entries are ranked (title hit > tag hit > body hit).
 * `tag`, `type` and `scope` are exact filters.
 */
function searchEntries({ q, tag, type, scope } = {}) {
 const tokens = tokenize(q);
 let results = listEntries();
 if (type) {
 // Support comma-separated types: type=knowledge,memory filters to any of them.
 const types = String(type).split(',').map((t) => t.trim().toLowerCase()).filter(Boolean);
 results = results.filter((e) => types.includes(e.type));
 }
 if (scope) results = results.filter((e) => e.scope === String(scope).toLowerCase());
 if (tag) {
 const t = String(tag).toLowerCase();
 results = results.filter((e) => e.tags.includes(t));
 }
 if (tokens.length) {
 const scored = [];
 for (const e of results) {
 const title = e.title.toLowerCase();
 const tagText = e.tags.join(' ').toLowerCase();
 const body = (e.body || '').toLowerCase();
 let score = 0;
 let all = true;
 for (const tok of tokens) {
 if (title.includes(tok)) score += 3;
 else if (tagText.includes(tok)) score += 2;
 else if (body.includes(tok)) score += 1;
 else {
 all = false;
 break;
 }
 }
 if (all) scored.push({ entry: e, score });
 }
 scored.sort((a, b) => b.score - a.score || (a.entry.created < b.entry.created ? 1 : -1));
 results = scored.map((s) => s.entry);
 }
 return results;
}

function allTags() {
 const counts = {};
 for (const e of listEntries()) for (const t of e.tags) counts[t] = (counts[t] || 0) + 1;
 return Object.entries(counts)
 .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
 .map(([tag, count]) => ({ tag, count }));
}

/**
 * Reindex: assign ids to any hand-written entries missing a frontmatter id,
 * renaming the file to `<id>.md` when it doesn't already look like one.
 * Returns the list of files that were fixed.
 */
function reindex() {
 ensureDirs();
 const fixed = [];
 for (const f of listFiles()) {
 const full = path.join(ENTRIES_DIR, f);
 const text = fs.readFileSync(full, 'utf8');
 const fm = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
 const hasId = fm ? fm[1].split(/\r?\n/).some((l) => /^id\s*:/i.test(l.trim())) : false;
 if (hasId) continue;
 const { meta, body } = parseEntryText(text, path.basename(f, '.md'));
 meta.id = genId();
 meta.created = meta.created || nowIso();
 meta.updated = meta.updated || meta.created;
 atomicWrite(full, serializeEntry(meta, body));
 const newName = meta.id + '.md';
 if (newName !== f) {
 try {
 fs.renameSync(full, path.join(ENTRIES_DIR, newName));
 } catch (e) {
 /* keep old filename; id is still in frontmatter */
 }
 }
 fixed.push(newName);
 }
 return fixed;
}

module.exports = {
 BRAIN_DIR,
 ENTRIES_DIR,
 TYPES,
 SCOPE_GLOBAL,
 ensureDirs,
 genId,
 listEntries,
 getEntry,
 addEntry,
 updateEntry,
 deleteEntry,
 searchEntries,
 allTags,
 reindex,
 parseEntryText,
 listFiles,
};