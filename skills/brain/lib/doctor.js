#!/usr/bin/env node
'use strict';
/**
 * brain/lib/doctor.js - is the brain still healthy?
 *
 * Every check here exists because it caught something real at least once, or
 * because it is the kind of rot that only shows up after 500 entries:
 *
 *   error    the corpus lies about itself, or an agent will get the wrong answer
 *   warning  ugly, but readable and searchable
 *   info     a number worth knowing, not a problem
 *
 * Checks are pure reads. doctor never writes; normalise and index do that.
 */

const fs = require('fs');
const path = require('path');
const store = require('./store');
const vocab = require('./vocab');
const index = require('./index');

const LEVELS = ['error', 'warning', 'info'];

/** The two characters this repo bans in every entry, title and field. */
const LONG_DASH = /[\u2013\u2014]/;

function finding(level, check, message, hint) {
 return { level, check, message, hint: hint || '' };
}

/**
 * Run every check. Returns { findings, stats }.
 * findings are sorted worst first, then by check name.
 */
function run() {
 const findings = [];
 const stats = {};

 const entries = store.listEntries();
 stats.entries = entries.length;

 const files = store.listFiles();
 stats.files = files.length;

 /* ---- files that are not entries ---------------------------------- */
 /* listFiles() ignores dotfiles, so a hidden .template.md cannot appear as an
 entry, but a normal-named file that fails to parse is a real problem. */
 const parseFailures = [];
 for (const f of files) {
  try {
  const { meta } = store.parseEntryText(
  fs.readFileSync(path.join(store.ENTRIES_DIR, f), 'utf8'),
  path.basename(f, '.md')
  );
  if (!meta.id) parseFailures.push(f + ' (no id)');
  } catch (e) {
  parseFailures.push(f + ' (' + e.message + ')');
  }
 }
 if (parseFailures.length) {
  findings.push(
  finding(
  'error',
  'unreadable',
  parseFailures.length + ' file(s) in entries/ do not parse',
  'These are not loadable entries: ' + parseFailures.slice(0, 5).join(', ')
  )
  );
 }

 /* ---- frontmatter ------------------------------------------------- */
 const noFrontmatter = [];
 const noTitle = [];
 const untitled = [];
 const badType = [];
 const noScopeTag = [];
 const noTags = [];
 const longTitles = [];
 const longDashes = [];
 const idMismatch = [];
 const seenIds = new Map();
 const seenTitles = new Map();
 const duplicateTitles = [];

 for (const f of files) {
  const text = fs.readFileSync(path.join(store.ENTRIES_DIR, f), 'utf8');
  const base = path.basename(f, '.md');
  const hasFm = /^---\r?\n/.test(text);
  if (!hasFm) noFrontmatter.push(base);

  const { meta, body } = store.parseEntryText(text, base);

  if (meta.id !== base) idMismatch.push(base + ' declares ' + meta.id);

  const title = (meta.title || '').trim();
  if (!title) noTitle.push(base);
  else {
  /* an untitled entry is one whose title carries no letters or digits, ie it is
  only punctuation or an emoji. search and INDEX both render that as nothing. */
  if (!/[\p{L}\p{N}]/u.test(title)) untitled.push(base);
  if (title.length > 120) longTitles.push(title.length + ' chars: ' + title.slice(0, 60) + '...');
  const norm = title.toLowerCase().replace(/\s+/g, ' ');
  if (seenTitles.has(norm)) duplicateTitles.push(norm);
  else seenTitles.set(norm, base);
  }

  if (!store.TYPES.includes(meta.type)) badType.push(base + ': ' + meta.type);
  if (!vocab.REGISTERED_SCOPES.has(meta.scope)) {
  findings.push(finding('error', 'unregistered-scope', base + ' uses scope "' + meta.scope + '"', 'Add it to SCOPES in brain/lib/vocab.js'));
  }
  if (meta.tags.length === 0) noTags.push(base);
  // global is not a tag: it is the absence of a project. normalise adds a
  // scope tag for projects only, so flagging global here would report 90-odd
  // correct entries as broken.
  else if (meta.scope !== store.SCOPE_GLOBAL && !meta.tags.includes(meta.scope)) noScopeTag.push(base);

  /* the no-long-dash rule, checked where it can actually break: in the title
  (it renders in the UI), the source, and the first line of the body. */
  const head = [meta.title, meta.source, body.split(/\r?\n/)[0] || ''].join(' ');
  if (LONG_DASH.test(head)) longDashes.push(base);
 }

 if (noFrontmatter.length) {
  findings.push(
  finding(
  'error',
  'frontmatter',
  noFrontmatter.length + ' entr' + (noFrontmatter.length === 1 ? 'y' : 'ies') + ' have no frontmatter block',
  'Frontmatter is mandatory. First offenders: ' + noFrontmatter.slice(0, 3).join(', ')
  )
  );
 }
 if (noTitle.length) {
  findings.push(
  finding('error', 'title', noTitle.length + ' entr' + (noTitle.length === 1 ? 'y has' : 'ies have') + ' an empty title', 'Affected: ' + noTitle.slice(0, 5).join(', '))
  );
 }
 if (untitled.length) {
  findings.push(
  finding('warning', 'title', untitled.length + ' title(s) contain no words', 'Affected: ' + untitled.slice(0, 5).join(', '))
  );
 }
 if (badType.length) {
  findings.push(
  finding('error', 'type', badType.length + ' entr' + (badType.length === 1 ? 'y has' : 'ies have') + ' a type outside ' + store.TYPES.join('/'), 'Affected: ' + badType.slice(0, 5).join(', ') + '. Run brain/tools/normalise.js --apply')
  );
 }
 if (idMismatch.length) {
  findings.push(
  finding('error', 'id', idMismatch.length + ' file name(s) disagree with the id in frontmatter', 'Affected: ' + idMismatch.slice(0, 3).join(', '))
  );
 }
 if (seenIds.size) stats.uniqueIds = seenIds.size;

 if (duplicateTitles.length) {
  findings.push(
  finding('info', 'duplicate-title', duplicateTitles.length + ' title(s) appear on more than one entry', 'Usually harmless (the same fact recorded twice). Affected: ' + duplicateTitles.slice(0, 3).join(' / '))
  );
 }
 if (longTitles.length) {
  findings.push(
  finding('warning', 'long-title', longTitles.length + ' title(s) are over 120 characters', 'They wrap badly in the sidebar. Run brain/tools/normalise.js --apply. Worst: ' + longTitles[0])
  );
 }
 if (longDashes.length) {
  findings.push(
  finding('warning', 'long-dash', longDashes.length + ' entr' + (longDashes.length === 1 ? 'y uses' : 'ies use') + ' an en or em dash where this repo bans them', 'Replace with a plain hyphen. Affected: ' + longDashes.slice(0, 5).join(', '))
  );
 }
 if (noTags.length) {
  findings.push(
  finding('info', 'untagged', noTags.length + ' entr' + (noTags.length === 1 ? 'y has' : 'ies have') + ' no tags at all', 'They only turn up in a text search. Affected: ' + noTags.slice(0, 3).join(', '))
  );
 }
 if (noScopeTag.length) {
  findings.push(
  finding('warning', 'scope-tag', noScopeTag.length + ' project-scoped entr' + (noScopeTag.length === 1 ? 'y is' : 'ies are') + ' missing their own scope as a tag', 'Run brain/tools/normalise.js --apply to add them. Affected: ' + noScopeTag.slice(0, 3).join(', '))
  );
 }

 /* ---- corpus shape ------------------------------------------------ */
 const tags = store.allTags();
 stats.tags = tags.length;
 const singletons = tags.filter((t) => t.count === 1);
 stats.singletons = singletons.length;
 findings.push(
  finding(
  'info',
  'tags',
  tags.length + ' tags in use, ' + singletons.length + ' of them on a single entry',
  'A tag used once is a detail, not vocabulary. Core tags: ' + vocab.CORE_TAGS.length + '. Promote a tag with `brain/cli.js index` only if it earns it.'
  )
  );

 /* ---- stale index ------------------------------------------------- */
 const written = index.readIndex();
 if (!written) {
  findings.push(finding('warning', 'index', 'index.json does not exist yet', 'Run: node brain/cli.js index'));
 } else {
  const live = index.buildIndex();
  stats.indexEntries = written.entryCount;
  if (written.entryCount !== live.entryCount) {
  findings.push(
  finding(
  'warning',
  'index',
  'index.json is stale: ' + written.entryCount + ' entries indexed, ' + live.entryCount + ' on disk',
  'Run: node brain/cli.js index'
  )
  );
  } else {
  findings.push(
  finding('info', 'index', 'index.json is current (' + written.entryCount + ' entries)', 'Built ' + written.generatedAt)
  );
  }
 }

 /* ---- registry hygiene -------------------------------------------- */
 const emptyScopes = vocab.SCOPES.filter((s) => {
  const n = written ? (written.counts.scopes[s.key] || 0) : null;
  return n === 0;
 });
 if (emptyScopes.length && written) {
  findings.push(
  finding('info', 'empty-scope', emptyScopes.length + ' registered scope(s) hold no entries', 'Fine if the project is retired. Candidates: ' + emptyScopes.map((s) => s.key).join(', '))
  );
 }
 const coreMissing = [];
 for (const t of vocab.CORE_TAGS) {
  if (!tags.some((x) => x.tag === t)) coreMissing.push(t);
 }
 if (coreMissing.length) {
  findings.push(
  finding('info', 'unused-core-tag', coreMissing.length + ' core tags are not used by any entry', 'Prune CORE_TAGS in brain/lib/vocab.js, or tag the entries. Unused: ' + coreMissing.slice(0, 8).join(', ') + (coreMissing.length > 8 ? '...' : ''))
  );
 }

 /* ---- dangling cross references ----------------------------------- */
 /* entry ids cited in bodies that no longer resolve. Cheap to check, and a
 dangling "see 2026..." is a dead end for exactly the reader who needs it. */
 const ids = new Set(entries.map((e) => e.id));
 const dangling = [];
 for (const e of entries) {
  const body = e.body || '';
  const re = /\b(20\d{6}-\d{6}-[0-9a-f]{6})\b/g;
  let m;
  const missingHere = new Set();
  while ((m = re.exec(body))) {
  if (!ids.has(m[1]) && !missingHere.has(m[1])) {
  missingHere.add(m[1]);
  dangling.push(m[1] + ' (cited by ' + e.id + ')');
  }
  }
 }
 if (dangling.length) {
  findings.push(
  finding('info', 'dangling-ref', dangling.length + ' entry id reference(s) do not resolve', 'The cited entry was renamed or deleted. Affected: ' + dangling.slice(0, 5).join(', ') + (dangling.length > 5 ? '...' : ''))
  );
 }

 /* ---- files that belong outside entries/ -------------------------- */
 /* store.listFiles() only sees *.md, so a stray template with a .md suffix would
 quietly read as an entry. Point it at brain/templates/. */
 const lookalikes = files.filter((f) => /^(template|example|todo|scratch|untitled)/i.test(path.basename(f, '.md')));
 if (lookalikes.length) {
  findings.push(
  finding('warning', 'stray-file', lookalikes.length + ' file(s) in entries/ look like working files', 'Templates and drafts belong in brain/templates/. Affected: ' + lookalikes.join(', '))
  );
 }

 const rank = { error: 0, warning: 1, info: 2 };
 findings.sort((a, b) => (rank[a.level] - rank[b.level]) || a.check.localeCompare(b.check));

 stats.errors = findings.filter((f) => f.level === 'error').length;
 stats.warnings = findings.filter((f) => f.level === 'warning').length;
 stats.infos = findings.filter((f) => f.level === 'info').length;

 return { findings, stats };
}

module.exports = { run, LEVELS, LONG_DASH };
