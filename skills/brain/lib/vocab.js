#!/usr/bin/env node
'use strict';
/**
 * brain/lib/vocab.js - the ONE place that says what a scope is and what a core
 * tag is.
 *
 * This used to be duplicated by hand in three files (SCOPE_EMOJI in ui/index.html,
 * scopeEmoji in ui/export.html, and the datalist in the new-entry dialog), which
 * is exactly how `yourcodebuddy` ended up with nine entries and no name or colour
 * in the UI. The registry now lives here, is served over the API as /api/index,
 * and the UI reads it from there (falling back to its own copy if the API is
 * unreachable, so the page still works on its own).
 *
 * Adding a project? Add it to SCOPES here. That is now the whole registration.
 */

const SCOPES = [
  { key: 'global', label: 'Global', emoji: '\u{1F310}', kind: 'workspace', blurb: 'Workspace-wide facts, brain conventions, agent protocols, ports, shared tooling.' },
  { key: 'playport', label: 'PlayPort', emoji: '\u{1F3AE}', kind: 'project', blurb: 'Game-server + web hosting panel, single Windows exe. The most active project.' },
  { key: 'professionpilot', label: 'ProfessionPilot', emoji: '\u{1F4BC}', kind: 'project', blurb: 'Career-guide SPA, guide batches written by many threads.' },
  { key: 'future-greenhouse', label: 'Future Greenhouse', emoji: '\u{1F331}', kind: 'project', blurb: 'Static 3D greenhouse site, 7 languages, nginx deploy.' },
  { key: 'project', label: 'KeepMarket', emoji: '\u{2615}', kind: 'project', blurb: 'Minecraft auction-house plugin (Paper 1.20.4, Java 17, com.keepmarket).' },
  { key: 'haul-empire', label: 'Haul Empire', emoji: '\u{1F69B}', kind: 'project', blurb: 'Idle transport tycoon. Folder no longer present.' },
  { key: 'bee-empire', label: 'Bee Empire', emoji: '\u{1F41D}', kind: 'project', blurb: 'Idle beekeeping tycoon. Folder no longer present.' },
  { key: 'pdfeditor', label: 'PdfEditor', emoji: '\u{1F4C4}', kind: 'project', blurb: 'Native Windows PDF editor (tkinter, single exe, OCR).' },
  { key: 'plugins', label: 'Plugins', emoji: '\u{1F9E9}', kind: 'project', blurb: 'BeaconShield + SpawnerBridge (Paper 1.21.1). Folder no longer present.' },
  { key: 'katunga-businessplan', label: 'Katunga Businessplan', emoji: '\u{1F4C8}', kind: 'project', blurb: 'Katunga Fresh Dutch business plan PDF. Folder no longer present.' },
  { key: 'localbuff', label: 'LocalBuff', emoji: '\u{1F50C}', kind: 'project', blurb: 'Chrome MV3 extension + Python WebSocket bridge. Renamed to YourCodeBuddy.' },
  { key: 'yourcodebuddy', label: 'YourCodeBuddy', emoji: '\u{1F9D1}\u{200D}\u{1F4BB}', kind: 'project', blurb: 'The current name of LocalBuff: chat-site coding agent with its own app window.' },
  { key: 'localbuddy', label: 'LocalBuddy', emoji: '\u{1F999}', kind: 'project', blurb: 'Local model host: llama.cpp + GGUF, OpenAI-compatible API on 8199.' },
  { key: 'frameforge', label: 'Frameforge', emoji: '\u{1F39E}\u{FE0F}', kind: 'project', blurb: 'Native Windows non-linear video editor (Java 17 + Swing).' },
  { key: 'pixelforge', label: 'PixelForge', emoji: '\u{1F58C}\u{FE0F}', kind: 'project', blurb: 'Native Windows raster image editor (Java2D + Swing).' },
  { key: 'schem-studio', label: 'Schem Studio', emoji: '\u{26CF}\u{FE0F}', kind: 'project', blurb: 'Offline Minecraft terrain and build tool: OpenGL .exe plus a single-file HTML build.' },
  { key: 'klanturen', label: 'KlantUren', emoji: '\u{23F1}\u{FE0F}', kind: 'project', blurb: 'Dutch hour registration for a fieldservice company (Next.js 16, Prisma, SQLite).' },
  { key: 'ytmusic-desktop', label: 'Ondographics', emoji: '\u{1F3B5}', kind: 'project', blurb: 'Electron music hub built to survive Windows Smart App Control.' },
  { key: 'king-crawler', label: 'King Crawler', emoji: '\u{1F451}', kind: 'project', blurb: 'Godot 4 + HTML5 2D pixel-art For the King-inspired endless dungeon grid crawler.' },
  { key: 'cubeamp', label: 'CubeAmp', emoji: '\u{1F57F}', kind: 'project', blurb: 'Ampache-compatible audio player.' },
  { key: 'crmpilot', label: 'CRMpilot', emoji: '\u{1F4BC}', kind: 'project', blurb: 'PilotCRM: one-stop-shop business CRM desktop app, Dutch primary with English summary, invoices+offers auto-send via SMTP, hours registration, export PDF/Excel/CSV.' },
  { key: 'romforge', label: 'RomForge', emoji: '\u{1F57F}', kind: 'project', blurb: 'Empty folder. No README or source files on disk.' },
  { key: 'casta-designer', label: 'Casta Designer', emoji: '\u{1F3F3}', kind: 'project', blurb: 'Kassenbouw Studio - desktop app. Empty folder aside from README.md.' },
  { key: 'pokeforge', label: 'Pokeforge', emoji: '\u{1F4A8}', kind: 'project', blurb: 'Pokemon save editor. Empty folder (only PlayPortCore subfolder).' },
  { key: 'infinite-fusion-muos', label: 'Infinite Fusion muOS', emoji: '\u{1F579}\u{FE0F}', kind: 'project', blurb: 'PortMaster-style port of Pokemon Infinite Fusion (RPG Maker XP via mkxp-z) to muOS on the Anbernic RG34XXSP.' },
  { key: 'glintwright', label: 'Glintwright', emoji: '\u{1F4DD}', kind: 'project', blurb: 'Empty folder. No README or source files on disk.' },
  { key: 'mibuild', label: 'MIBuild', emoji: '\u{1F6E0}', kind: 'project', blurb: 'Empty folder. No README or source files on disk.' },
  { key: 'uibuild', label: 'UI Build', emoji: '\u{1F3A8}', kind: 'project', blurb: 'Empty folder. No README or source files on disk.' },
  { key: 'vision-gate-handoff', label: 'Vision Gate Handoff', emoji: '\u{1F6E1}', kind: 'project', blurb: 'Empty folder. No README or source files on disk.' },
  { key: 'localbuff-e459a4f8', label: 'LocalBuff (e459a4f8)', emoji: '\u{1F50C}', kind: 'project', blurb: 'A short-lived project scope, to be retired.' },
  { key: 'playport-ce857c82', label: 'PlayPort (ce857c82)', emoji: '\u{1F3AE}', kind: 'project', blurb: 'A short-lived project scope, to be retired.' },
  { key: 'rustpot', label: 'Rustpot', emoji: '\u{1F9F0}', kind: 'project', blurb: 'Play-money Rust skin arcade: crates, coinflip, jackpot, crash, roulette, upgrade. Node server plus a vanilla ES-module frontend.' },
  { key: 'edulink', label: 'EduLink', emoji: '\u{1F393}', kind: 'project', blurb: 'Multi-role school portal (students, parents, teachers, admins) on React, Express and Prisma.' },
  { key: 'minecraft-launcher-rs', label: 'Minecraft Server Pack Builder', emoji: '\u{1F9F1}', kind: 'project', blurb: 'Native Rust egui launcher helper: pings a Minecraft server, reads the mod list it exposes, downloads those mods into a pack folder named after the server.' },
];

