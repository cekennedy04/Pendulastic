// The trials recorded in the current session. Before this view the page only
// ever showed the most recent trial, so an operator could not confirm that
// trial 3 of 5 had actually been captured without exporting.
//
// Trials can be EXCLUDED here, never deleted. An excluded trial keeps its
// params, its raw log and its place in this list, and still exports; it only
// stops counting toward the session count and the export gate. On a phone the
// record is the only copy until it has been exported, and the project's
// standing rule is that code flags data quality and never drops it.
import { isExcluded, isQuickTest } from '../session-store.js';

// Pure. One row's display text.
export function trialSummary(trial, index) {
  const bits = [];
  bits.push(trial.side ? String(trial.side) : 'side not set');
  const n = trial.params && trial.params.n;
  if (typeof n === 'number') bits.push(`N ${n.toFixed(2)}`);
  const a0 = trial.params && trial.params.a0_deg;
  if (typeof a0 === 'number') bits.push(`A0 ${a0.toFixed(1)}°`);
  const unmeasured = (trial.unmeasured || []).length;
  if (unmeasured) bits.push(`${unmeasured} unmeasured`);
  if (isQuickTest(trial)) bits.push('quick test');
  return { id: trial.id, label: `Trial ${index + 1}`, meta: bits.join(' · ') };
}

// Pure. The second line an excluded trial carries, so the list says WHY as
// well as that it happened. Returns null when the trial is not excluded.
export function exclusionNote(trial) {
  if (!isExcluded(trial)) return null;
  const reason = String(trial.excluded_reason || '').trim();
  return reason ? `Excluded — ${reason}` : 'Excluded — no reason given';
}

export function createTrialsView({ el, loadTrials, showTrial, setExcluded }) {
  return {
    async onEnter() {
      const list = el('trial-list');
      list.textContent = '';
      const trials = await loadTrials();
      if (trials.length === 0) {
        const p = document.createElement('p');
        p.className = 'empty';
        p.textContent = 'No trials recorded in this session yet.';
        list.append(p);
        return;
      }
      // Rows are built with createElement rather than innerHTML:
      // clinic_patient_id is free text a clinician types, and it reaches this
      // list through the trial's own record. The exclusion reason is free text
      // too, and lands in the same list.
      for (const [i, t] of trials.entries()) {
        list.append(buildRow(t, i, { showTrial, setExcluded, rerender: () => this.onEnter() }));
      }
    },
  };
}

function buildRow(t, i, { showTrial, setExcluded, rerender }) {
  const s = trialSummary(t, i);
  const wrap = document.createElement('div');
  wrap.className = 'trial-entry';

  const row = document.createElement('button');
  row.className = 'tile trial-row';
  if (isExcluded(t)) row.classList.add('trial-excluded');
  row.dataset.trialId = s.id;

  const text = document.createElement('span');
  text.className = 'tile-text';
  const title = document.createElement('span');
  title.className = 'tile-title';
  title.textContent = s.label;
  const sub = document.createElement('span');
  sub.className = 'tile-sub';
  sub.textContent = s.meta;
  text.append(title, sub);

  const note = exclusionNote(t);
  if (note) {
    const n = document.createElement('span');
    n.className = 'tile-sub trial-exclusion-note';
    n.textContent = note;
    text.append(n);
  }
  row.append(text);
  row.addEventListener('click', () => showTrial(t));
  wrap.append(row);

  // No exclusion controls unless the caller wired persistence, so the view
  // cannot offer a button that silently does nothing.
  if (typeof setExcluded !== 'function') return wrap;

  const actions = document.createElement('div');
  actions.className = 'trial-actions';

  if (isExcluded(t)) {
    const restore = document.createElement('button');
    restore.className = 'link-btn';
    restore.textContent = 'Restore to count';
    restore.addEventListener('click', async () => {
      await setExcluded(t, false, '');
      await rerender();
    });
    actions.append(restore);
  } else {
    const exclude = document.createElement('button');
    exclude.className = 'link-btn';
    exclude.textContent = 'Exclude from count';
    actions.append(exclude);

    // The reason form is built on demand rather than rendered hidden on every
    // row: a session can hold a lot of trials, and this keeps the list cheap.
    exclude.addEventListener('click', () => {
      exclude.hidden = true;
      const form = document.createElement('div');
      form.className = 'trial-exclude-form';

      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'Why? (optional)';
      input.className = 'trial-exclude-reason';
      input.maxLength = 120;

      const save = document.createElement('button');
      save.className = 'link-btn';
      save.textContent = 'Exclude';
      save.addEventListener('click', async () => {
        await setExcluded(t, true, input.value);
        await rerender();
      });

      const cancel = document.createElement('button');
      cancel.className = 'link-btn';
      cancel.textContent = 'Cancel';
      cancel.addEventListener('click', () => {
        form.remove();
        exclude.hidden = false;
      });

      form.append(input, save, cancel);
      actions.append(form);
      input.focus();
    });
  }

  wrap.append(actions);
  return wrap;
}
