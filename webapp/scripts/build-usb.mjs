// Assembles webapp/dist-usb/: a copy of the app that runs with NO server and
// NO network, opened straight from a USB drive as a file:// page. Run via
// `npm run build:usb` (which runs build:wasm first, like build:dist).
//
// Why a separate build rather than copying dist/: dist/ cannot run from
// file://, for three independent reasons, each of which alone is fatal:
//   - <script type="module" src> is fetched in CORS mode, and file:// URLs
//     are never same-origin, so Chrome refuses to load app.js at all
//     (MDN, "JavaScript modules", Modules on the web).
//   - wasm-bindgen's init() fetch()es mobile_imu_core_bg.wasm, and fetch()
//     of a file:// URL fails.
//   - capture.js starts a module Worker from ./worker.js -- the same
//     file:// fetch restriction.
// The service worker is irrelevant here: register() only accepts http(s).
//
// So this writes ONE self-contained HTML file: app.css inlined, every ES
// module of the app (and of the worker) folded into a tiny CommonJS-style
// registry, the wasm inlined as base64, and the capture worker started from
// a Blob URL (with an in-thread fallback if a browser refuses even that).
//
// What it deliberately does NOT do: change any source file, any maths, or
// the online build. The modules are the same files build:dist ships, with
// only their import/export syntax rewritten; BUILD_ID and ALGORITHM_VERSION
// come from the same generated src/build-id.js, so a trial scored on the USB
// copy carries the same algorithm stamp as one scored online by the same
// build. tests/usb-build.test.js drives the bundled worker against the
// unbundled one on the swing fixture and requires identical output.
//
// The bundler is ~150 lines of regex rather than a dependency (esbuild etc.)
// because webapp/ has no npm dependencies at all and CI runs `npm test`
// without an install step. It only understands the import/export forms this
// codebase uses, and THROWS on anything else rather than emitting a broken
// bundle -- see transformModule.

import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const MAIN_ENTRY = 'src/app.js';
const WORKER_ENTRY = 'src/worker.js';
const WASM_GLUE = 'src/wasm/mobile_imu_core.js';
const WASM_BIN = 'src/wasm/mobile_imu_core_bg.wasm';
export const USB_HTML = 'Pendulastic-offline.html';
export const USB_README = 'README-OFFLINE.txt';

// Per-module free variables shadowed inside that module's wrapper only.
//  - capture.js: `Worker` becomes the USB worker factory (Blob URL worker,
//    or in-thread fallback). capture.js itself is untouched.
//  - worker.js on the MAIN thread: `self` is hidden, so its "am I a Web
//    Worker?" check (typeof self.postMessage === 'function', which is also
//    true of window) does not bind window.onmessage. Inside the real worker
//    bundle no shadow is applied, so the entry point binds as it always has.
const MAIN_SHADOWS = {
  'src/capture.js': { Worker: '__usbWorker' },
  'src/worker.js': { self: 'undefined' },
};

function resolveSpec(fromId, spec) {
  if (!spec.startsWith('./') && !spec.startsWith('../')) {
    throw new Error(`build-usb: ${fromId} imports bare specifier '${spec}' -- only relative imports are supported`);
  }
  return path.posix.normalize(path.posix.join(path.posix.dirname(fromId), spec));
}

function parseNamed(list, fromId) {
  // `{ a, b as c }` -> [['a','a'], ['b','c']]  (imported, local)
  return list.split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
    const m = s.match(/^([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?$/);
    if (!m) throw new Error(`build-usb: cannot parse binding '${s}' in ${fromId}`);
    return [m[1], m[2] ?? m[1]];
  });
}

