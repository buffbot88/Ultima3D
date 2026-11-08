#!/usr/bin/env node
'use strict';
/**
 * brain/lib/index.js - the catalogue.
 *
 * One pass over the corpus produces a single object that answers every
 * "what is in here" question: counts by type and scope, the tag vocabulary with
 * frequencies, and one flat record per entry. Everything else is a view of it:
 *
 *   brain/index.json   the machine surface (robots, agents, scripts)
 *   brain/INDEX.md     the human surface (a readable catalogue)
 *   /api/index         the same object, live
 *
 * Deliberately kept separate from store.js: store.js is the writer of record,
 * this is only ever a reader. Nothing here mutates an entry.
 */

const fs = require('fs');
const path = require('path');
const store = require('./store');
const vocab = require('./vocab');

const INDEX_JSON = path.join(store.BRAIN_DIR, 'index.json');
const INDEX_MD = path.join(store.BRAIN_DIR, 'INDEX.md');
const GENERATOR = 'brain/lib/index.js';
const GENERATOR_VERSION = 1;

/* ------------------------------------------------------------------ */
/* building                                                            */
/* ------------------------------------------------------------------ */

function emptyCounts() {
 return {};
}

function bump(counts, key, by) {
 if (key === undefined || key === null || key === '') return;
 counts[key] = (counts[key] || 0) + (by === undefined ? 1 : by);
}

/**
 * The catalogue. Sorted so that two runs over an unchanged corpus produce byte
 * identical output: entries newest first (as store.listEntries returns them),
 * scopes in registry order, tags by count then name.
 */
function buildIndex() {
 const entries = store.listEntries();

 const byType = emptyCounts();
 const byScope = emptyCounts();
 const tagCounts = emptyCounts();
 const records = [];

 for (const e of entries) {
  const tags = (e.tags || []).slice();
  bump(byType, e.type);
  bump(byScope, e.scope);
  for (const t of tags) bump(tagCounts, t);

  records.push({
  id: e.id,
  file: e.file || '',
  type: e.type,
  scope: e.scope,
  title: e.title || '',
  tags,
  created: e.created || '',
  updated: e.updated || '',
  source: e.source || '',
  words: (e.body || '').split(/\s+/).filter(Boolean).length,
  });
 }

 /* scopes: the registry, enriched with what the corpus actually holds, so an
 empty scope is visible as "0 entries" rather than silently missing. */
 const seenScopes = new Set(Object.keys(byScope));
 const scopes = vocab.SCOPES.map((s) => ({
  ...s,
  registered: true,
  count: byScope[s.key] || 0,
  known: seenScopes.has(s.key),
 }));
 /* scopes the corpus uses but the registry forgot: doctor flags these, and the
 catalogue shows them rather than dropping their entries on the floor. */
 for (const key of Object.keys(byScope).sort()) {
  if (vocab.REGISTERED_SCOPES.has(key)) continue;
  scopes.push({
  key,
  label: key,
  emoji: vocab.scopeEmoji(key),
  kind: 'unregistered',
  blurb: 'Not in the registry. Run `brain doctor` and add it to brain/lib/vocab.js.',
  registered: false,
  count: byScope[key],
  known: true,
  });
 }

 const allTags = Object.keys(tagCounts)
  .map((tag) => ({ tag, count: tagCounts[tag], core: vocab.isCoreTag(tag) }))
  .sort((a, b) => (b.count - a.count) || (a.tag < b.tag ? -1 : 1));
 const coreTags = allTags.filter((t) => t.core);
 const specificTags = allTags.filter((t) => !t.core);

 const types = store.TYPES.map((t) => ({ type: t, count: byType[t] || 0 }));

 const index = {
  schema: 'brain-index/1',
  generatedAt: new Date().toISOString(),
  generator: GENERATOR,
  generatorVersion: GENERATOR_VERSION,
  entryCount: records.length,
  counts: {
  entries: records.length,
  words: records.reduce((n, r) => n + r.words, 0),
  types: byType,
  scopes: byScope,
  tags: allTags.length,
  coreTags: coreTags.length,
  specificTags: specificTags.length,
  singletons: allTags.filter((t) => t.count === 1).length,
  },
  types,
  scopes,
  tags: {
  core: coreTags,
  specific: specificTags,
  registeredCore: vocab.CORE_TAGS.slice(),
  },
  entries: records,
 };
 return index;
}

