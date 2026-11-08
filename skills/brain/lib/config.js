#!/usr/bin/env node
'use strict';
/**
 * brain/lib/config.js - per-brain settings.
 *
 * A brain is a folder you drop into a project, which means several of them can sit
 * on one machine at the same time. If they all defaulted to port 8765, the second
 * one to start would die with EADDRINUSE and the user would have to work out which
 * brain they were looking at. So each brain keeps its own `brain.json` next to
 * cli.js, and the installer writes a free port into it on first run.
 *
 * Everything here is optional and has a working default, so a brain with no config
 * file at all behaves exactly as it did before.
 */

const fs = require('fs');
const path = require('path');

const CONFIG_PATH = path.join(__dirname, '..', 'brain.json');
const DEFAULT_PORT = 8765;

function read() {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, 'utf8');
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    return parsed;
  } catch (e) {
    // No file, or unreadable, or not JSON. A brain with a broken config must still
    // start, so this is a default, never an error.
    return {};
  }
}

/** The port this brain serves on, unless one is given explicitly. */
function port(explicit) {
  const candidates = [
    explicit,
    process.env.BRAIN_PORT,
    read().port,
    DEFAULT_PORT,
  ];
  for (const c of candidates) {
    if (c === undefined || c === null || c === '') continue;
    const n = parseInt(c, 10);
    if (Number.isInteger(n) && n > 0 && n < 65536) return n;
  }
  return DEFAULT_PORT;
}

/** A human name for this brain, used in the UI header and by `brain info`. */
function name() {
  const n = read().name;
  if (typeof n === 'string' && n.trim()) return n.trim();
  // Fall back to the folder the brain lives in, which is the project's name in
  // every real drop-in case.
  return path.basename(path.join(__dirname, '..', '..'));
}

function write(patch) {
  const next = Object.assign(read(), patch);
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(next, null, 2) + '\n', 'utf8');
  return next;
}

module.exports = { CONFIG_PATH, DEFAULT_PORT, read, port, name, write };