// Rewrites one ES module into the body of a registry function
// `(exports, __req, __dyn, __meta_url) => { ... }`. Exports are installed as
// getters at the TOP of the body, so they are live bindings exactly as in
// ESM (function declarations are hoisted; a const read before its line still
// throws, as it would natively).
export function transformModule(code, id) {
  const deps = new Set();
  const exported = []; // [exportedName, localName]
  let out = code;

  // Static imports. Anchored at column 0 so prose in comments never matches.
  out = out.replace(/^import\s+([\s\S]*?)\s+from\s+['"]([^'"]+)['"];?[ \t]*$/gm, (_m, clause, spec) => {
    const dep = resolveSpec(id, spec);
    deps.add(dep);
    const req = `__req(${JSON.stringify(dep)})`;
    const c = clause.trim();
    let m;
    if ((m = c.match(/^\*\s+as\s+([A-Za-z_$][\w$]*)$/))) return `const ${m[1]} = ${req};`;
    if ((m = c.match(/^([A-Za-z_$][\w$]*)\s*,\s*\{([\s\S]*)\}$/))) {
      const named = parseNamed(m[2], id).map(([a, b]) => (a === b ? a : `${a}: ${b}`));
      return `const { default: ${m[1]}, ${named.join(', ')} } = ${req};`;
    }
    if ((m = c.match(/^\{([\s\S]*)\}$/))) {
      const named = parseNamed(m[1], id).map(([a, b]) => (a === b ? a : `${a}: ${b}`));
      return `const { ${named.join(', ')} } = ${req};`;
    }
    if ((m = c.match(/^([A-Za-z_$][\w$]*)$/))) return `const ${m[1]} = ${req}.default;`;
    throw new Error(`build-usb: unsupported import clause '${c}' in ${id}`);
  });
  out = out.replace(/^import\s+['"]([^'"]+)['"];?[ \t]*$/gm, (_m, spec) => {
    const dep = resolveSpec(id, spec);
    deps.add(dep);
    return `__req(${JSON.stringify(dep)});`;
  });

  // Dynamic import of a literal relative path (app.js's lazy wasm load).
  out = out.replace(/\bimport\(\s*['"]([^'"]+)['"]\s*\)/g, (_m, spec) => {
    const dep = resolveSpec(id, spec);
    deps.add(dep);
    return `__dyn(${JSON.stringify(dep)})`;
  });

  out = out.replace(/\bimport\.meta\.url\b/g, '__meta_url');

  // export <declaration>
  out = out.replace(/^export\s+((?:async\s+)?function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)/gm, (_m, kind, name) => {
    exported.push([name, name]);
    return `${kind} ${name}`;
  });
  // export { a, b as c };
  out = out.replace(/^export\s*\{([^}]*)\}\s*;?[ \t]*$/gm, (_m, list) => {
    for (const [local, name] of parseNamed(list, id)) exported.push([name, local]);
    return '';
  });

  // Anything still shaped like module syntax is a form this bundler does not
  // understand. Fail the build rather than ship a page that throws on load.
  const leftover = out.match(/^(import|export)\b.*$/m);
  if (leftover) throw new Error(`build-usb: unsupported module syntax in ${id}: ${leftover[0]}`);

  return { code: out, deps: [...deps], exported };
}

// The glue's default export is wasm-bindgen's init(). Wrapped so that a call
// with no explicit source -- which is how both worker.js and app.js call it --
// instantiates the inlined bytes instead of fetching a file:// URL. A caller
// that DOES pass a source (none does in the browser) is honoured unchanged.
const GLUE_DEFAULT_OVERRIDE = `
function __usbInit(arg) {
  const src = arg && Object.getPrototypeOf(arg) === Object.prototype ? arg.module_or_path : arg;
  return __wbg_init({ module_or_path: src === undefined ? __usbWasmBytes() : src });
}`;

