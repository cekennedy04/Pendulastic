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
