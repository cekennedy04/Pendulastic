import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import plot_multisource_trials as pmt


def test_modality_key_collapses_mediapipe_variants():
    assert pmt._modality_key("mediapipe_full_0.5") == "mediapipe"
    assert pmt._modality_key("mediapipe_lite_0.3") == "mediapipe"


def test_modality_key_recognizes_imu_viewer():
    assert pmt._modality_key("imu_viewer") == "imu"


def test_modality_key_ignores_other_hpe_models():
    assert pmt._modality_key("rtmpose_m") is None
    assert pmt._modality_key("mmpose") is None
    assert pmt._modality_key("fremocap") is None


# ── lateral-motion row (instrument-validation plan U6) ───────────────────────

def _figure_trial(lateral="absent"):
    import numpy as np
    import pendulastic_pt_score as pt
    t = np.arange(1200) / 120.0
    s = np.clip(t - 1.5, 0.0, None)
    ang = 180.0 - np.where(t < 1.5, 0.0, 60.0 * (1 - np.exp(-0.6 * s) * np.cos(2 * np.pi * 0.9 * s)))
    params = pt.compute_pt_params(t, ang)
    trial = dict(participant="901", leg="left", condition="pre", trial="1",
                 opti_t=t, opti_ang=ang, opti_params=params,
                 opti_score=pt.compute_pt_score(params), modalities={})
    if lateral != "absent":
        trial["lateral"] = lateral
    return trial, t


def _axes_of(trial, tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    import plot_multisource_trials as pm
    seen = {}
    real_close = plt.close
    monkeypatch.setattr(pm.plt, "close", lambda fig: (seen.update(fig=fig), real_close(fig)))
    out = tmp_path / "trial.png"
    pm.make_trial_figure(trial, str(out))
    assert out.exists() and out.stat().st_size > 0
    return seen["fig"].axes


def test_trial_figure_without_lateral_data_keeps_its_two_rows(tmp_path, monkeypatch):
    trial, _t = _figure_trial()
    assert len(_axes_of(trial, tmp_path, monkeypatch)) == 2


def test_trial_figure_draws_a_non_flat_lateral_trace_when_given_one(tmp_path, monkeypatch):
    import numpy as np
    trial, t = _figure_trial()
    trial["lateral"] = {"optitrack": (t, 8.0 * np.abs(np.sin(t))), "imu": (t, 6.0 * np.abs(np.sin(t)))}
    axes = _axes_of(trial, tmp_path, monkeypatch)
    assert len(axes) == 3
    lines = axes[1].get_lines()
    assert len(lines) == 2
    assert np.ptp(lines[0].get_ydata()) > 5.0


def test_trial_figure_draws_a_flat_trace_for_a_pure_hinge(tmp_path, monkeypatch):
    import numpy as np
    trial, t = _figure_trial()
    trial["lateral"] = {"optitrack": (t, np.zeros_like(t)), "imu": None}
    axes = _axes_of(trial, tmp_path, monkeypatch)
    assert len(axes) == 3
    assert np.ptp(axes[1].get_lines()[0].get_ydata()) == 0.0


def test_trial_figure_survives_a_missing_or_empty_lateral_source(tmp_path, monkeypatch):
    import numpy as np
    trial, t = _figure_trial()
    trial["lateral"] = {"optitrack": (t, np.full_like(t, np.nan)), "imu": None}
    assert len(_axes_of(trial, tmp_path, monkeypatch)) == 2
