"""The portable 1 kHz model must reproduce the validated RK4 reference.

If these fail, the rig is not running the published model, whatever its other
behaviour looks like -- so they gate everything built on model_rt.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import pendulum_sim as ref  # noqa: E402
from neuromech.model_rt import Params, simulate, passive_rest_deg, DT  # noqa: E402

TOL_DEG = 0.5   # over a full 10 s trace; the phone's static accuracy is 0.58 deg


def _ref(tone, k_f, k_df, B, incline):
    t, th = ref.simulate(tone, k_f, k_df, s=ref.L_SCALE_NOMINAL, B=B, dt=5e-4, duration=10.0,
                         thigh_incline_deg=incline)
    return t[::2], th[::2]     # onto the 1 kHz grid


def _rt(tone, k_f, k_df, B, incline):
    r = simulate(Params(tone=tone, k_f=k_f, k_df=k_df, B=B, incline_deg=incline), duration=10.0)
    return r["t"], r["theta_deg"]


def test_passive_matches_reference():
    _, a = _ref(0.0, 0.0, 0.0, ref.D_NOMINAL, -35.0)
    _, b = _rt(0.0, 0.0, 0.0, ref.D_NOMINAL, -35.0)
    err = np.max(np.abs(a - b))
    assert err < TOL_DEG, f"passive max |err| {err:.3f} deg"


def test_nominal_spastic_matches_reference():
    """The paper's own spastic CP parameters: tone, SRS and the delayed force reflex all active."""
    _, a = _ref(ref.TB_NOMINAL, ref.KF_NOMINAL, ref.KDF_NOMINAL, ref.D_NOMINAL, -35.0)
    _, b = _rt(ref.TB_NOMINAL, ref.KF_NOMINAL, ref.KDF_NOMINAL, ref.D_NOMINAL, -35.0)
    err = np.max(np.abs(a - b))
    assert err < TOL_DEG, f"spastic max |err| {err:.3f} deg"


def test_strong_spastic_matches_reference():
    _, a = _ref(2 * ref.TB_NOMINAL, 2 * ref.KF_NOMINAL, 2 * ref.KDF_NOMINAL, ref.D_NOMINAL, -35.0)
    _, b = _rt(2 * ref.TB_NOMINAL, 2 * ref.KF_NOMINAL, 2 * ref.KDF_NOMINAL, ref.D_NOMINAL, -35.0)
    err = np.max(np.abs(a - b))
    assert err < TOL_DEG, f"strong spastic max |err| {err:.3f} deg"


def test_new_modules_are_off_by_default():
    """With every NEW module at its default the RT model is exactly the reference model."""
    r0 = simulate(Params(tone=2.0, k_f=0.75, k_df=0.025), duration=6.0)["theta_deg"]
    r1 = simulate(Params(tone=2.0, k_f=0.75, k_df=0.025, flex_gain=0.0, catch_gain=0.0, clonus_deg=0.0,
                         rigid_nm=0.0, contract_k=0.0, guard_nm=0.0, spasm_nm=0.0), duration=6.0)["theta_deg"]
    assert np.array_equal(r0, r1)


def test_rest_angle_matches_clinic():
    """incline -35 deg should hang the passive leg near the controls' ~125 deg (Pendulastic) rest."""
    rest = 180.0 + passive_rest_deg(Params())
    assert 120.0 < rest < 130.0, rest


def test_lead_pipe_decays_linearly_not_exponentially():
    """Coulomb-like rigidity removes a FIXED amplitude per half-cycle; viscous damping a fixed RATIO.
    That envelope shape is the rigidity signature the scenarios rely on."""
    r = simulate(Params(B=0.02, rigid_nm=0.6), duration=8.0)
    th = r["theta_deg"] - np.median(r["theta_deg"][-1000:])
    pk = [abs(th[i]) for i in range(1, len(th) - 1) if abs(th[i]) > 2 and (th[i] - th[i - 1]) * (th[i + 1] - th[i]) < 0]
    d = np.diff(pk[:5])
    assert np.std(d) / abs(np.mean(d)) < 0.35, f"amplitude steps {d}"


def test_clonus_adds_its_frequency_and_size():
    """The clonus module must add beats at the programmed frequency and roughly the programmed size."""
    kw = dict(tone=1.0, k_f=0.4, k_df=0.013)
    r = simulate(Params(**kw, clonus_deg=4.0, clonus_hz=6.0), duration=6.0)
    base = simulate(Params(**kw), duration=6.0)["theta_deg"]
    t = r["t"]; d = (r["theta_deg"] - base)[(t > 1.0) & (t < 2.0)]
    f = np.fft.rfftfreq(len(d), DT); spec = np.abs(np.fft.rfft(d - d.mean()))
    band = (f > 3) & (f < 12)
    assert abs(f[band][np.argmax(spec[band])] - 6.0) < 0.6
    # beat size = the 3-12 Hz component only; the burst also phase-shifts the ~1 Hz swing
    beat = np.fft.irfft(np.where(band, np.fft.rfft(d - d.mean()), 0), n=len(d))
    assert 2.0 < np.max(np.abs(beat)) < 8.0, np.max(np.abs(beat))


def test_formula_node_integrator_matches_reference():
    """The LabVIEW Formula Node implements integrator="euler4"; it must stay within tolerance too."""
    for tb, kf, kdf in ((0, 0, 0), (2.0, 0.75, 0.025), (4.0, 1.5, 0.05)):
        _, a = _ref(tb, kf, kdf, ref.D_NOMINAL, -35.0)
        b = simulate(Params(tone=tb, k_f=kf, k_df=kdf), duration=10.0, integrator="euler4")["theta_deg"]
        assert np.max(np.abs(a - b)) < TOL_DEG
