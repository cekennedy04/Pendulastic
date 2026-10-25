import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VIEWS, resolveView, planTransition, createRouter } from '../src/router.js';

test('the six views are the ones index.html defines', () => {
  assert.deepEqual(VIEWS, ['home', 'capture', 'trials', 'mas', 'session', 'trends']);
});

test('an unknown view falls back to home rather than a blank screen', () => {
  assert.equal(resolveView('home'), 'home');
  assert.equal(resolveView('nope'), 'home');
  assert.equal(resolveView(undefined), 'home');
});

const always = () => true;

test('navigating to the current view is a no-op', () => {
  assert.deepEqual(planTransition('home', 'home', { canLeave: always }),
    { kind: 'noop', view: 'home' });
});

test('a permitted transition switches', () => {
  assert.deepEqual(planTransition('home', 'capture', { canLeave: always }),
    { kind: 'switch', from: 'home', to: 'capture' });
});

// The whole reason this is a reducer and not a class method: a live capture
// holds a devicemotion listener, a flush interval and a wake lock, and
// leaving the view without stopping them orphans all three.
test('a view that refuses to leave blocks the transition and keeps the view', () => {
  const canLeave = () => 'Stop the trial first.';
  assert.deepEqual(planTransition('capture', 'home', { canLeave }),
    { kind: 'blocked', view: 'capture', reason: 'Stop the trial first.' });
});

test('a blocked transition is still blocked when the target is unknown', () => {
  const r = planTransition('capture', 'garbage', { canLeave: () => 'busy' });
  assert.equal(r.kind, 'blocked');
});

test('onLeave runs to completion before onEnter', () => {
  const order = [];
  const router = createRouter({ onShow: (n) => order.push(`show:${n}`) });
  router.register('home', { onLeave: () => { order.push('home:leave'); return true; } });
  router.register('capture', { onEnter: () => order.push('capture:enter') });
  router.navigate('capture');
  assert.deepEqual(order, ['home:leave', 'show:capture', 'capture:enter']);
});

test('a blocked navigate leaves current() unchanged and never shows the target', () => {
  const shown = [];
  const router = createRouter({ onShow: (n) => shown.push(n) });
  router.register('capture', { onLeave: () => 'recording' });
  router.register('home', {});
  router.navigate('capture');
  shown.length = 0;
  const result = router.navigate('home');
  assert.equal(result.kind, 'blocked');
  assert.equal(result.reason, 'recording');
  assert.equal(router.current(), 'capture');
  assert.deepEqual(shown, []);
});

test('a view with no hooks navigates freely', () => {
  const router = createRouter({ onShow: () => {} });
  router.register('trials', {});
  assert.equal(router.navigate('trials').kind, 'switch');
  assert.equal(router.current(), 'trials');
});

test('onEnter receives the params passed to navigate', () => {
  let got = null;
  const router = createRouter({ onShow: () => {} });
  router.register('trials', { onEnter: (p) => { got = p; } });
  router.navigate('trials', { trialId: 't-9' });
  assert.deepEqual(got, { trialId: 't-9' });
});

test('the router starts on home', () => {
  assert.equal(createRouter({ onShow: () => {} }).current(), 'home');
});
