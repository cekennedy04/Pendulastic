import { test } from 'node:test';
import assert from 'node:assert/strict';
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
