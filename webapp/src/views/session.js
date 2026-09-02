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

// Which view a launch lands on. Pure so the launch rule is testable without a
// DOM or a database.
//
// Opening on the tiles when no participant is set invites the operator to tap
// Record Trial first; the Start handler would then refuse, which teaches the
// gate by failure. Opening on selection teaches it by layout instead.
export function initialView({ patient } = {}) {
  return patient ? 'home' : 'session';
}

// The build line shown at the foot of the session view. Pure, so the format is
// pinned by a test rather than by whatever the DOM happened to render.
//
// BUILD_ID leads because it is the service worker's actual cache key: matching
// it against what the deployment serves proves the device is running exactly
// those bytes, which is the only question a tester needs answered. A git
// revision cannot answer it -- the commit containing a generated build-id.js
// does not exist at the moment it is generated, which is precisely the
// off-by-one ALGORITHM_VERSION already carries.
export function buildInfoText({ buildId, algorithmVersion } = {}) {
  return `build ${buildId || 'unknown'} · algorithm ${algorithmVersion || 'unknown'}`;
}

// Which participant a launch should resume, given the stored setting and every
// participant on the device. Pure, because the three-way distinction below is
// the whole rule and it must not live inside an IndexedDB callback.
//
// Whether the `settings` row EXISTS is load-bearing, not incidental:
//
//   absent              -- never chosen. A pre-v2 install carrying one legacy
//                          participant adopts it rather than forcing a choice
//                          a clinician mid-study never had to make.
//   present, value set  -- a chosen participant.
//   present, value null -- deliberately cleared by Close Session. Re-prompt.
//
// Collapsing the last two into "no value" would re-adopt the participant the
// operator just closed, on exactly the single-participant device where that is
// most likely, and the prompt would never appear.
export function resolveActivePatient({ activeSetting, patients = [] } = {}) {
  if (activeSetting && activeSetting.value) {
    return patients.find((p) => p.id === activeSetting.value) ?? null;
  }
  if (activeSetting) return null;
  if (patients.length === 1) return patients[0];
  return null;
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
export function createSessionView({
  el, context, listPatients, addPatient, selectPatient, selectSide, countPending,
}) {
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

    // Shown only when there is nothing selected. This is guidance, not an
    // error, so it uses .field-status rather than .field-error.
    const needEl = el('participant-required');
    if (needEl) needEl.hidden = Boolean(patient);

    // A notice, never a block. A pending row is legitimate and the desktop
    // ingests it; the failure this guards against is forgetting one, not
    // exporting one.
    const pending = countPending ? await countPending() : 0;
    const pendEl = el('mas-pending-count');
    if (pendEl) {
      pendEl.textContent = pending
        ? `${pending} MAS assessment${pending === 1 ? '' : 's'} still marked "not yet assessed".`
        : '';
      pendEl.hidden = pending === 0;
    }
  }

  return {
    async onEnter() {
      initOnce();
      await render();
    },
  };
}
