// The trials recorded in the current session. Before this view the page only
// ever showed the most recent trial, so an operator could not confirm that
// trial 3 of 5 had actually been captured without exporting.

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
  return { id: trial.id, label: `Trial ${index + 1}`, meta: bits.join(' · ') };
}

export function createTrialsView({ el, loadTrials, showTrial }) {
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
      // list through the trial's own record.
      for (const [i, t] of trials.entries()) {
        const s = trialSummary(t, i);
        const row = document.createElement('button');
        row.className = 'tile trial-row';
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
        row.append(text);
        row.addEventListener('click', () => showTrial(t));
        list.append(row);
      }
    },
  };
}
