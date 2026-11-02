"""Pairing the designer's trial log with a phone export (neuromech.match_trials)."""
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import match_trials as MT  # noqa: E402


def _trials(starts, phone_trial=None):
    return pd.DataFrame({"trial": [str(i + 1) for i in range(len(starts))], "label": [f"L{i + 1}" for i in range(len(starts))],
                         "pc_start": starts, "rig_file": [f"trial_files\\T{i + 1:03d}.csv" for i in range(len(starts))],
                         "phone_trial": phone_trial or [""] * len(starts), "pred_f": ["1"] * len(starts)})


def _ms(s, off_s):
    return int((datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp() + off_s) * 1000)


START = ["2026-11-02 10:00:00", "2026-11-02 10:02:00", "2026-11-02 10:04:00"]


def test_order_comes_from_the_phone_timestamp_not_the_file_number():
    # file numbers scrambled against capture order, as the app writes them
    phone = [{"file": f"x-trial{n}.jsonl", "timestamp": _ms(s, 37), "params": {"f": 1.0 + n}} for n, s in zip((3, 1, 2), START)]
    out = MT.pair(_trials(START), phone)
    assert list(out.phone_file) == ["x-trial3.jsonl", "x-trial1.jsonl", "x-trial2.jsonl"]
    assert list(out.phone_f) == [4.0, 2.0, 3.0]
    assert all("CHECK" not in c for c in out.clock_check)


def test_phone_trial_typed_at_the_bench_wins_and_the_rest_fill_in_order():
    phone = [{"file": f"x-trial{n}.jsonl", "timestamp": _ms(s, 5), "params": {}} for n, s in zip((1, 2, 3), START)]
    out = MT.pair(_trials(START, ["", "3", ""]), phone)
    assert list(out.phone_file) == ["x-trial1.jsonl", "x-trial3.jsonl", "x-trial2.jsonl"]
    assert list(out.pairing) == ["capture order", "phone_trial", "capture order"]
    # trial 3 got trial2's recording, made two minutes before it: the clock check says so
    assert "CHECK" in out.clock_check.iloc[2]


def test_a_false_start_on_the_phone_is_skipped_not_shifted_into_every_later_pair():
    # an extra recording made between trials 1 and 2 must not push trials 2 and 3 onto the wrong files
    t = ["2026-11-02 10:00:00", "2026-11-02 10:01:00", "2026-11-02 10:02:00", "2026-11-02 10:04:00"]
    phone = [{"file": f"x-trial{i + 1}.jsonl", "timestamp": _ms(s, 41), "params": {}} for i, s in enumerate(t)]
    out = MT.pair(_trials(START), phone)
    assert list(out.phone_file) == ["x-trial1.jsonl", "x-trial3.jsonl", "x-trial4.jsonl"]
    assert all("CHECK" not in c for c in out.clock_check)


def test_a_missing_recording_is_skipped_when_the_spacing_shows_which_one():
    starts = ["2026-11-02 10:00:00", "2026-11-02 10:01:10", "2026-11-02 10:04:30"]   # uneven gaps
    phone = [{"file": f"x-trial{i + 1}.jsonl", "timestamp": _ms(s, 12), "params": {}} for i, s in enumerate([starts[0], starts[2]])]
    out = MT.pair(_trials(starts), phone)
    assert list(out.phone_file) == ["x-trial1.jsonl", "", "x-trial2.jsonl"]
    assert out.pairing.iloc[1] == "no phone recording fits"
    assert not any("AMBIGUOUS" in p for p in out.pairing)


def test_evenly_spaced_trials_with_a_missing_recording_are_called_ambiguous_not_guessed():
    phone = [{"file": f"x-trial{i + 1}.jsonl", "timestamp": _ms(s, 0), "params": {}} for i, s in enumerate(START[1:])]
    out = MT.pair(_trials(START), phone)
    assert any("AMBIGUOUS" in p for p in out.pairing)