function collectGraph(rootDir, entries, shadows) {
  const mods = new Map();
  const stack = [...entries];
  while (stack.length) {
    const id = stack.pop();
    if (mods.has(id)) continue;
    const file = path.join(rootDir, ...id.split('/'));
    if (!existsSync(file)) {
      throw new Error(`build-usb: missing ${file}${id.startsWith('src/wasm/') ? ' -- run `npm run build:wasm` first' : ''}`);
    }
    const t = transformModule(readFileSync(file, 'utf8'), id);
    let body = t.code;
    let exported = t.exported;
    if (id === WASM_GLUE) {
      body += GLUE_DEFAULT_OVERRIDE;
      exported = exported.map(([name, local]) => (name === 'default' ? ['default', '__usbInit'] : [name, local]));
      if (!exported.some(([n]) => n === 'default')) throw new Error('build-usb: wasm glue has no default export (init)');
    }
    const getters = exported.map(([name, local]) => `${JSON.stringify(name)}: () => ${local}`).join(', ');
    const shadow = shadows[id] ?? {};
    const shadowDecl = Object.entries(shadow).map(([k, v]) => `const ${k} = ${v};`).join(' ');
    const metaUrl = JSON.stringify(`pendulastic-usb:/${id}`);
    mods.set(id, `${JSON.stringify(id)}: function (exports, __req, __dyn) { const __meta_url = ${metaUrl}; ${shadowDecl}\n__exp(exports, { ${getters} });\n${body}\n}`);
    stack.push(...t.deps);
  }
  return mods;
}

// Shared module registry. Same evaluation order as ESM for an acyclic graph
// (depth-first, each module once); the registry entry is created before the
// body runs so a cycle would see a partially-initialised namespace, as ESM's
// would see uninitialised bindings.
const RUNTIME = `
function __exp(ns, getters) {
  for (const k of Object.keys(getters)) Object.defineProperty(ns, k, { enumerable: true, get: getters[k] });
}
const __cache = Object.create(null);
function __req(id) {
  if (__cache[id]) return __cache[id];
  const def = __defs[id];
  if (!def) throw new Error('pendulastic-usb: module not bundled: ' + id);
  const ns = (__cache[id] = {});
  def(ns, __req, __dyn);
  return ns;
}
function __dyn(id) { return Promise.resolve().then(() => __req(id)); }
let __usbWasm = null;
function __usbWasmBytes() {
  if (!__usbWasm) {
    const s = atob(__USB_WASM_B64);
    const b = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
    __usbWasm = b;
  }
  return __usbWasm;
}
`;

function bundle(mods, entry) {
  return `${RUNTIME}\nconst __defs = {\n${[...mods.values()].join(',\n')}\n};\n__req(${JSON.stringify(entry)});\n`;
}

// Starts the capture worker. Tries a real Web Worker from a Blob URL first
// (keeps wasm work off the thread that receives devicemotion, as online). If
// the browser refuses one -- synchronously, or with an error event before the
// worker ever replies -- it falls back to running worker.js's own message
// handler on the main thread. Messages sent before that decision are copied
// (batch buffers are transferred, i.e. detached, on send) and replayed.
const WORKER_FACTORY = `
function __usbWorker(url) {
  const listeners = { onmessage: null };
  const pending = [];
  let mode = 'starting';
  let real = null;
  let handle = null;
  const deliver = (data) => { if (listeners.onmessage) listeners.onmessage({ data }); };
  function fallback(why) {
    if (mode === 'inthread') return;
    console.warn('pendulastic-usb: running capture worker in-thread:', why);
    mode = 'inthread';
    try { real && real.terminate(); } catch (e) { /* already gone */ }
    handle = __req('src/worker.js').createWorkerHandler();
    const queued = pending.splice(0);
    let chain = Promise.resolve();
    for (const m of queued) chain = chain.then(() => handle(m, (r) => setTimeout(() => deliver(r), 0)));
  }
  function copyOf(m) { return m && m.buf instanceof ArrayBuffer ? Object.assign({}, m, { buf: m.buf.slice(0) }) : m; }
  try {
    const src = 'const __USB_WASM_B64 = ' + JSON.stringify(__USB_WASM_B64) + ';\\n' + __USB_WORKER_SRC;
    real = new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
    real.onmessage = (e) => { if (mode !== 'inthread') { mode = 'worker'; pending.length = 0; deliver(e.data); } };
    real.onerror = (e) => { if (mode === 'starting') { e.preventDefault && e.preventDefault(); fallback(e.message || 'worker error'); } };
  } catch (err) {
    fallback(err && err.message);
  }
  return {
    set onmessage(fn) { listeners.onmessage = fn; },
    get onmessage() { return listeners.onmessage; },
    postMessage(m, transfer) {
      if (mode === 'inthread') { handle(m, (r) => setTimeout(() => deliver(r), 0)); return; }
      if (mode === 'starting') pending.push(copyOf(m));
      real.postMessage(m, transfer);
    },
    terminate() { try { real && real.terminate(); } catch (e) { /* ignore */ } },
  };
}
`;

