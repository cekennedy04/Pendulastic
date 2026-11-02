"""Format-level tests for labview_bin_reader: big-endian, 7-channel stride, MATLAB baseline semantics."""
import numpy as np
import pytest

from labview_bin_reader import CHANNELS_7, normalize_torque, read_labview_bin


def _write(tmp_path, data, name="t.bin"):
    """data: (channels, samples) -> interleaved big-endian doubles on disk, like LabVIEW."""
    p = tmp_path / name
    np.ascontiguousarray(data.T, dtype=">f8").tofile(p)
    return p


def test_round_trip_7_channels(tmp_path):
    rng = np.random.default_rng(0)
    data = rng.normal(size=(7, 200))
    trial = read_labview_bin(_write(tmp_path, data))
    for i, name in enumerate(CHANNELS_7):
        np.testing.assert_array_equal(trial[name], data[i])
    assert trial["time"][1] == pytest.approx(0.001)
    assert len(trial["time"]) == 200


def test_infers_6_channel_legacy_layout(tmp_path):
    data = np.arange(6 * 7, dtype=float).reshape(6, 7)   # 42 doubles: divides by 6 and 7 -> 7 wins
    trial = read_labview_bin(_write(tmp_path, data), n_channels=6)
    assert "velocity_setpoint" not in trial
    np.testing.assert_array_equal(trial["emg_edc"], data[5])


def test_rejects_empty_and_ragged(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        read_labview_bin(p)
    p.write_bytes(b"\0" * 8 * 5)   # 5 doubles: neither 6 nor 7
    with pytest.raises(ValueError, match="multiple of 6 or 7"):
        read_labview_bin(p)


def test_baseline_picks_quieter_end_and_uses_matlab_windows():
    # first 150 samples sit at +1.0, last 151 at +0.2, the middle is a bump.
    torque = np.zeros(1000)
    torque[:150] = 1.0
    torque[-151:] = 0.2
    torque[150] = 5.0   # makes end-150:end (151 samples) differ from the last 150
    torque[-151] = 5.0
    out = normalize_torque(torque)
    # end window mean = (150*0.2 + 5.0)/151 = 0.2318; that is the smaller |baseline|
    expected_base = (150 * 0.2 + 5.0) / 151
    # far from the bumps and the filter edges the output is just torque - base
    assert out[500] == pytest.approx(-expected_base, abs=1e-6)
