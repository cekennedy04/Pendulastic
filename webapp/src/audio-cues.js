// Audio cues for the capture loop. WebAudio oscillator only: no asset files,
// no dependency, consistent with the project-wide no-dependency constraint.
//
// ADDITIVE ONLY. Every cue here has a visual counterpart -- the progress bar,
// the state colour, the guide text. A muted phone, a phone in a pocket, or a
// clinician who is deaf must lose no information, so audio may never be the
// sole carrier of a state change. Nothing in this module is allowed to gate
// capture: every call is wrapped, and a failure is logged and ignored.
//
// `ctxFactory` is injected so the module imports safely under `node --test`
// and can be exercised with a fake.

export function createAudioCues({ ctxFactory = () => new (window.AudioContext || window.webkitAudioContext)() } = {}) {
  let ctx = null;
  let failed = false;

  // iOS refuses to start an AudioContext outside a user gesture, so this is
  // called from the Start click handler. A failure here is not an error the
  // operator needs to see -- it costs the beeps, not the trial.
  function unlock() {
    if (ctx || failed) return;
    try {
      ctx = ctxFactory();
      if (ctx.state === 'suspended' && typeof ctx.resume === 'function') ctx.resume();
    } catch (err) {
      failed = true;
      console.warn('audio unavailable; cues will be visual only', err);
    }
  }

  function tone({ freq, seconds, gain }) {
    if (!ctx || failed) return;
    try {
      const osc = ctx.createOscillator();
      const amp = ctx.createGain();
      osc.frequency.value = freq;
      osc.type = 'sine';
      // Ramped rather than switched: a square-edged gate on a sine produces an
      // audible click that reads as a fault sound in a clinical room.
      const now = ctx.currentTime;
      amp.gain.setValueAtTime(0, now);
      amp.gain.linearRampToValueAtTime(gain, now + 0.01);
      amp.gain.linearRampToValueAtTime(0, now + seconds);
      osc.connect(amp).connect(ctx.destination);
      osc.start(now);
      osc.stop(now + seconds + 0.02);
    } catch (err) {
      console.warn('audio cue failed', err);
    }
  }

  return {
    unlock,
    /// One short blip per completed second of stability.
    tick: () => tone({ freq: 880, seconds: 0.06, gain: 0.15 }),
    /// Longer and lower, so completion is distinguishable from a tick without
    /// counting -- the one cue the operator may act on while not looking.
    complete: () => tone({ freq: 440, seconds: 0.45, gain: 0.2 }),
    get available() { return Boolean(ctx) && !failed; },
  };
}
