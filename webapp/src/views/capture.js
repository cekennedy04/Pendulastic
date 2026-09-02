// Lifecycle for the capture view.
//
// Four resources outlive any DOM node while a trial runs: capture.js's
// `devicemotion` listener, its `setInterval(flush)`, its screen wake lock,
// and app.js's window `resize` -> drawWaveform handler. The first three are
// owned by the capture handle and released by its stop(); this module owns
// the fourth and the refusal that keeps the first three from being abandoned.
//
// `isCapturing` and `redraw` are injected rather than imported so the whole
// module is testable with plain objects under `node --test`.

export function createCaptureView({ el, isCapturing, redraw }) {
  let active = false;

  return {
    onEnter() {
      active = true;
      // The canvas was inside a `display: none` subtree until this instant,
      // so any redraw attempted while away measured 0x0. Redraw now that it
      // has a real box, or a returning operator sees a blank plot.
      redraw();
    },

    // Returning a string rather than `false` lets this view own the wording;
    // the router only distinguishes `true` from everything else.
    onLeave() {
      if (isCapturing()) {
        return 'A trial is recording. Tap Stop before leaving this screen.';
      }
      active = false;
      const blocked = el('nav-blocked');
      if (blocked) blocked.hidden = true;
      return true;
    },

    // Called by app.js's single window `resize` listener. Gating on `active`
    // rather than adding and removing the listener keeps one registration
    // for the page's whole life -- there is no path that can leak a second.
    handleResize() {
      if (active) redraw();
    },
  };
}