/* ------------------------------------------------------------------ */
/* rendering: brain/index.json                                         */
/* ------------------------------------------------------------------ */

/**
 * Pretty printed because it is meant to be read by a human when debugging, and
 * a machine parses it without caring about the whitespace.
 */
function renderJson(index) {
 return JSON.stringify(index, null, 2) + '\n';
}

/* ------------------------------------------------------------------ */
/* rendering: brain/INDEX.md                                           */
/* ------------------------------------------------------------------ */

/** Escape the pipe so a title containing one cannot break the table. */
function cell(s) {
 return String(s === undefined || s === null ? '' : s).replace(/\|/g, '\\|');
}

function renderMd(index) {
 const out = [];
 const c = index.counts;

 out.push('# Brain index');
 out.push('');
 out.push('A catalogue of every entry in this brain, generated by `' + GENERATOR + '`.');
 out.push('Do not hand-edit it: `brain index` rewrites this file and `index.json`.');
 out.push('');
 out.push('- Generated: ' + index.generatedAt);
 out.push('- Entries: **' + c.entries + '** across ' + Object.keys(c.scopes).length + ' scopes');
 out.push('- Words: ' + c.words.toLocaleString('en-US') + ' | Tags: ' + c.tags + ' (' + c.coreTags + ' core, ' + c.specificTags + ' specific, ' + c.singletons + ' used once)');
 out.push('');
 out.push('For humans: this page. For robots: [`index.json`](index.json), `brain cli`, or `GET /api/index`.');
 out.push('');

 /* how to find things - the thing a new arrival most needs */
 out.push('## How to read this');
 out.push('');
 out.push('| If you want | Do this |');
 out.push('| --- | --- |');
 out.push('| Everything about one project | Find its section under [Scopes](#scopes) |');
 out.push('| One specific fact | `brain search "words you remember"` |');
 out.push('| Everything tagged one way | `brain list --tag <tag>` |');
 out.push('| A feed of the newest work | `brain list --scope <scope>` |');
 out.push('| What the brain believes | Read [Core vocabulary](#core-vocabulary) |');
 out.push('');

 /* scopes */
 out.push('## Scopes');
 out.push('');
 out.push('| Scope | Entries | What it is |');
 out.push('| --- | ---: | --- |');
 for (const s of index.scopes) {
  out.push(
  '| ' +
  s.emoji +
  ' ' +
  cell(s.label) +
  ' (`' +
  s.key +
  '`) | ' +
  s.count +
  ' | ' +
  cell(s.blurb) +
  ' |'
  );
 }
 out.push('');

 /* types */
 out.push('## Types');
 out.push('');
 out.push('| Type | Entries | Meaning |');
 out.push('| --- | ---: | --- |');
 const TYPE_MEANING = {
  knowledge: 'A durable fact about the world, established by evidence.',
  memory: 'Something that happened: a session outcome, a state note.',
  decision: 'A choice that was made, ideally with its reason.',
  note: 'Everything else. Working scratch and observations.',
 };
 for (const t of index.types) {
  out.push('| `' + t.type + '` | ' + t.count + ' | ' + (TYPE_MEANING[t.type] || '') + ' |');
 }
 out.push('');

 /* core vocabulary */
 out.push('## Core vocabulary');
 out.push('');
 out.push('The tags broad enough to be worth learning. Everything outside this list is a detail of one entry and lives with it.');
 out.push('');
 out.push('| Tag | Entries |');
 out.push('| --- | ---: |');
 for (const t of index.tags.core) {
  out.push('| `' + t.tag + '` | ' + t.count + ' |');
 }
 out.push('');

 /* full tag list */
 out.push('## All tags');
 out.push('');
 out.push('Every tag in the corpus by frequency. Tags used once are normal, they are just not navigable.');
 out.push('');
 out.push('| Tag | Entries | Core |');
 out.push('| --- | ---: | :---: |');
 for (const t of index.tags.core) {
  out.push('| `' + t.tag + '` | ' + t.count + ' | yes |');
 }
 for (const t of index.tags.specific) {
  out.push('| `' + t.tag + '` | ' + t.count + ' | |');
 }
 out.push('');

 /* entries by scope */
 out.push('## Entries by scope');
 out.push('');
 const byScope = new Map();
 for (const e of index.entries) {
  if (!byScope.has(e.scope)) byScope.set(e.scope, []);
  byScope.get(e.scope).push(e);
 }
 const scopeOrder = index.scopes.filter((s) => s.count > 0).map((s) => s.key);
 for (const key of scopeOrder) {
  const meta = vocab.scopeMeta(key);
  const rows = byScope.get(key) || [];
  out.push('### ' + (meta ? meta.emoji + ' ' + meta.label : '\u{1F4E6} ' + key) + ' - `' + key + '` (' + rows.length + ')');
  out.push('');
  if (meta && meta.blurb) {
   out.push('> ' + meta.blurb);
   out.push('');
  }
  /* newest first inside the scope, which is how store.listEntries hands them over */
  const withBody = rows.slice().sort((a, b) => (a.created < b.created ? 1 : a.created > b.created ? -1 : 0));
  for (const e of withBody) {
   out.push(
  '- `' +
  e.id +
  '` **' +
  e.type +
  '** [' +
  cell(e.title) +
  '](' +
  (e.file ? '../entries/' + e.file : '#') +
  ')' + (e.tags.length ? ' - `' + e.tags.join('`, `') + '`' : '') + ' - ' + (e.created || '').slice(0, 10)
  );
  }
  out.push('');
 }

 out.push('---');
 out.push('');
 out.push('Generated by `' + GENERATOR + '`. Rebuild with `node brain/cli.js index`.');
 out.push('');
 return out.join('\n');
}

