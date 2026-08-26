import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildDist } from '../scripts/build-dist.mjs';
import { SHELL } from '../src/build-id.js';

// webapp/dist/ is what actually gets uploaded to a static host. A missing
// shell entry fails cache.addAll() as a unit in sw.js's install handler --
// the service worker never installs, and the app breaks ONLY offline, which
// is the hardest version of this bug to notice (see sw.js's own header).
//
// This builds into a TEMP directory, never the real webapp/dist/. Building
// the real one here would make `npm test` silently rewrite the deploy
// artifact: the documented release sequence is build:dist -> `git checkout --
// webapp/src/build-id.js` (that file is generated but tracked) -> upload, and
// any `npm test` in between would re-copy the now-reverted, stale build-id.js
// over the good one in dist/. Deploying that ships a BUILD_ID that does not
// describe the shell shipped beside it, so every installed phone is stuck on
// the old cache forever. That exact class of stale-BUILD_ID bug already
// shipped once (786ad30). Same reason sw-shell.test.js uses a temp dir.

const webappRoot = fileURLToPath(new URL('../', import.meta.url));
const srcDir = path.join(webappRoot, 'src');
const distDir = mkdtempSync(path.join(tmpdir(), 'pendulastic-dist-'));

after(() => rmSync(distDir, { recursive: true, force: true, maxRetries: 3 }));

// Run once for the whole file: building is a real filesystem copy (including
// the wasm pair), and every test below just makes assertions about the
// result.
buildDist(webappRoot, srcDir, distDir);

// './' is index.html under another name (see shell-list.mjs), and the leading
// './' is stripped so these compare against walk()'s repo-relative paths.
const shellFiles = SHELL.filter((e) => e !== './').map((e) => e.replace(/^\.\//, ''));

function walkFiles(dir, base, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) walkFiles(full, base, out);
    else out.push(path.relative(base, full).split(path.sep).join('/'));
  }
  return out;
}

// _headers is a block format: an unindented path line, then indented
// directives belonging to that path. Parsed into blocks rather than matched
// with one regex over the whole file -- a `/sw\.js[\s\S]*?no-cache` pattern
// passes even when the /sw.js block is empty, because the lazy span runs on
// into the NEXT block's directive. An assertion that cannot fail for the
// reason it exists is worse than no assertion.
function parseHeaderBlocks(text) {
  const blocks = new Map();
  let current = null;
  for (const raw of text.split('\n')) {
    if (!raw.trim()) continue;
    if (!/^\s/.test(raw)) {
      current = raw.trim();
      blocks.set(current, []);
    } else if (current) {
      blocks.get(current).push(raw.trim());
    }
  }
  return blocks;
}

test('every SHELL entry is present in the built dist/', () => {
  const missing = shellFiles.filter((entry) => !existsSync(path.join(distDir, entry)));
  assert.deepEqual(missing, [], `missing from dist/: ${missing.join(', ')}`);
});

test('sw.js is present in the built dist/ (it is never a SHELL entry, but the page cannot run without it)', () => {
  assert.ok(existsSync(path.join(distDir, 'sw.js')));
});

test('_headers pins no-cache on the two files carrying the cache key, and application/wasm on the wasm binary', () => {
  const headersPath = path.join(distDir, '_headers');
  assert.ok(existsSync(headersPath), '_headers missing from dist/');
  const blocks = parseHeaderBlocks(readFileSync(headersPath, 'utf8'));

  // Each directive is checked inside its OWN block, so deleting it fails here.
  assert.deepEqual(blocks.get('/sw.js'), ['Cache-Control: no-cache']);
  assert.deepEqual(blocks.get('/src/build-id.js'), ['Cache-Control: no-cache']);
  assert.deepEqual(
    blocks.get('/src/wasm/mobile_imu_core_bg.wasm'),
    ['Content-Type: application/wasm'],
  );
});

// An allowlist, not a denylist. The previous form collected basenames and
// checked them against six literals ('captures', 'dev_server.py', ...), which
// a participant capture copied under its own name would pass straight
// through. dist/ is fully determined -- shell + sw.js + _headers -- so the
// stronger and simpler assertion is that it contains exactly that and
// nothing else.
test('the built dist/ contains exactly the shell, sw.js and _headers -- nothing else', () => {
  const expected = [...shellFiles, 'sw.js', '_headers'].sort();
  const actual = walkFiles(distDir, distDir).sort();
  assert.deepEqual(actual, expected);
});
