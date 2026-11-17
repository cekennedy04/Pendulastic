// The offline USB build (scripts/build-usb.mjs) rewrites every module's
// import/export syntax and swaps the wasm fetch for inlined bytes. These tests
// pin the two things that must survive that: the page is genuinely
// self-contained (nothing a file:// page would have to fetch), and the bundled
// capture worker scores a trial EXACTLY as the unbundled worker.js does.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildUsbHtml, transformModule } from '../scripts/build-usb.mjs';

const webappRoot = fileURLToPath(new URL('../', import.meta.url));
const wasmDir = new URL('../src/wasm/', import.meta.url);
if (!existsSync(new URL('mobile_imu_core_bg.wasm', wasmDir))) {
  throw new Error('webapp/src/wasm/ is missing -- run `npm run build:wasm` first (see tests/worker.test.js).');
}
const { createWorkerHandler } = await import('../src/worker.js');
const wasmSource = readFileSync(new URL('mobile_imu_core_bg.wasm', wasmDir));
const fixture = JSON.parse(readFileSync(new URL('./fixtures/swing.json', import.meta.url)));
const buildIdText = readFileSync(new URL('../src/build-id.js', import.meta.url), 'utf8');

const built = buildUsbHtml(webappRoot);

test('the USB page references no external file (a file:// page cannot fetch modules, wasm or a worker)', () => {
  const refs = [...built.html.matchAll(/\b(?:src|href)\s*=\s*"([^"]*)"/g)].map((m) => m[1])
    .filter((u) => !u.startsWith('#'));
  assert.deepEqual(refs, []);
  assert.doesNotMatch(built.html, /type="module"/);
  assert.doesNotMatch(built.html, /rel="manifest"/);
  assert.doesNotMatch(built.html, /serviceWorker\.register/);
});

test('the USB page carries the same BUILD_ID and ALGORITHM_VERSION as src/build-id.js', () => {
  const id = buildIdText.match(/BUILD_ID = '([^']+)'/)[1];
  const alg = buildIdText.match(/ALGORITHM_VERSION = '([^']+)'/)[1];
  assert.equal(built.BUILD_ID, id);
  assert.equal(built.ALGORITHM_VERSION, alg);
  assert.ok(built.html.includes(`'${alg}'`), 'build-id.js module (with ALGORITHM_VERSION) is bundled');
});

test('every inlined module runs strict, as an ES module does online', () => {
  const wrappers = (s) => s.split('function (exports, __req, __dyn) {').slice(1);
  for (const src of [built.html, built.workerSrc]) {
    const w = wrappers(src);
    assert.ok(w.length > 1);
    for (const body of w) assert.match(body, /^ 'use strict';/);
  }
});

test('"Send to laptop" (a POST to the dev server) is pinned hidden in the USB copy', () => {
  assert.match(built.html, /<button id="send-to-laptop" hidden disabled aria-hidden="true">/);
  assert.match(built.html, /#send-to-laptop \{ display: none !important; \}/);
});

test('transformModule throws on module syntax it does not understand rather than emitting a broken bundle', () => {
  assert.throws(() => transformModule("export default function () {}\n", 'src/x.js'), /unsupported module syntax/);
  assert.throws(() => transformModule("import x from 'pkg';\n", 'src/x.js'), /bare specifier/);
});

// Runs `src` as a Web Worker would: a `self` with postMessage, onmessage set
// by the entry point. Returns a driver that awaits each message's handling.
function bootWorkerBundle(src) {
  const posted = [];
  const self = { postMessage: (m) => posted.push(m), onmessage: null };
  const b64 = wasmSource.toString('base64');
  // eslint-disable-next-line no-new-func
  new Function('self', `const __USB_WASM_B64 = ${JSON.stringify(b64)};\n${src}`)(self);
  assert.equal(typeof self.onmessage, 'function', 'bundled worker.js bound self.onmessage');
  return { send: async (m) => { await self.onmessage({ data: m }); }, posted };
}

async function drive(send) {
  await send({ type: 'start', cfg: { beta: 0.041, emaAlpha: 0.3 } });
  for (const batch of fixture.batches) await send({ type: 'batch', buf: Float64Array.from(batch).buffer });
  await send({ type: 'finish' });
  await send({ type: 'reference' });
}

test('the bundled worker scores the swing fixture identically to src/worker.js', async () => {
  const usb = bootWorkerBundle(built.workerSrc);
  await drive(usb.send);

  const ref = [];
  const handle = createWorkerHandler();
  // Same config capture.js posts; the bytes stand in for the browser's fetch.
  await drive(async (m) => handle(m.type === 'start' ? { ...m, cfg: { ...m.cfg, wasmSource } } : m, (r) => ref.push(r)));

  const result = usb.posted.find((m) => m.type === 'result');
  assert.ok(result, `bundled worker produced a result (got ${JSON.stringify(usb.posted.at(-1))})`);
  assert.deepEqual(usb.posted, ref);
  assert.ok(usb.posted.find((m) => m.type === 'reference').payload, 'healthy reference available');
});
