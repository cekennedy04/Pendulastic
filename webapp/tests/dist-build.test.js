import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildDist } from '../scripts/build-dist.mjs';
import { SHELL } from '../src/build-id.js';

// webapp/dist/ is what actually gets uploaded to a static host. A missing
// shell entry fails cache.addAll() as a unit in sw.js's install handler --
// the service worker never installs, and the app breaks ONLY offline, which
// is the hardest version of this bug to notice (see sw.js's own header).
// This test builds the real dist/ (gitignored, disposable -- same footing as
// src/wasm/, which npm test already requires to exist before it can run at
// all) and checks it the way a phone actually would: by what files are
// there, not by re-deriving a second list of what should be.

const webappRoot = fileURLToPath(new URL('../', import.meta.url));
const srcDir = path.join(webappRoot, 'src');
const distDir = path.join(webappRoot, 'dist');

// Run once for the whole file: building is a real filesystem copy (including
// the wasm pair), and every test below just makes assertions about the
// result.
buildDist(webappRoot, srcDir, distDir);

test('every SHELL entry is present in webapp/dist/', () => {
  const missing = SHELL.filter((entry) => entry !== './' && !existsSync(path.join(distDir, entry)));
  assert.deepEqual(missing, [], `missing from dist/: ${missing.join(', ')}`);
});

test('sw.js is present in webapp/dist/ (it is never a SHELL entry, but the page cannot run without it)', () => {
  assert.ok(existsSync(path.join(distDir, 'sw.js')));
});

test('a _headers file is present, pinning no-cache on the two files carrying the cache key and application/wasm on the wasm binary', () => {
  const headersPath = path.join(distDir, '_headers');
  assert.ok(existsSync(headersPath), '_headers missing from dist/');
  const text = readFileSync(headersPath, 'utf8');
  assert.match(text, /\/sw\.js[\s\S]*?Cache-Control:\s*no-cache/);
  assert.match(text, /\/src\/build-id\.js[\s\S]*?Cache-Control:\s*no-cache/);
  assert.match(text, /\.wasm[\s\S]*?Content-Type:\s*application\/wasm/);
});

// Everything the deployed output must NOT contain: participant-adjacent
// capture data, the dev-only server, and anything not needed at runtime.
// Checked by walking the actual output rather than re-asserting the copy
// logic's own file list, so a future change to buildDist that starts copying
// a whole directory (rather than the explicit shell + sw.js list) would still
// be caught here.
test('nothing from the exclusion list leaked into webapp/dist/', () => {
  const forbidden = ['tests', 'scripts', 'captures', 'dev_server.py', 'README.md', 'docs'];
  function walk(dir, out) {
    for (const entry of readdirSync(dir)) {
      out.push(entry);
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) walk(full, out);
    }
  }
  const entries = [];
  walk(distDir, entries);
  const leaked = forbidden.filter((name) => entries.includes(name));
  assert.deepEqual(leaked, [], `forbidden entries leaked into dist/: ${leaked.join(', ')}`);
});