const REGISTERED_SCOPES = new Set(SCOPES.map((s) => s.key));

/**
 * The core tag vocabulary: the tags broad enough to be worth showing first.
 * Everything else stays on its entries and is still searchable and filterable -
 * it is just filed under "more tags" instead of cluttering the top. A tag is
 * automatically core if it names a scope.
 */
const CORE_TAGS = [
  // how the entry was earned
  'build', 'test', 'bug', 'gotcha', 'decision', 'reference', 'convention',
  'measurement', 'regression', 'tool', 'template',
  // what the entry is about
  'architecture', 'api', 'ui', 'config', 'commands', 'docs', 'deploy', 'port',
  'security', 'performance', 'database', 'plugin', 'network', 'export',
  'workflow', 'agents', 'brain', 'workspace', 'snapshot',
  // stacks that show up across projects
  'java', 'javascript', 'python', 'windows', 'native-windows', 'jpackage',
  'ffmpeg', 'swing', 'java2d', 'opengl', 'minecraft', 'paper', 'sqlite',
  'prisma', 'nextjs', 'electron', 'python-tkinter', 'node', 'html', 'css',
  // playport's own vocabulary, kept because it dominates the corpus
  'playport', 'ppcore', 'panel', 'livetest', 'live-suite', 'harness',
  'gap-queue', 'mineflayer', 'spawners', 'dungeons', 'shop', 'gate', 'lane',
  'arena', 'fixture', 'reference-config', 'create-flow', 'localbuff',
];

const CORE_TAG_SET = new Set(CORE_TAGS);

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