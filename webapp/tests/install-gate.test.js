import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { installState } from '../src/install-gate.js';

const IOS = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537.36';
const mm = (matches) => () => ({ matches });

test('display-mode standalone is recognised on any platform', () => {
  assert.equal(installState({ matchMedia: mm(true), navigatorStandalone: undefined, userAgent: ANDROID }), 'standalone');
  assert.equal(installState({ matchMedia: mm(true), navigatorStandalone: undefined, userAgent: IOS }), 'standalone');
});

test('iOS reports standalone through its own non-standard flag', () => {
  // Older iOS does not match the display-mode query but does set this.
  assert.equal(installState({ matchMedia: mm(false), navigatorStandalone: true, userAgent: IOS }), 'standalone');
});

test('a browser tab needs installing', () => {
  assert.equal(installState({ matchMedia: mm(false), navigatorStandalone: false, userAgent: IOS }), 'needs-install');
});

test('Android in a tab is needs-install, NOT unsupported', () => {
  // navigator.standalone is undefined on Android. Treating undefined as "not
  // installed AND not iOS" must not lock Android users out permanently.
  assert.equal(installState({ matchMedia: mm(false), navigatorStandalone: undefined, userAgent: ANDROID }), 'needs-install');
});

test('a browser with no service worker support cannot go offline', () => {
  assert.equal(
    installState({ matchMedia: mm(false), navigatorStandalone: undefined, userAgent: ANDROID, hasServiceWorker: false }),
    'unsupported-browser',
  );
});

// The gate above is only half the mechanism. installState can ONLY ever
// return 'standalone' if the Home Screen launch actually is standalone, and
// on iOS that requires the page to declare it -- via a manifest with
// `display: "standalone"` (iOS 15.4+) or the legacy
// `apple-mobile-web-app-capable` meta tag. With neither, an installed clip
// opens with browser chrome, the gate never lifts, and the app cannot record
// at all. These two tests pin the declarations that make the gate passable,
// because nothing else in this suite would notice their removal.

const webappRoot = new URL('../', import.meta.url);
const readWebappFile = (rel) => readFileSync(fileURLToPath(new URL(rel, webappRoot)), 'utf8');

test('manifest.json declares standalone display, which is what lets the gate ever open', () => {
  const manifest = JSON.parse(readWebappFile('manifest.json'));
  assert.equal(
    manifest.display,
    'standalone',
    'without display:"standalone" an iOS Home Screen launch keeps browser chrome and installState never returns "standalone"',
  );
  assert.ok(manifest.name, 'a manifest with no name is not an installable web app');
  assert.equal(manifest.start_url, './', 'start_url must be relative so the app works wherever it is deployed');
});

test('index.html links the manifest and carries the legacy iOS standalone meta tag', () => {
  // Both, not either: the manifest covers iOS 15.4+ and every other platform,
  // the meta tag covers older iOS, which ignores the manifest's `display`.
  const html = readWebappFile('index.html');
  assert.match(html, /<link\s+rel="manifest"\s+href="\.\/manifest\.json">/);
  assert.match(html, /<meta\s+name="apple-mobile-web-app-capable"\s+content="yes">/);
});
