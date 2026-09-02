// View transitions for the five-section shell.
//
// The transition RULE is a pure reducer (planTransition) so it can be tested
// without a DOM; createRouter is the thin stateful wrapper the page uses.
//
// Why onLeave can refuse. A live capture holds four resources that outlive
// any DOM node: a `devicemotion` listener, a `setInterval(flush)`, a screen
// wake lock (all capture.js), and a window `resize` handler that redraws the
// waveform (app.js). Navigating away without stopping them leaves a capture
// running headless -- still consuming sensor events and flushing batches with
// no UI attached, still holding the screen awake -- and points the resize
// handler at a canvas inside a `display: none` subtree, where
// getBoundingClientRect() returns zeros and the canvas is silently resized to
// 0x0. Refusing the transition is the only outcome that neither discards a
// trial in progress nor orphans one.

export const VIEWS = ['home', 'capture', 'trials', 'mas', 'session'];

// An unknown name lands on home rather than hiding every section and
// leaving a blank page under the banner.
export function resolveView(name) {
  return VIEWS.includes(name) ? name : 'home';
}

// `canLeave(current)` returns `true` to permit, or a human-readable string
// explaining the refusal. A string is used rather than `false` so the view
// that blocks owns the wording -- the router has no idea why a capture is
// busy.
export function planTransition(current, next, { canLeave }) {
  const target = resolveView(next);
  if (target === current) return { kind: 'noop', view: current };
  const verdict = canLeave(current);
  if (verdict !== true) return { kind: 'blocked', view: current, reason: verdict };
  return { kind: 'switch', from: current, to: target };
}

export function createRouter({ onShow }) {
  const hooks = new Map();
  let current = 'home';

  return {
    register(name, { onEnter, onLeave } = {}) {
      hooks.set(name, { onEnter, onLeave });
    },

    current() {
      return current;
    },

    navigate(next, params = {}) {
      const plan = planTransition(current, next, {
        canLeave: (from) => hooks.get(from)?.onLeave?.() ?? true,
      });
      if (plan.kind !== 'switch') return plan;
      // Order is load-bearing: the outgoing view's teardown completes before
      // the incoming view is shown, so the incoming onEnter can measure
      // layout (drawWaveform needs a laid-out canvas) and never races a
      // teardown still holding the same DOM.
      current = plan.to;
      onShow(plan.to);
      hooks.get(plan.to)?.onEnter?.(params);
      return plan;
    },
  };
}
