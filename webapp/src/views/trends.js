// A participant's history over time. This is the app's first cross-session
// read: every other trial query in the app is scoped to one session.

// Median, not mean. One unscorable or artifact-laden trial must not drag a
// session's point -- see the spec's "why per-session and not per-trial".
//
// Non-finite values are skipped rather than poisoning the result: a parameter
// that could not be measured is stored as a placeholder, and an unscorable
// trial's composite comes back null. Averaging either in would quietly move a
// clinical series.
export function median(values) {
  const xs = (values || []).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

// One point per (session, leg). `scoreOf(trial) -> number | null` is injected
// so this module never reaches for the wasm, and the whole join stays testable
// with plain objects.
//
// A trial with no `side` is grouped as 'unset' rather than dropped or guessed.
// Trials recorded before the side selector existed carry null, and filing them
// under a leg they were never attributed to would invent a clinical fact --
// the same reason app.js used to export `side: null` rather than defaulting to
// 'left'.
export function sessionSeries(sessions, trialsBySession, { scoreOf }) {
  const out = [];
  const ordered = [...(sessions || [])].sort((a, b) => a.timestamp - b.timestamp);

  for (const session of ordered) {
    const trials = (trialsBySession || {})[session.id] || [];
    const byLeg = new Map();
    for (const t of trials) {
      const leg = t.side || 'unset';
      if (!byLeg.has(leg)) byLeg.set(leg, []);
      byLeg.get(leg).push(t);
    }
    for (const [leg, group] of byLeg) {
      out.push({
        sessionId: session.id,
        date: session.timestamp,
        leg,
        a0: median(group.map((t) => t.params && t.params.a0_deg)),
        pt7: median(group.map((t) => scoreOf(t))),
        n: group.length,
        thin: group.length < 2,
        anyUnmeasured: group.some((t) => (t.unmeasured || []).length > 0),
      });
    }
  }
  return out;
}
