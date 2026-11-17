import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deploy, parseBuildId, DEPLOY_ARGS, VERCEL_PROJECT } from '../scripts/deploy.mjs';

// A scripted `run`: records every command and answers the git queries.
function fakeRun({ dirty = '', head = 'abc123', main = 'abc123', failOn = null } = {}) {
  const calls = [];
  const run = (cmd, args) => {
    const line = `${cmd} ${args.join(' ')}`;
    calls.push(line);
    if (failOn && line.includes(failOn)) throw new Error(`${line} failed`);
    if (line.startsWith('git status')) return dirty;
    if (line === 'git rev-parse HEAD') return head;
    if (line === 'git rev-parse origin/main') return main;
    return '';
  };
  return { run, calls };
}

const quiet = () => {};

test('dry run builds and tests, but never calls vercel or the network', async () => {
  const { run, calls } = fakeRun();
  let fetched = false;
  const out = await deploy({
    dryRun: true, run, log: quiet, readBuildId: () => 'deadbeef0001',
    fetchText: async () => { fetched = true; return ''; },
  });
  assert.deepEqual(out, { deployed: false, buildId: 'deadbeef0001' });
  assert.ok(calls.includes('npm run build:dist'));
  assert.ok(calls.includes('npm test'));
  assert.ok(!calls.some((c) => c.includes('vercel')), calls.join('\n'));
  assert.equal(fetched, false);
  assert.equal(calls.at(-1), 'git checkout -- src/build-id.js', 'build-id.js is always restored');
});

test('refuses a dirty tree before building anything', async () => {
  const { run, calls } = fakeRun({ dirty: ' M src/app.js' });
  await assert.rejects(() => deploy({ dryRun: true, run, log: quiet }), /uncommitted/);
  assert.ok(!calls.some((c) => c.startsWith('npm')));
});

test('refuses when HEAD is not origin/main', async () => {
  const { run, calls } = fakeRun({ head: 'feature1', main: 'main0001' });
  await assert.rejects(() => deploy({ dryRun: true, run, log: quiet }), /not origin\/main/);
  assert.ok(calls.includes('git fetch origin main'), 'compares against a freshly fetched main');
  assert.ok(!calls.some((c) => c.startsWith('npm')));
});

test('the vercel call always names the project', () => {
  const i = DEPLOY_ARGS.indexOf('--project');
  assert.ok(i > 0);
  assert.equal(DEPLOY_ARGS[i + 1], VERCEL_PROJECT);
  assert.equal(VERCEL_PROJECT, 'pendulastic-app');
  assert.ok(DEPLOY_ARGS.includes('--prod') && DEPLOY_ARGS.at(-1) === 'dist');
});

test('a real deploy verifies the live BUILD_ID', async () => {
  const { run, calls } = fakeRun();
  const out = await deploy({
    run, log: quiet, readBuildId: () => 'feedface0002', sleep: async () => {},
    fetchText: async () => "export const BUILD_ID = 'feedface0002';\n",
  });
  assert.deepEqual(out, { deployed: true, buildId: 'feedface0002' });
  assert.ok(calls.includes(`npx ${DEPLOY_ARGS.join(' ')}`));
});

test('a live BUILD_ID mismatch fails the deploy, and still restores build-id.js', async () => {
  const { run, calls } = fakeRun();
  await assert.rejects(() => deploy({
    run, log: quiet, readBuildId: () => 'feedface0002', sleep: async () => {}, verifyAttempts: 2,
    fetchText: async () => "export const BUILD_ID = 'a1d000000000';\n",
  }), /did NOT verify.*a1d000000000/);
  assert.equal(calls.at(-1), 'git checkout -- src/build-id.js');
});

test('a failing test run stops before vercel and restores build-id.js', async () => {
  const { run, calls } = fakeRun({ failOn: 'npm test' });
  await assert.rejects(() => deploy({ run, log: quiet, readBuildId: () => 'x' }), /npm test failed/);
  assert.ok(!calls.some((c) => c.includes('vercel')));
  assert.equal(calls.at(-1), 'git checkout -- src/build-id.js');
});

test('parseBuildId reads the generated file and nothing else', () => {
  assert.equal(parseBuildId("export const BUILD_ID = '17188b199160';"), '17188b199160');
  assert.equal(parseBuildId('<html>not found</html>'), null);
  assert.equal(parseBuildId(undefined), null);
});
