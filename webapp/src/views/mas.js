// The MAS entry form: all 11 of the desktop MasEntryPanel's fields, because
// a column missing here means re-examining a patient, not re-deriving a
// number.

import {
  MAS_ORDER, PENDING_MAS_GRADE, STRONGER_LEG_OPTIONS, LEG_OPTIONS, MAS_FIELDS,
  validateMasForm, makeMasRecord, isPending,
} from '../mas-store.js';

// Drafts live in IndexedDB `settings`, NOT sessionStorage: sessionStorage is
// cleared when the standalone app is terminated, which is precisely the
// eviction-then-relaunch case a draft exists to survive. A half-filled MAS
// form is a clinical observation -- losing it means re-examining the patient.
export function draftKey(patientId, leg) {
  return `mas-draft:${patientId ?? ''}:${leg ?? ''}`;
}

// Every key this participant could hold a draft under. The form's Leg field
// is editable and independent of the session's side, so a draft may have been
// written under a leg the session does not currently have selected -- see
// Ruling J. Bounded: two legs plus the unset-leg key.
export function draftCandidateKeys(patientId) {
  return [...LEG_OPTIONS, ''].map((leg) => draftKey(patientId, leg));
}

// Pure. Given whatever was stored under those keys, pick the form values to
// resume: the most recently saved non-empty draft. A cleared draft is stored
// as `null` and must never win.
export function pickNewestDraft(drafts) {
  const live = (drafts || []).filter((d) => d && d.values);
  if (live.length === 0) return null;
  live.sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
  return live[0].values;
}

const GRADE_OPTIONS = [
  { value: '', label: '--' },
  ...MAS_ORDER.map((g) => ({ value: g, label: g })),
  // Explicit, and never the default. append_mas_score() rejects an empty
  // mas_grade outright, so requiring a deliberate choice here is what keeps
  // '-1' from being what an untouched picker yields.
  { value: PENDING_MAS_GRADE, label: 'not yet assessed' },
];

// Optional grades take the inverse rule: blank IS "not assessed", and the
// pending sentinel is invalid -- so it is not offered.
const OPTIONAL_GRADE_OPTIONS = [
  { value: '', label: '-- not assessed' },
  ...MAS_ORDER.map((g) => ({ value: g, label: g })),
];

function fill(select, options) {
  select.textContent = '';
  for (const o of options) {
    const opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.label;
    select.append(opt);
  }
}

function readForm(form) {
  const out = {};
  for (const f of MAS_FIELDS) out[f] = form.elements[f] ? form.elements[f].value : '';
  return out;
}

function writeForm(form, values) {
  for (const f of MAS_FIELDS) {
    if (form.elements[f]) form.elements[f].value = values[f] ?? '';
  }
}

// Why a MAS record may not be saved yet, or null when it may. Pure so the
// rule is unit-tested rather than only observed in a browser. A record with
// no patient_id is an UNANCHORED row -- the spec's migration section requires
// that no mas or trials row ever exist without a foreign-key anchor in
// `patients`, and db.js's backfill exists to guarantee exactly that.
export function masGuardReason({ patientId } = {}) {
  if (!patientId) return 'Set a participant in Session before saving an assessment.';
  return null;
}

export function createMasView({ el, saveRecord, loadRecords, loadDraft, saveDraft, clearDraft, context }) {
  const form = el('mas-form');
  let ready = false;

  function initOnce() {
    if (ready) return;
    fill(form.elements.mas_grade, GRADE_OPTIONS);
    fill(form.elements.mas_flexion, OPTIONAL_GRADE_OPTIONS);
    fill(form.elements.mas_extension, OPTIONAL_GRADE_OPTIONS);
    fill(form.elements.stronger_leg, STRONGER_LEG_OPTIONS.map(
      (v) => ({ value: v, label: v === '' ? '-- not assessed' : v })));

    // Debounced so a fast typist does not queue one IndexedDB write per
    // keystroke; 400ms is short enough that a termination loses at most a
    // few characters.
    let timer = null;
    const persist = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const { patientId } = context();
        saveDraft(draftKey(patientId, form.elements.leg.value), {
          values: readForm(form),
          saved_at: Date.now(),
        });
      }, 400);
    };
    form.addEventListener('input', persist);
    form.addEventListener('change', persist);

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const { patientId } = context();
      const values = readForm(form);
      const errEl = el('mas-errors');
      const blocked = masGuardReason({ patientId });
      if (blocked) {
        errEl.textContent = blocked;
        errEl.hidden = false;
        return;
      }
      const { ok, errors } = validateMasForm(values);
      if (!ok) {
        errEl.textContent = errors.join(' ');
        errEl.hidden = false;
        return;
      }
      errEl.hidden = true;
      try {
        await saveRecord(makeMasRecord({ patientId, form: values }));
        // Clear every candidate key, not just this leg's: the draft being
        // saved may have been resumed from a different leg's key.
        for (const k of draftCandidateKeys(patientId)) await clearDraft(k);
        el('mas-status').textContent = isPending(values)
          ? 'Saved as pending -- add the overall grade before the study closes.'
          : 'Saved.';
        await renderList();
      } catch (err) {
        // A ConstraintError is the `by_identity` unique index rejecting a
        // duplicate (same participant, leg, condition and date). That is a
        // recoverable situation, not a failure -- say so.
        el('mas-status').textContent = err && err.name === 'ConstraintError'
          ? 'An assessment already exists for this leg on this date. Change the date or edit the existing one.'
          : `Save failed: ${err instanceof Error ? err.message : String(err)}`;
      }
    });

    ready = true;
  }

  async function renderList() {
    const list = el('mas-list');
    list.textContent = '';
    for (const r of await loadRecords()) {
      const row = document.createElement('div');
      row.className = 'card mas-row';
      const t = document.createElement('p');
      t.className = 'tile-title';
      t.textContent = `${r.leg} · ${isPending(r) ? 'pending' : r.mas_grade}`;
      const m = document.createElement('p');
      m.className = 'tile-sub';
      m.textContent = `${r.assessed_date}${r.condition ? ` · ${r.condition}` : ''}`;
      row.append(t, m);
      if (isPending(r)) row.classList.add('is-pending');
      list.append(row);
    }
  }

  return {
    async onEnter() {
      initOnce();
      const { patientId, participantLabel, side } = context();
      const stored = [];
      for (const k of draftCandidateKeys(patientId)) stored.push(await loadDraft(k));
      const draft = pickNewestDraft(stored);
      if (draft) {
        writeForm(form, draft);
      } else {
        // Prefill rather than leave blank: the participant and leg are
        // already known from the session, and re-typing them is where a
        // transcription error enters.
        writeForm(form, {
          participant: participantLabel,
          leg: LEG_OPTIONS.includes(side) ? side : '',
          assessed_date: new Date().toISOString().slice(0, 10),
        });
      }
      el('mas-status').textContent = '';
      el('mas-errors').hidden = true;
      await renderList();
    },
  };
}
