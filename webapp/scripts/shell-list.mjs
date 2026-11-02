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
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
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

// The two wasm artifacts are excluded from the content fold on purpose, and
// replaced by a digest of the Rust SOURCE that produces them (see
// coreSourceDigest). They are generated, and per webapp/README.md they are not
// reproducible across machines: the .wasm embeds the building machine's cargo
// registry path (including its username), that OS's path separators, and the
// rustc version and commit hash. Folding their bytes in made BUILD_ID a
// property of the machine that last ran the generator rather than of the
// source, so `the real BUILD_ID equals a fresh hash` could only ever hold
// there. Measured on one commit it produced three different ids on three
// machines -- 500cc645b5ca committed, b040e88f79b4 on the CI runner,
// 065da6ebf2c3 in a container -- which is why that assertion had failed on
// every CI run since it landed.
//
// Hashing the Rust source instead keeps the half of the contract that matters
// -- a changed algorithm still rotates the cache key, so a rebuilt wasm
// reaches an installed phone -- while making the key reproducible from the
// repository alone. It is also strictly stronger than folding in
// ALGORITHM_VERSION, which is derived from a git revision and so cannot see
// uncommitted Rust changes.
const WASM_ARTIFACTS = new Set([
  './src/wasm/mobile_imu_core.js',
  './src/wasm/mobile_imu_core_bg.wasm',
]);

// A content digest of everything that determines the compiled wasm: every
// .rs file under mobile-imu-core/src/, plus Cargo.toml and Cargo.lock so a
// dependency bump is visible too. Cargo.lock also pins the wasm-bindgen crate
// version, and build-wasm.mjs refuses to run a CLI that does not match it --
// so a change to the generated JS glue cannot happen without a change here.
//
// Paths are folded in RELATIVE to coreDir. Absolute paths would reintroduce
// the machine dependence this function exists to remove.
export function coreSourceDigest(coreDir) {
  const srcDir = path.join(coreDir, 'src');
  if (!existsSync(srcDir)) {
    throw new Error(
      `coreSourceDigest: no Rust source at ${srcDir}. BUILD_ID folds the core's ` +
        `source in place of the generated wasm, so it cannot be computed without it.`,
    );
  }
  const rels = [];
  (function walk(dir) {
    for (const entry of readdirSync(dir).sort()) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (entry.endsWith('.rs')) {
        rels.push(path.relative(coreDir, full).split(path.sep).join('/'));
      }
    }
  })(srcDir);
  for (const manifest of ['Cargo.toml', 'Cargo.lock']) {
    if (existsSync(path.join(coreDir, manifest))) rels.push(manifest);
  }
  rels.sort();
  const hash = createHash('sha256');
  // The list itself, so adding or removing a source file moves the digest
  // even when the remaining bytes are untouched.
  hash.update(rels.join('\n'));
  for (const rel of rels) hash.update(readFileSync(path.join(coreDir, rel)));
  return hash.digest('hex');
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
// relative to it). `coreDir` is mobile-imu-core/, defaulting to its position
// beside webapp/ in the repository; it is a parameter so the unit tests can
// fold a real core digest into a synthetic shell in a temp directory. Lives
// here, beside computeShell and shared with tests/sw-shell.test.js, for the
// same reason computeShell does: a test with its own copy of these rules
// could pass while the generator did something else.
export function computeBuildId(rootDir, shell, coreDir = path.resolve(rootDir, '..', 'mobile-imu-core')) {
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
    // Generated and machine-dependent; the source digest below stands in.
    if (WASM_ARTIFACTS.has(rel)) continue;
    hash.update(readFileSync(path.join(rootDir, rel)));
  }
  hash.update(coreSourceDigest(coreDir));
  return hash.digest('hex').slice(0, 12);
}
