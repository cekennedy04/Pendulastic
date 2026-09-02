// Participant identity, the trial side, and the export/close controls.
//
// Replaces app.js's FIXED_PATIENT_ID/FIXED_PATIENT_LABEL, which hardcoded
// every trial on every device to one synthetic participant. The state
// transitions are a pure reducer so the "cannot switch mid-session" rule is
// testable without a DOM.

import { LEG_OPTIONS } from '../mas-store.js';

export const SETTING_KEYS = {
  activePatient: 'active-patient',
  side: 'trial-side',
};

// Legacy rows anchor trials recorded before participant entry existed (see
// db.js's legacyPatientPatches). They stay listed and exportable; the suffix
// is so a clinician can tell them apart from one they typed.
export function patientLabel(patient) {
  const id = patient && patient.clinic_patient_id;
  if (!id) return 'no participant set';
  return patient.legacy === true ? `${id} (legacy)` : id;
}

// Pure. `state` is `{patient, side, trialCount}`; the returned state carries
// an `error` string when an action was refused.
export function nextParticipantState(state, action) {
  if (action.type === 'side') {
    if (!LEG_OPTIONS.includes(action.side)) return { ...state, error: undefined };
    return { ...state, side: action.side, error: undefined };
  }

  if (action.type === 'select') {
    // A session's trials all belong to one participant -- makeSessionRecord
    // stores patient_id on the SESSION, not the trial. Switching now would
    // silently file the next trial under a different person than the ones
    // already recorded, and the export manifest would name only one of them.
    if (state.trialCount > 0 && state.patient && state.patient.id !== action.patient.id) {
      return {
        ...state,
        error: 'This session already has trials. Export and close it before switching participant.',
      };
    }
    return { ...state, patient: action.patient, error: undefined };
  }

  return state;
}

// The view itself. Everything it needs is injected, so this module stays
// import-safe under `node --test` and the pure reducer above can be tested
// without a DOM.
export function createSessionView({ el, context, listPatients, addPatient, selectPatient, selectSide }) {
  let ready = false;

  function initOnce() {
    if (ready) return;

    el('participant-select').addEventListener('change', async (e) => {
      const { patients } = await listPatients();
      const chosen = patients.find((p) => p.id === e.target.value) || null;
      if (chosen) await selectPatient(chosen);
      await render();
    });

    el('participant-add').addEventListener('click', async () => {
      const raw = el('participant-new').value.trim();
      const errEl = el('participant-error');
      if (!raw) {
        errEl.textContent = 'Enter a participant ID first.';
        errEl.hidden = false;
        return;
      }
      errEl.hidden = true;
      await addPatient(raw);
      el('participant-new').value = '';
      await render();
    });

    el('side-select').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-side]');
      if (!btn) return;
      await selectSide(btn.dataset.side);
      await render();
    });

    ready = true;
  }

  async function render() {
    const { patients } = await listPatients();
    const { patient, side, error } = context();

    const select = el('participant-select');
    select.textContent = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'no participant set';
    select.append(none);
    // createElement + textContent, not innerHTML: clinic_patient_id is free
    // text a clinician types.
    for (const p of patients) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = patientLabel(p);
      select.append(opt);
    }
    select.value = patient ? patient.id : '';

    for (const btn of el('side-select').querySelectorAll('[data-side]')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.side === side));
    }

    const errEl = el('participant-error');
    errEl.textContent = error || '';
    errEl.hidden = !error;
  }

  return {
    async onEnter() {
      initOnce();
      await render();
    },
  };
}
