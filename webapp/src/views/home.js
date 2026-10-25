// The landing screen. Mirrors pendulastic_app.py's ModeSelectView: one
// filled-accent hero tile for the routine path, secondary tiles for the rest.
//
// No DOM work at module scope -- app.js calls this from inside its
// `typeof document !== 'undefined'` guard, so importing the module under
// `node --test` stays safe.

export function createHomeView({ el }) {
  return {
    // Both subtitles are live state, so they are refreshed on every entry
    // rather than written once at startup.
    onEnter({ participantLabel, side, trialCount } = {}) {
      const p = el('home-participant');
      if (p) {
        p.textContent = participantLabel
          ? `${participantLabel}${side ? ` · ${side} leg` : ''}`
          : 'no participant set';
      }
      const c = el('home-trial-count');
      if (c) {
        c.textContent = trialCount ? `${trialCount} this session` : 'none yet';
      }
    },
  };
}
