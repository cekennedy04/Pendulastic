// Enumerates the files the offline-shell service worker (webapp/sw.js) must
// cache, in the exact order build-wasm.mjs writes them into
// webapp/src/build-id.js's SHELL export.
//
// This lives in its own module, imported by both build-wasm.mjs and
// tests/sw-shell.test.js, so the generator and the test that checks its
// output cannot drift apart. A test with its own copy of these rules could
// pass while the generator did something else -- which is exactly the kind
// of gap that let a stale SHELL through undetected until an offline load.
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

// Walk srcDir for .js/.css shell files. Two things are deliberately excluded
// from the generic walk and appended explicitly instead:
//  - wasm/: walking it would also sweep up the .d.ts files wasm-bindgen
//    writes alongside its two runtime outputs, which the browser never
//    fetches.
//  - build-id.js itself: build-wasm.mjs overwrites it on every run, so
//    whether it already exists on disk depends on whether this is the first
//    build ever run in this checkout. Listing it unconditionally keeps
//    SHELL identical on a clean checkout and a rebuild.
function collect(dir, base, out) {
  for (const entry of readdirSync(dir).sort()) {
    const full = path.join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (path.relative(base, full) === 'wasm') continue;
      collect(full, base, out);
      continue;
    }
    if (path.relative(base, full) === 'build-id.js') continue;
    if (entry.endsWith('.js') || entry.endsWith('.css')) {
      const rel = path.relative(base, full).split(path.sep).join('/');
      out.push(`./src/${rel}`);
    }
  }
}

// srcDir is an absolute filesystem path to webapp/src (the caller resolves
// it, since build-wasm.mjs and the test each start from a different
// import.meta.url).
export function computeShell(srcDir) {
  const discovered = [];
  collect(srcDir, srcDir, discovered);
  return [
    './',
    './index.html',
    // Listed explicitly rather than by widening the walk above: the walk only
    // scans webapp/src/ for .js/.css, and manifest.json lives at the webapp
    // root beside index.html (the conventional location, and the one a
    // relative `href="./manifest.json"` resolves to from there).
    //
    // It must be in the shell, not merely on disk: the manifest's
    // `display: "standalone"` is what makes an iOS Home Screen clip launch
    // WITHOUT browser chrome, which is the only thing that lets
    // install-gate.js's `display-mode: standalone` check ever pass. A device
    // that installed the app and then went offline would, without this
    // entry, fetch nothing for the manifest and fall back to a chromed
    // launch -- putting the install gate back up permanently on exactly the
    // offline-capable install this whole branch exists to produce.
    './manifest.json',
    ...discovered,
    './src/build-id.js',
    './src/wasm/mobile_imu_core.js',
    './src/wasm/mobile_imu_core_bg.wasm',
  ];
}

// The service worker's cache key. It MUST fold over the contents of EVERY
// file in the shell, not just the compiled wasm.
//
// Why this is not a nicety: sw.js caches under `pendulastic-${BUILD_ID}` and
// only ever populates that cache from its `install` handler. `install` fires
// only when the service worker's own module graph changes -- that is, when
// build-id.js changes. With a wasm-only hash, editing app.js, app.css or
// index.html changed neither the hash nor build-id.js, so `install` never
// fired; and with a cache-first fetch handler and no revalidation, an
// installed phone went on serving the OLD app.js indefinitely. A pure-JS bug
// fix, however critical, could not reach an installed device at all.
//
// `rootDir` is an absolute filesystem path to webapp/ (SHELL entries are
// relative to it). Lives here, beside computeShell and shared with
// tests/sw-shell.test.js, for the same reason computeShell does: a test with
// its own copy of these rules could pass while the generator did something
// else.
export function computeBuildId(rootDir, shell) {
  const hash = createHash('sha256');
  // The list ITSELF, so adding or removing an entry changes the key even if
  // the remaining bytes are untouched.
  hash.update(shell.join('\n'));
  for (const rel of shell) {
    // './' is index.html under another name (already listed separately), and
    // readFileSync on a directory throws.
    if (rel === './') continue;
    // build-id.js is the file this id is written INTO. Hashing its previous
    // contents would make each id depend on the last one, so every build
    // would mint a new key even with nothing changed -- destroying the "an
    // unchanged build never produces a new key" half of the contract. Its
    // only other content is the shell list, folded in above.
    if (rel === './src/build-id.js') continue;
    hash.update(readFileSync(path.join(rootDir, rel)));
  }
  return hash.digest('hex').slice(0, 12);
}