// Inspection hook for verifying the built file in a browser (load it, then
// drive the bundled worker or wasm from the console). USB build only.
const INSPECT_HOOK = `globalThis.__pendulasticUsb = { worker: __usbWorker, require: __req };`;

// Replaced in the visible install banner: the online text tells the user to
// install from Safari, which is meaningless for a file opened from a drive.
const USB_NOTICE = `<strong>Offline USB copy.</strong> Trials are kept only in this
    browser on this device &mdash; export after every session. Same scoring
    build as the online app (see the build line in Session).`;

function escapeForScript(js, what) {
  if (/<\/script/i.test(js)) throw new Error(`build-usb: ${what} contains a literal </script>`);
  return js;
}

export function buildUsbHtml(rootDir) {
  const readRel = (rel) => readFileSync(path.join(rootDir, ...rel.split('/')), 'utf8');
  const wasmPath = path.join(rootDir, ...WASM_BIN.split('/'));
  if (!existsSync(wasmPath)) throw new Error(`build-usb: missing ${wasmPath} -- run \`npm run build:wasm\` first`);
  const wasmB64 = readFileSync(wasmPath).toString('base64');

  const workerMods = collectGraph(rootDir, [WORKER_ENTRY], {});
  const workerSrc = bundle(workerMods, WORKER_ENTRY);
  // Main graph also carries worker.js (for the in-thread fallback) and the
  // glue (app.js imports it lazily for the trends view).
  const mainMods = collectGraph(rootDir, [MAIN_ENTRY, WORKER_ENTRY, WASM_GLUE], MAIN_SHADOWS);
  const mainJs = [
    `const __USB_WASM_B64 = ${JSON.stringify(wasmB64)};`,
    // </ is escaped inside the string literal so the HTML parser can never
    // see a closing tag in the worker's source; JS reads it back unchanged.
    `const __USB_WORKER_SRC = ${JSON.stringify(workerSrc).replace(/<\//g, '<\\/')};`,
    WORKER_FACTORY,
    INSPECT_HOOK,
    bundle(mainMods, MAIN_ENTRY),
  ].join('\n');

  const { BUILD_ID, ALGORITHM_VERSION } = parseBuildId(readRel('src/build-id.js'));

  let html = readRel('index.html');
  const swap = (re, to, what) => {
    if (!re.test(html)) throw new Error(`build-usb: index.html no longer contains ${what}`);
    html = html.replace(re, () => to);
  };
  swap(/<title>([^<]*)<\/title>/, '<title>Pendulastic (offline)</title>', '<title>');
  swap(/<link rel="manifest"[^>]*>\n?/, '', 'the manifest link');
  swap(/<link rel="stylesheet" href="\.\/src\/app\.css">/, `<style>\n${escapeForScript(readRel('src/app.css'), 'app.css')}\n</style>`, 'the app.css link');
  swap(/<script type="module" src="\.\/src\/app\.js"><\/script>\s*<script>[\s\S]*?serviceWorker[\s\S]*?<\/script>/,
    `<script>\n/* Pendulastic offline USB build. BUILD_ID ${BUILD_ID}, ALGORITHM_VERSION ${ALGORITHM_VERSION}. Generated by webapp/scripts/build-usb.mjs -- do not edit. */\n(function () {\n${escapeForScript(mainJs, 'the bundle')}\n})();\n</script>`,
    'the app.js module script followed by the service-worker registration');
  // The gate's text and <details> install instructions do not apply off a
  // drive; app.js still un-hides the gate (not standalone), so it shows this.
  const gate = /(<div id="install-gate"[^>]*>)\s*<strong>Not installed\.<\/strong>[\s\S]*?<\/details>/;
  if (!gate.test(html)) throw new Error('build-usb: index.html no longer contains the install-gate text');
  // #install-how stays (hidden): app.js writes into it unconditionally.
  html = html.replace(gate, (_m, open) => `${open}\n    ${USB_NOTICE}\n    <p id="install-how" hidden></p>`);
  return { html, BUILD_ID, ALGORITHM_VERSION, workerSrc, wasmBytes: wasmB64.length * 3 / 4 };
}

