// Regenerates webapp/src/wasm/ from mobile-imu-core/src/. Run via
// `npm run build:wasm` from webapp/.
//
// Why this exists as a script rather than a shell one-liner in package.json:
// npm runs scripts through cmd.exe on Windows, where the pipeline CI uses to
// read the pinned wasm-bindgen version out of Cargo.lock (grep/cut) does not
// exist. This does the same two steps -- cargo build for
// wasm32-unknown-unknown, then wasm-bindgen --target web -- with Node
// builtins only (no new dependencies), so one command works on every machine
// a developer might have.
//
// Why the generated output is not committed: see webapp/README.md. The short
// version is that the .wasm embeds the building machine's cargo registry
// path, source paths with that OS's separators, and a rustc version/commit
// string, so no two machines produce identical bytes. Building is therefore
// the only way to be sure the browser runs the same maths `cargo test`
// verifies -- which is why webapp/tests/worker.test.js refuses to run
// without this output rather than skipping.

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = new URL('../../', import.meta.url);
const manifest = fileURLToPath(new URL('mobile-imu-core/Cargo.toml', repoRoot));
const lockfile = fileURLToPath(new URL('mobile-imu-core/Cargo.lock', repoRoot));
const wasmIn = fileURLToPath(
  new URL('mobile-imu-core/target/wasm32-unknown-unknown/release/mobile_imu_core.wasm', repoRoot),
);
const outDir = fileURLToPath(new URL('webapp/src/wasm', repoRoot));
const wasmOut = fileURLToPath(new URL('webapp/src/wasm/mobile_imu_core_bg.wasm', repoRoot));
const srcDir = fileURLToPath(new URL('webapp/src', repoRoot));
const buildIdOut = fileURLToPath(new URL('webapp/src/build-id.js', repoRoot));

function die(message) {
  console.error(`\nbuild:wasm failed\n\n${message}\n`);
  process.exit(1);
}

// The `wasm-bindgen` CLI must match the `wasm-bindgen` CRATE version resolved
// in Cargo.lock. A mismatch does not merely produce cosmetic differences: it
// produces JS bindings the compiled .wasm does not actually agree with.
// Matched on the exact quoted name so `wasm-bindgen-backend` and friends,
// which carry their own versions, cannot be picked up instead.
function pinnedVersion() {
  const lines = readFileSync(lockfile, 'utf8').split(/\r?\n/);
  const i = lines.findIndex((l) => l.trim() === 'name = "wasm-bindgen"');
  if (i === -1) die(`could not find a wasm-bindgen package entry in ${lockfile}`);
  const v = lines.slice(i + 1, i + 4).find((l) => l.trim().startsWith('version = '));
  if (!v) die(`found wasm-bindgen in ${lockfile} but no version line after it`);
  return v.split('"')[1];
}

// No `shell: true`: both binaries are real executables that Node resolves off
// PATH on its own, and going through cmd.exe would leave the absolute paths
// below unquoted.
function run(cmd, args) {
  console.log(`$ ${cmd} ${args.join(' ')}`);
  execFileSync(cmd, args, { stdio: 'inherit' });
}

const version = pinnedVersion();
console.log(`Cargo.lock pins wasm-bindgen ${version}`);

// Preflight the CLI before spending a release build on it, and say exactly
// how to fix both "not installed" and "wrong version" -- the second is the
// dangerous one, because it succeeds and produces subtly wrong bindings.
let cliVersion = null;
try {
  cliVersion = execFileSync('wasm-bindgen', ['--version'], {
    encoding: 'utf8',
    // stderr ignored so a bare "not recognized" from the OS does not print
    // ahead of the actionable message below.
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim();
} catch {
  die(
    `wasm-bindgen CLI not found on PATH.\n\n` +
      `    cargo install wasm-bindgen-cli --version ${version}\n\n` +
      `The version must match the wasm-bindgen crate in mobile-imu-core/Cargo.lock.`,
  );
}
if (!cliVersion.includes(version)) {
  die(
    `wasm-bindgen CLI reports "${cliVersion}" but Cargo.lock pins ${version}.\n` +
      `A mismatched CLI emits bindings the compiled .wasm does not agree with.\n\n` +
      `    cargo install wasm-bindgen-cli --version ${version} --force\n`,
  );
}
console.log(`wasm-bindgen CLI: ${cliVersion}`);

run('cargo', ['build', '--manifest-path', manifest, '--release', '--target', 'wasm32-unknown-unknown']);
run('wasm-bindgen', [wasmIn, '--out-dir', outDir, '--target', 'web']);

console.log(`\nWrote bindings to ${outDir}`);

// The offline-shell service worker (webapp/sw.js) needs a cache key that
// changes exactly when the compiled maths changes, and a file list that
// cannot go stale as later work adds modules under src/. Both are derived
// here, right after the artifact they describe exists, rather than
// hand-maintained -- a rebuilt wasm always produces a new key; an unchanged
// one never does, and a new src/*.js file is picked up on the next build
// with no separate list to remember to update.
const buildId = createHash('sha256').update(readFileSync(wasmOut)).digest('hex').slice(0, 12);

// Walk webapp/src/ for .js/.css shell files. Two things are deliberately
// excluded from the generic walk and appended explicitly instead:
//  - wasm/: walking it would also sweep up the .d.ts files wasm-bindgen
//    writes alongside its two runtime outputs, which the browser never
//    fetches.
//  - build-id.js itself: this script overwrites it below, so whether it
//    already exists on disk depends on whether this is the first build ever
//    run in this checkout. Listing it unconditionally keeps SHELL identical
//    on a clean checkout and a rebuild.
function collectShellFiles(dir, base, out) {
  for (const entry of readdirSync(dir).sort()) {
    const full = path.join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (path.relative(base, full) === 'wasm') continue;
      collectShellFiles(full, base, out);
      continue;
    }
    if (path.relative(base, full) === 'build-id.js') continue;
    if (entry.endsWith('.js') || entry.endsWith('.css')) {
      const rel = path.relative(base, full).split(path.sep).join('/');
      out.push(`./src/${rel}`);
    }
  }
}

const discovered = [];
collectShellFiles(srcDir, srcDir, discovered);

const shell = [
  './',
  './index.html',
  ...discovered,
  './src/build-id.js',
  './src/wasm/mobile_imu_core.js',
  './src/wasm/mobile_imu_core_bg.wasm',
];

writeFileSync(
  buildIdOut,
  `// GENERATED by scripts/build-wasm.mjs -- do not edit.\n` +
    `// BUILD_ID changes whenever the compiled wasm changes; SHELL is every file\n` +
    `// the offline service worker (sw.js) must cache to run without a network.\n` +
    `export const BUILD_ID = '${buildId}';\n` +
    `export const SHELL = [\n${shell.map((f) => `  '${f}',`).join('\n')}\n];\n`,
);
console.log(`build id ${buildId}`);
console.log(`shell: ${shell.length} files`);
