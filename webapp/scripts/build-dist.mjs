// Assembles webapp/dist/, the directory that actually gets uploaded to a
// static host. Run via `npm run build:dist` (which runs build:wasm first --
// see package.json -- so the wasm pair this copies is always current).
//
// The file list is NOT hand-maintained here. It is derived from
// computeShell(), the same function sw.js's SHELL and tests/sw-shell.test.js
// already use to know what the offline cache needs. This project has been
// bitten twice by two lists (the shell and something else) drifting apart --
// see shell-list.mjs's own header -- so this deliberately does not become a
// third. The only addition on top of the shell is sw.js itself, which is
// never a shell ENTRY (a service worker cannot cache.addAll its own script --
// the browser fetches and installs it through a separate mechanism) but is
// still a file the page cannot run without.
//
// Deliberately excluded, because none of it is needed at runtime and some of
// it must never leave this machine: tests/, scripts/, captures/ (participant-
// adjacent capture data), dev_server.py, README.md, docs/. Nothing here
// copies a directory wholesale -- only the explicit shell + sw.js list -- so
// there is no exclusion list to keep in sync; anything not named by
// computeShell or EXTRA simply never gets copied.
//
// Why this is a local build-and-upload step rather than a host-side build:
// build:wasm needs a Rust toolchain (cargo + wasm-bindgen) that a static
// host's build environment does not provide. See webapp/README.md.

import { existsSync, mkdirSync, copyFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { computeShell } from './shell-list.mjs';

// Netlify/Cloudflare Pages _headers format. Two things this must guarantee:
//
// - sw.js and src/build-id.js carry the cache key (BUILD_ID/SHELL) and must
//   never be served stale themselves -- if the browser's HTTP cache serves an
//   old build-id.js, the service worker never notices a new build exists and
//   an installed phone is stuck forever. no-cache (not no-store) still lets
//   the browser send a conditional request, it just forces revalidation.
// - .wasm must be served as application/wasm. Safari's instantiateStreaming
//   rejects a application/octet-stream response with a content-type error
//   that names neither the file nor the reason, so this is cheap insurance
//   against a confusing on-device failure. The exact known filename is
//   listed explicitly, because that exact path is not glob-dependent. The
//   `/*.wasm` line below is only belt-and-braces: hosts read a trailing `*`
//   as a splat, so it covers .wasm at the site ROOT, not a rename under
//   src/wasm/. If the binary is ever renamed, update the exact path -- do
//   not rely on the glob to catch it.
//
// GitHub Pages ignores this file entirely -- see webapp/README.md.
const HEADERS = `/sw.js
  Cache-Control: no-cache

/src/build-id.js
  Cache-Control: no-cache

/src/wasm/mobile_imu_core_bg.wasm
  Content-Type: application/wasm

/*.wasm
  Content-Type: application/wasm
`;

const EXTRA = ['./sw.js'];

// rootDir: webapp/ (absolute). srcDir: webapp/src (absolute). distDir: where
// to write the deployable output (absolute). Split out from the CLI section
// below so tests/dist-build.test.js can call this directly against a real
// checkout without re-invoking the CLI plumbing.
export function buildDist(rootDir, srcDir, distDir) {
  if (existsSync(distDir)) {
    // maxRetries: Windows throws EPERM/EBUSY when an indexer or virus
    // scanner still holds a handle on a file in the tree. Without it this is
    // an intermittent hard failure, not a slow success -- and this now runs
    // on the `npm test` path, where CI asserts an exact test count.
    rmSync(distDir, { recursive: true, force: true, maxRetries: 3 });
  }
  mkdirSync(distDir, { recursive: true });

  const shell = computeShell(srcDir);
  // './' is index.html under another name (see shell-list.mjs) -- not a
  // distinct file to copy. De-duplicated via Set in case EXTRA ever overlaps
  // a shell entry.
  const files = [...new Set([...shell.filter((f) => f !== './'), ...EXTRA])];

  for (const rel of files) {
    const dest = path.join(distDir, rel);
    mkdirSync(path.dirname(dest), { recursive: true });
    copyFileSync(path.join(rootDir, rel), dest);
  }

  writeFileSync(path.join(distDir, '_headers'), HEADERS);

  return { shell, files };
}

// CLI entry point. Guarded so importing buildDist (as the test does) never
// triggers a real filesystem build as a side effect of loading the module.
const isMain = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  const repoRoot = new URL('../../', import.meta.url);
  const rootDir = fileURLToPath(new URL('webapp/', repoRoot));
  const srcDir = fileURLToPath(new URL('webapp/src', repoRoot));
  const distDir = fileURLToPath(new URL('webapp/dist', repoRoot));
  const { shell, files } = buildDist(rootDir, srcDir, distDir);
  console.log(`Built ${distDir}`);
  console.log(`${files.length} files copied (${shell.length - 1} shell entries + sw.js), plus _headers`);
}
