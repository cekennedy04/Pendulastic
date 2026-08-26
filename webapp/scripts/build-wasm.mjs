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
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { computeShell, computeBuildId } from './shell-list.mjs';

const repoRoot = new URL('../../', import.meta.url);
const manifest = fileURLToPath(new URL('mobile-imu-core/Cargo.toml', repoRoot));
const lockfile = fileURLToPath(new URL('mobile-imu-core/Cargo.lock', repoRoot));
const wasmIn = fileURLToPath(
  new URL('mobile-imu-core/target/wasm32-unknown-unknown/release/mobile_imu_core.wasm', repoRoot),
);
const outDir = fileURLToPath(new URL('webapp/src/wasm', repoRoot));
const wasmOut = fileURLToPath(new URL('webapp/src/wasm/mobile_imu_core_bg.wasm', repoRoot));
const srcDir = fileURLToPath(new URL('webapp/src', repoRoot));
const webappRoot = new URL('webapp/', repoRoot);
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
// The crate version that produced the scoring params, per spec §3.2. Read
// from Cargo.toml rather than hard-coded, so it cannot silently disagree with
// the crate that was actually just built.
function crateVersion() {
  const text = readFileSync(manifest, 'utf8');
  // Only the [package] section's own `version = ` line -- stop at the next
  // section header so a dependency's version can never be picked up instead.
  const pkg = text.split(/^\s*\[/m).find((s) => s.startsWith('package]'));
  const m = pkg && pkg.match(/^\s*version\s*=\s*"([^"]+)"/m);
  if (!m) die(`could not read the [package] version from ${manifest}`);
  return m[1];
}

// The source revision that produced the wasm. Best-effort: a checkout with no
// git (a release tarball, a vendored copy) still builds, it just cannot claim
// a revision -- and says so rather than inventing one.
function gitRevision() {
  try {
    return execFileSync('git', ['rev-parse', '--short=12', 'HEAD'], {
      cwd: fileURLToPath(repoRoot),
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim() || 'nogit';
  } catch {
    return 'nogit';
  }
}

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

// The scan itself lives in shell-list.mjs, shared with
// tests/sw-shell.test.js, so the generator and the test that checks its
// output cannot drift apart.
const shell = computeShell(srcDir);

// ---------------------------------------------------------------------------
// Two DIFFERENT identifiers are emitted below, and conflating them was a real
// defect. They answer different questions and must change on different
// triggers:
//
//   BUILD_ID          -- "is the offline shell on this phone current?"
//   ALGORITHM_VERSION -- "which source revision scored this trial?"
// ---------------------------------------------------------------------------

// BUILD_ID: the service-worker cache key, folded over every file in SHELL --
// the wasm among them, since it is a shell entry. See computeBuildId's doc
// comment in shell-list.mjs for why a wasm-only hash meant a JS or CSS fix
// could never reach an already-installed device.
const buildId = computeBuildId(fileURLToPath(webappRoot), shell);

// ALGORITHM_VERSION: what every exported trial record and manifest reports as
// the thing that produced its `params` (spec §3.2), and what §3.5 keys
// re-scoring off. This used to be BUILD_ID -- 12 hex chars of a wasm hash --
// which is not a version and, per this file's own header, is not even
// reproducible: the .wasm embeds the building machine's cargo registry path,
// its path separators, and a rustc version string, so no two machines produce
// identical bytes. Nobody but the machine that built it could resolve an
// exported manifest's algorithm_version back to a source revision. In files
// that ARE the archive of record, that is a traceability hole.
//
// `<crate version>+<git revision>.<wasm hash>` keeps it a single string while
// making the first two components resolvable by anyone with the repo, and
// leaving the third as the tiebreaker for two builds of the same revision.
// Deliberately hashed over the WASM ALONE, not the shell: a CSS change must
// not read as a change to the maths that scored a trial.
const wasmHash = createHash('sha256').update(readFileSync(wasmOut)).digest('hex').slice(0, 12);
const algorithmVersion = `${crateVersion()}+${gitRevision()}.${wasmHash}`;

writeFileSync(
  buildIdOut,
  `// GENERATED by scripts/build-wasm.mjs -- do not edit.\n` +
    `// BUILD_ID is the offline service worker's cache key: it changes whenever ANY\n` +
    `// file in SHELL changes, because sw.js only repopulates its cache when this\n` +
    `// file changes. SHELL is every file sw.js must cache to run without a network.\n` +
    `// ALGORITHM_VERSION identifies the source revision that produced a trial's\n` +
    `// params (spec 3.2) and tracks the wasm alone, NOT the shell.\n` +
    `export const BUILD_ID = '${buildId}';\n` +
    `export const ALGORITHM_VERSION = '${algorithmVersion}';\n` +
    `export const SHELL = [\n${shell.map((f) => `  '${f}',`).join('\n')}\n];\n`,
);
console.log(`build id ${buildId}`);
console.log(`algorithm version ${algorithmVersion}`);
console.log(`shell: ${shell.length} files`);
