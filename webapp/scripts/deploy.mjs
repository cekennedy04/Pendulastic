// Repeatable production deploy of the web app: `npm run deploy` (from webapp/).
//
// The steps, each of which has gone wrong by hand at least once:
//   1. Refuse a dirty tree (webapp/ and mobile-imu-core/ -- what gets built).
//   2. Refuse unless HEAD is exactly origin/main (after a fetch). The live app
//      must be a commit anyone can check out.
//   3. npm run build:dist, then npm test against that fresh build.
//   4. npx vercel deploy --prod --yes --project pendulastic-app dist
//      -- ALWAYS with --project: without it the CLI deploys to whatever
//      project the folder happens to be linked to, or prompts to create one.
//   5. Fetch the live src/build-id.js and check its BUILD_ID is the one just
//      built. A mismatch means the deploy did not land (or a cache is serving
//      an old build), and installed phones would not update.
//   6. Always restore the tracked src/build-id.js that build:dist rewrote, so
//      the checkout is left clean.
//
// `--dry-run` does 1-3 and 6, prints the deploy command, and skips 4-5 (no
// network, no Vercel login needed). tests/deploy-script.test.js drives
// `deploy()` with stubbed commands.

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const VERCEL_PROJECT = 'pendulastic-app';
export const LIVE_URL = 'https://pendulastic-app.vercel.app';
export const DEPLOY_ARGS = ['vercel', 'deploy', '--prod', '--yes', '--project', VERCEL_PROJECT, 'dist'];

const WEBAPP_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function parseBuildId(source) {
  const m = /export const BUILD_ID = '([0-9a-f]+)'/.exec(source ?? '');
  return m ? m[1] : null;
}

// Runs a command in webapp/, inheriting stdio unless `capture` is set.
// shell:true on Windows so `npm`/`npx` (.cmd shims) resolve.
function defaultRun(cmd, args, { capture = false } = {}) {
  const r = spawnSync(cmd, args, {
    cwd: WEBAPP_DIR,
    stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit',
    encoding: 'utf8',
    shell: process.platform === 'win32',
  });
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(' ')} exited with ${r.status}`);
  return capture ? r.stdout.trim() : '';
}

async function defaultFetchText(url) {
  const res = await fetch(url, { cache: 'no-store', headers: { 'Cache-Control': 'no-cache' } });
  if (!res.ok) throw new Error(`GET ${url} -> HTTP ${res.status}`);
  return res.text();
}

function defaultReadBuildId() {
  return parseBuildId(readFileSync(path.join(WEBAPP_DIR, 'src', 'build-id.js'), 'utf8'));
}

export async function deploy({
  dryRun = false,
  run = defaultRun,
  fetchText = defaultFetchText,
  readBuildId = defaultReadBuildId,
  log = console.log,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  verifyAttempts = 6,
} = {}) {
  const dirty = run('git', ['status', '--porcelain', '--', '.', '../mobile-imu-core'], { capture: true });
  if (dirty) {
    throw new Error(`refusing to deploy: uncommitted changes under webapp/ or mobile-imu-core/:\n${dirty}`);
  }
  run('git', ['fetch', 'origin', 'main']);
  const head = run('git', ['rev-parse', 'HEAD'], { capture: true });
  const main = run('git', ['rev-parse', 'origin/main'], { capture: true });
  if (head !== main) {
    throw new Error(`refusing to deploy: HEAD ${head.slice(0, 12)} is not origin/main ${main.slice(0, 12)}. `
      + 'Check out origin/main (or merge the PR first).');
  }

  try {
    run('npm', ['run', 'build:dist']);
    run('npm', ['test']);
    const built = readBuildId();
    if (!built) throw new Error('could not read BUILD_ID from src/build-id.js after the build');
    log(`built ${head.slice(0, 12)} -> BUILD_ID ${built}`);

    if (dryRun) {
      log(`[dry-run] would run: npx ${DEPLOY_ARGS.join(' ')}`);
      log(`[dry-run] would verify ${LIVE_URL}/src/build-id.js serves BUILD_ID ${built}`);
      return { deployed: false, buildId: built };
    }

    run('npx', DEPLOY_ARGS);

    // The alias can take a few seconds to point at the new deployment.
    let live = null;
    for (let i = 0; i < verifyAttempts; i++) {
      try {
        live = parseBuildId(await fetchText(`${LIVE_URL}/src/build-id.js?v=${Date.now()}`));
      } catch (err) {
        log(`verify attempt ${i + 1}: ${err.message}`);
      }
      if (live === built) break;
      if (i < verifyAttempts - 1) await sleep(5000);
    }
    if (live !== built) {
      throw new Error(`deploy did NOT verify: ${LIVE_URL} serves BUILD_ID ${live ?? '(unreadable)'}, `
        + `expected ${built}`);
    }
    log(`verified: ${LIVE_URL} serves BUILD_ID ${built}. The app footer should show it after reload.`);
    return { deployed: true, buildId: built };
  } finally {
    // build:dist rewrites this tracked file; never leave it modified.
    run('git', ['checkout', '--', 'src/build-id.js']);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  deploy({ dryRun: process.argv.includes('--dry-run') }).catch((err) => {
    console.error(`deploy: ${err.message}`);
    process.exit(1);
  });
}
