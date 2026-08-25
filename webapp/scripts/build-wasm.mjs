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
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const repoRoot = new URL('../../', import.meta.url);
const manifest = fileURLToPath(new URL('mobile-imu-core/Cargo.toml', repoRoot));
const lockfile = fileURLToPath(new URL('mobile-imu-core/Cargo.lock', repoRoot));
const wasmIn = fileURLToPath(
  new URL('mobile-imu-core/target/wasm32-unknown-unknown/release/mobile_imu_core.wasm', repoRoot),
);
const outDir = fileURLToPath(new URL('webapp/src/wasm', repoRoot));

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
