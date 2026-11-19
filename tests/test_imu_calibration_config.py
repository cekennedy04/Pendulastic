import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import imu_calibration_config as cfgmod


def test_load_config_returns_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "missing.json"))
    cfg = cfgmod.load_config()
    assert cfg == cfgmod.DEFAULT_CONFIG


def test_save_then_load_roundtrips(tmp_path, monkeypatch):
    path = str(tmp_path / "cfg.json")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    written = {
        "beta": 0.08, "ema_alpha": 0.5,
        "flex_axis_capture": False, "gravity_seed": True,
        "penalty": 1.23, "passes": True,
        "tuned_at": "2026-07-30T00:00:00+00:00", "source_trial": "PID_1_imu.csv",
        "method": "ockendon",
    }
    cfgmod.save_config(written)
    assert cfgmod.load_config() == written


def test_save_writes_atomically_no_tmp_file_left_behind(tmp_path, monkeypatch):
    path = str(tmp_path / "cfg.json")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    cfgmod.save_config(dict(cfgmod.DEFAULT_CONFIG))
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


def test_load_config_falls_back_on_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG


def test_load_config_falls_back_on_missing_key(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"beta": 0.1}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG


def test_load_config_falls_back_on_wrong_type(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    bad = dict(cfgmod.DEFAULT_CONFIG)
    bad["flex_axis_capture"] = "yes"   # must be bool, not str
    path.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG


def test_load_config_fills_default_method_for_legacy_configs_missing_it(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    legacy = {k: v for k, v in cfgmod.DEFAULT_CONFIG.items() if k != "method"}
    legacy.update({"beta": 0.08, "ema_alpha": 0.1,
                  "flex_axis_capture": False, "gravity_seed": False})
    path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config()["method"] == "relative"


def test_the_default_fallback_is_announced_once(tmp_path, monkeypatch, capsys):
    """A missing config must not pass silently: DEFAULT_CONFIG is ema_alpha 0.3
    and the corpus is scored at 0.5 (a published corpus table was withdrawn
    over exactly this, 2026-11-19)."""
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(cfgmod, "_fallback_announced", False)
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG
    err = capsys.readouterr().err
    assert err.count("using DEFAULT_CONFIG") == 1 and "not the reference footing" in err


def test_evidence_scripts_get_an_error_not_the_default(tmp_path, monkeypatch):
    import pytest
    path = tmp_path / "imu_calibration_config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    with pytest.raises(cfgmod.ReplayConfigMissing, match="REFERENCE_REPLAY_CONFIG"):
        cfgmod.load_persisted_config()
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(cfgmod.ReplayConfigMissing, match="not valid JSON"):
        cfgmod.load_persisted_config()
    path.write_text('{"beta": 0.041}', encoding="utf-8")
    with pytest.raises(cfgmod.ReplayConfigMissing):
        cfgmod.load_persisted_config()
    cfgmod.save_config(dict(cfgmod.DEFAULT_CONFIG, ema_alpha=0.5))
    assert cfgmod.load_persisted_config()["ema_alpha"] == 0.5


def test_only_the_app_side_may_fall_back_to_the_default():
    """Every tracked caller of load_config(), by name. A corpus or evidence
    script added to this list needs a reason; it should be using
    REFERENCE_REPLAY_CONFIG or load_persisted_config()."""
    import os
    import re
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = subprocess.check_output(["git", "ls-files", "-z", "*.py"], cwd=root, text=True).split(chr(0))
    callers = set()
    for rel in files:
        if (not rel or rel.startswith(("tests/", "archive/")) or "/tests/" in rel
                or rel == "imu_calibration_config.py"):
            continue
        src = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        if re.search(r"(?<![A-Za-z_])load_config\(", src):
            callers.add(rel)
    assert callers == {
        "imu_calibration_tuner.py",    # tune_and_persist compares against what is saved
        "motor_bench.py",              # the bench runs on the live app's config by design
        "pendulastic_app.py",          # the live app
        "pendulastic_imu_server.py",   # the live app
        "pendulastic_pt_score.py",     # _replay_raw_imu_fallback: desktop path, deliberately left on the default
        "tune_imu.py",                 # shows the saved config
        "workbench_engine.py",         # load_imu_trial's default when no config is passed
    }, sorted(callers)
