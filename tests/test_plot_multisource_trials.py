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