/* ------------------------------------------------------------------ */
/* writing                                                             */
/* ------------------------------------------------------------------ */

function atomicWrite(file, content) {
 const tmp = file + '.' + process.pid + '.' + Date.now() + '.tmp';
 fs.writeFileSync(tmp, content, 'utf8');
 try {
  fs.renameSync(tmp, file);
 } catch (e) {
  try {
   fs.unlinkSync(tmp);
  } catch (_) {}
  throw e;
 }
}

/**
 * Write both artefacts. Returns { index, jsonPath, mdPath, json, md } where json
 * and md are the byte counts written, so the caller can report "no change" when
 * the corpus has not moved.
 */
function writeIndex(index) {
 index = index || buildIndex();
 const json = renderJson(index);
 const md = renderMd(index);
 store.ensureDirs();
 if (!fs.existsSync(INDEX_JSON) || fs.readFileSync(INDEX_JSON, 'utf8') !== json) atomicWrite(INDEX_JSON, json);
 if (!fs.existsSync(INDEX_MD) || fs.readFileSync(INDEX_MD, 'utf8') !== md) atomicWrite(INDEX_MD, md);
 return { index, jsonPath: INDEX_JSON, mdPath: INDEX_MD, jsonBytes: Buffer.byteLength(json), mdBytes: Buffer.byteLength(md) };
}

/** Read the written index, or null when it is absent or unreadable. */
function readIndex() {
 try {
  return JSON.parse(fs.readFileSync(INDEX_JSON, 'utf8'));
 } catch (e) {
  return null;
 }
}

module.exports = {
 INDEX_JSON,
 INDEX_MD,
 buildIndex,
 renderJson,
 renderMd,
 writeIndex,
 readIndex,
};