export function parseBuildId(text) {
  const id = text.match(/export const BUILD_ID = '([^']+)'/);
  const alg = text.match(/export const ALGORITHM_VERSION = '([^']+)'/);
  if (!id || !alg) throw new Error('build-usb: cannot read BUILD_ID / ALGORITHM_VERSION from src/build-id.js');
  return { BUILD_ID: id[1], ALGORITHM_VERSION: alg[1] };
}

export function usbReadme({ BUILD_ID, ALGORITHM_VERSION }) {
  return `Pendulastic - offline copy for a USB drive
===========================================

Build ${BUILD_ID}, algorithm ${ALGORITHM_VERSION}
(the same identifiers the online app shows under Session > build).
Research capture only - not validated.

WHAT IS ON THIS DRIVE
  ${USB_HTML}   the whole app in one file: no internet, no server needed.

WHO CAN USE IT
  Android phone, Chrome:  copy ${USB_HTML} to the phone (Downloads), then
      open it in Chrome. Capture needs the phone's motion sensors; this has
      NOT yet been confirmed on a real phone opened from a file - do one
      practice trial before relying on it.
  Laptop / desktop, Chrome or Edge:  double-click the file. Works for
      reviewing, trends and exporting; a laptop has no motion sensor, so it
      cannot record a trial.
  iPhone / iPad:  NOT SUPPORTED from a file. The Files app shows web pages
      without running them. Instead, while you still have internet, open
      https://pendulastic-app.vercel.app in Safari, tap Share > Add to Home
      Screen, and open it once from the icon. It then keeps working offline.
      (The same install-once route also works on Android and is the most
      reliable option on every phone.)

KEEP YOUR DATA
  Trials are stored inside the browser on the device that recorded them -
  not on this drive. Export after every session. Clearing the browser's data
  erases them. Moving or renaming the file can also give it a fresh, empty
  store in some browsers.

Regenerate with: cd webapp && npm run build:usb
`;
}

export function buildUsb(rootDir, outDir) {
  const built = buildUsbHtml(rootDir);
  if (existsSync(outDir)) rmSync(outDir, { recursive: true, force: true, maxRetries: 3 });
  mkdirSync(outDir, { recursive: true });
  writeFileSync(path.join(outDir, USB_HTML), built.html);
  writeFileSync(path.join(outDir, USB_README), usbReadme(built).replace(/\n/g, '\r\n'));
  return built;
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  const rootDir = fileURLToPath(new URL('../', import.meta.url));
  const outDir = path.join(rootDir, 'dist-usb');
  const b = buildUsb(rootDir, outDir);
  console.log(`Built ${outDir}`);
  console.log(`${USB_HTML}: ${(b.html.length / 1024).toFixed(0)} KB (wasm ${(b.wasmBytes / 1024).toFixed(0)} KB inlined)`);
  console.log(`build id ${b.BUILD_ID}, algorithm version ${b.ALGORITHM_VERSION}`);
}
