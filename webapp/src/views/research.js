// Lifecycle for the gated research recorder view.
//
// Mirrors createCaptureView: `isRecording` and `redraw` are injected (not
// imported) so this module is testable with plain objects, and the same
// resource-leak reasoning applies -- a recording holds a devicemotion
// listener, a flush interval and a wake lock via its capture handle, so
// navigating away mid-recording must be refused, not silently orphaned.

export function createResearchView({ el, isRecording, redraw }) {
  let active = false;
  return {
    onEnter() {
      active = true;
      // The canvas was in a display:none subtree until now, so it measured
      // 0x0; redraw once it has a real box or a returning operator sees blank.
      redraw();
    },
    onLeave() {
      if (isRecording()) {
        return 'A recording is in progress. Tap Stop before leaving this screen.';
      }
      active = false;
      return true;
    },
    handleResize() {
      if (active) redraw();
    },
  };
}
