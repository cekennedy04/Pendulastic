// Enumerates the files the offline-shell service worker (webapp/sw.js) must
// cache, in the exact order build-wasm.mjs writes them into
// webapp/src/build-id.js's SHELL export.
//
// This lives in its own module, imported by both build-wasm.mjs and
// tests/sw-shell.test.js, so the generator and the test that checks its
// output cannot drift apart. A test with its own copy of these rules could
// pass while the generator did something else -- which is exactly the kind
// of gap that let a stale SHELL through undetected until an offline load.
import { readdirSync, statSync } from 'node:fs';
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
    ...discovered,
    './src/build-id.js',
    './src/wasm/mobile_imu_core.js',
    './src/wasm/mobile_imu_core_bg.wasm',
  ];
}
