#!/usr/bin/env node
'use strict';
/**
 * brain/lib/vocab.js - the ONE place that says what a project is.
 *
 * A project gets registered by adding it to SCOPES below and then running:
 *
 *     node brain/brain index
 *
 * That is the whole registration. The API serves this list, the visualizer reads it
 * for names and colours, and the catalogue lists it. Entries written under a scope
 * that is not registered here still store fine, but they have no name anywhere and
 * `brain doctor` reports them - which is how they end up looking dumped in global.
 *
 * CORE_TAGS is the vocabulary broad enough to be worth browsing. Everything else is
 * a detail that stays on its own entry. Keep it small: a tag used once is not
 * navigation, and a vocabulary where everything appears once cannot be browsed.
 */

const SCOPES = [
  { key: 'global', label: 'Workspace', emoji: '\u{1F310}', kind: 'workspace', blurb: 'Workspace-wide facts, conventions, machine state and shared tooling.' },

  /* Add your own projects here. One line each:
   *
   * { key: 'myproject', label: 'My Project', emoji: '\u{1F3AE}', kind: 'project',
   *   blurb: 'One line a newcomer would want to know about it.' },
   */
];

const REGISTERED_SCOPES = new Set(SCOPES.map((s) => s.key));

const CORE_TAGS = [
  // how the entry was earned
  'build', 'test', 'bug', 'gotcha', 'decision', 'reference', 'convention',
  'measurement', 'regression', 'tool', 'template',
  // what the entry is about
  'architecture', 'api', 'ui', 'config', 'commands', 'docs', 'deploy', 'port',
  'security', 'performance', 'database', 'network', 'export', 'workflow',
  'agents', 'brain', 'workspace',
];

const CORE_TAG_SET = new Set(CORE_TAGS);

/** A tag is core if it is listed, or if it names a project. */
function isCoreTag(tag) {
  return CORE_TAG_SET.has(String(tag).toLowerCase()) || REGISTERED_SCOPES.has(String(tag).toLowerCase());
}

function scopeMeta(key) {
  return SCOPES.find((s) => s.key === key) || null;
}

/** The emoji for a scope, or a parcel when it is not registered (doctor flags those). */
function scopeEmoji(key) {
  const m = scopeMeta(key);
  return m ? m.emoji : '\u{1F4E6}';
}

function scopeLabel(key) {
  const m = scopeMeta(key);
  return m ? m.label : key;
}

module.exports = { SCOPES, REGISTERED_SCOPES, CORE_TAGS, CORE_TAG_SET, isCoreTag, scopeMeta, scopeEmoji, scopeLabel };
