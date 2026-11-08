#!/usr/bin/env node
'use strict';
/**
 * brain-install.js - install a brain into the current folder.
 *
 *   node brain-install.js            install into ./brain
 *   node brain-install.js --to X     install somewhere else
 *   node brain-install.js --name X   give it a name (defaults to the folder name)
 *   node brain-install.js --force    install over an existing brain
 *   node brain-install.js --keep-scope   keep an existing lib/vocab.js
 *
 * HOW YOU GOT THIS
 *
 * Either someone dropped the whole brain folder into your project, or you were
 * handed this one file and asked to install it. Either way, from inside the
 * project you want it in:
 *
 *   node brain-install.js
 *
 * WHAT HAPPENS
 *
 * Writes a complete, working brain into ./brain, picks a free port for it, writes
 * one entry explaining how to use it, and then RUNS `doctor` and `index` to prove
 * the result actually works rather than merely claiming it does.
 *
 * GENERATED FILE - do not edit.
 *
 * Built by brain/tools/build-installer.js from a real brain. Every file of that
 * brain is inside here as a string and is written back out byte for byte, which
 * is why this file can be trusted: it is not a copy anyone maintained by hand,
 * and `build-installer.js --check` fails if it ever drifts from the source.
 */

const fs = require('fs');
const path = require('path');
const net = require('net');

/* ------------------------------------------------------------------ */
/* the brain, as it will be written                                     */
/* ------------------------------------------------------------------ */

