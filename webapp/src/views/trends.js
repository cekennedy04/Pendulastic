import { MAS_ORDER, isPending } from '../mas-store.js';
import { renderCharts } from '../trend-charts.js';

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

// Clinician assessments over time.
//
// `rank` is the grade's ORDINAL POSITION in MAS_ORDER, not a numeric reading
// of the label: '1+' sits between '1' and '2' and is neither 1.5 nor a
// number. Plotting the label as a number is exactly the mistake the desktop's
// MAS_ORDER exists to prevent.
//
// A pending row and an unrecognised grade both carry rank null so the chart
// leaves a GAP. Plotting either at zero would read as "no spasticity" -- the
// opposite of "not yet assessed", and a fabricated observation in the
// unrecognised case. Grade '0' is a real assessment and correctly ranks 0,
// which is why the lookup checks for -1 rather than relying on truthiness.
export function masSeries(masRecords) {
  return [...(masRecords || [])]
    .map((r) => {
      const i = MAS_ORDER.indexOf(r.mas_grade);
      const pending = isPending(r);
      return {
        date: Date.parse(`${r.assessed_date}T00:00:00Z`),
        assessed_date: r.assessed_date,
        leg: r.leg || 'unset',
        grade: r.mas_grade,
        rank: pending || i === -1 ? null : i,
        pending,
      };
    })
    .sort((a, b) => a.date - b.date);
}

// The view. Every dependency is injected, so this module stays import-safe
// under `node --test` and the pure functions above can be tested with plain
// objects.
export function createTrendsView({ el, context, loadHistory, importBundle, exportFigure }) {
  let ready = false;
  let latest = null;

  function initOnce() {
    if (ready) return;
    el('trend-import').addEventListener('change', async (e) => {
      const files = [...e.target.files];
      if (files.length === 0) return;
      el('trend-import-status').textContent = 'Importing…';
      el('trend-import-status').textContent = await importBundle(files);
      // Cleared so re-selecting the same file fires `change` again; without
      // this a retry after a failed import looks like nothing happened.
      e.target.value = '';
      await render();
    });
    for (const btn of document.querySelectorAll('.chart-export')) {
      btn.addEventListener('click', () => exportFigure(btn.dataset.figure, latest));
    }
    ready = true;
  }

  async function render() {
    const { participantLabel } = context();
    const history = await loadHistory();
    latest = history;
    const empty = el('trend-empty');

    // Three distinct empty states, because they have three distinct remedies:
    // choose a participant, record something, or import history from another
    // device. One generic "no data" message would leave the operator guessing
    // which of the three applies.
    if (!history) {
      empty.textContent = 'No participant selected. Choose one in Session first.';
      empty.hidden = false;
    } else if (history.points.length === 0 && history.mas.length === 0) {
      empty.textContent = `Nothing recorded for ${participantLabel} on this device yet. Record a trial, enter a MAS assessment, or import a session bundle below.`;
      empty.hidden = false;
    } else {
      empty.hidden = true;
    }

    // Always states its provenance. A history assembled from one device is
    // partial by construction, and a partial clinical series that does not
    // say so is worse than an empty one.
    el('trend-source').textContent = history
      ? `${history.deviceSessions} session(s) on this device · ${history.importedSessions} imported`
      : '';

    if (history) renderCharts(el, history);
  }

  return {
    async onEnter() {
      initOnce();
      await render();
    },
  };
}
