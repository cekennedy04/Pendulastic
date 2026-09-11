"""
data_purpose.py
===============
Decide whether a participant's recording and IMU data may be used for RESULTS
or is training-only.

The rule (operator instruction, 2026-08-28)
-------------------------------------------
**If there is no OptiTrack `.tak` for a participant, mark their recording and
IMU data as training-only so it does not get used for results.**

The `.tak` is the raw Motive take. It is the only artefact the optical ground
truth can be regenerated from: a CSV export is a derived product, and when it
turns out to be wrong -- exported in the rigid body's local frame, or with the
markers unreconstructed -- a `.tak` is what lets it be fixed. Without one there
is no way back to the source, so any IMU or video from that session can never
be validated against a trustworthy optical reference. It can still teach a
model; it cannot support a result.

What this does NOT mean (operator clarification, 2026-08-28)
------------------------------------------------------------
**No participant is disqualified by this.** The label applies to the IMU stream
only. A participant with RGB video and OptiTrack but no IMU at all is perfectly
acceptable for results -- missing IMU is not a defect, it is a different
modality mix. P2, P4 and P6-P12 stay in the analysis on their optical data;
what is set aside is the use of their IMU as a validated measurement.

Two further things this deliberately does NOT do
------------------------------------------------
It does not delete, move or hide anything. `excluded_trials.json` remains the
only mechanism that removes a trial, and this module only labels. A caller that
wants results-grade data asks for it; nothing disappears from a listing.

It does not decide what happens to a participant's OPTICAL data. The rule is
about "the recording and imu data". A participant with no `.tak` but a working
OptiTrack CSV export still has an optical curve, and whether that curve is
results-grade is a separate judgement the operator has not delegated here --
see `RESULTS_OPTICAL_WITHOUT_TAK`.
"""
from __future__ import annotations

import glob
import os
from typing import NamedTuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
REC_ROOT = os.path.join(BASE_DIR, "Recordings")

PURPOSE_RESULTS = "results"
PURPOSE_TRAINING = "training"

# Folder names under Recordings/ that are not real participants. A participant
# id is a bare number; anything else is scaffolding or a stray import
# (Participant_test, Participant_P001_msparticipant2). Checked by shape rather
# than by an ever-growing deny list, with the list kept for numeric oddities.
NON_PARTICIPANT_IDS = {"test", "0", "demo"}


def _is_participant_id(pid: str) -> bool:
    pid = (pid or "").strip()
    return pid.isdigit() and pid.lower() not in NON_PARTICIPANT_IDS

# Whether an OptiTrack CSV that has no .tak behind it may still back a result.
# True keeps the existing optical curves usable (they load, and several are the
# cleanest in the corpus) while still marking the IMU/video training-only, which
# is what the instruction names. Flip to False to treat a missing .tak as
# disqualifying for that participant's optical data too.
RESULTS_OPTICAL_WITHOUT_TAK = True


# Modalities a participant may legitimately have. An absent one is recorded,
# never penalised: RGB + OptiTrack with no IMU is an acceptable complete set.
ACCEPTABLE_WITHOUT_IMU = True


class DataPurpose(NamedTuple):
    participant: str
    has_tak: bool
    n_tak: int
    has_optitrack_csv: bool
    has_imu: bool
    imu_purpose: str
    optical_purpose: str
    reason: str


def _count(root: str, pid: str, pattern: str) -> int:
    return len(glob.glob(os.path.join(root, f"Participant_{pid}", "**", pattern),
                         recursive=True))


def participant_ids() -> list:
    ids = set()
    for root in (OPTI_ROOT, REC_ROOT):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if not name.startswith("Participant_"):
                continue
            pid = name.replace("Participant_", "").strip()
            if _is_participant_id(pid):
                ids.add(pid)
    return sorted(ids, key=lambda x: int(x) if x.isdigit() else 10 ** 6)


def classify(pid: str) -> DataPurpose:
    n_tak = _count(OPTI_ROOT, pid, "*.tak")
    n_csv = _count(OPTI_ROOT, pid, "trial_*_optitrack.csv")
    n_imu = _count(REC_ROOT, pid, "*_imu.csv")
    n_rgb = _count(REC_ROOT, pid, "*.avi") + _count(REC_ROOT, pid, "*.mp4")

    has_tak = n_tak > 0
    if n_imu == 0:
        # Not a gap to flag. RGB + OptiTrack is an acceptable complete set, so
        # there is no IMU stream to qualify one way or the other.
        return DataPurpose(pid, has_tak, n_tak, n_csv > 0, False,
                           PURPOSE_RESULTS, PURPOSE_RESULTS,
                           f"no IMU recorded; {n_rgb} RGB file(s) and "
                           f"{n_csv} optical trial(s) -- acceptable as-is")
    if has_tak:
        return DataPurpose(pid, True, n_tak, n_csv > 0, n_imu > 0,
                           PURPOSE_RESULTS, PURPOSE_RESULTS,
                           f"{n_tak} .tak take(s) present; optical ground truth "
                           "can be regenerated")

    optical = (PURPOSE_RESULTS if (n_csv > 0 and RESULTS_OPTICAL_WITHOUT_TAK)
               else PURPOSE_TRAINING)
    reason = ("no .tak: the optical export cannot be regenerated or corrected, "
              "so the IMU/video has no verifiable reference")
    if n_csv == 0:
        reason += " and there is no optical export either"
    return DataPurpose(pid, False, 0, n_csv > 0, n_imu > 0,
                       PURPOSE_TRAINING, optical, reason)


def reexportable_conditions(pid: str) -> dict:
    """{(leg, condition): n_tak} for one participant.

    Per condition, not per participant, because a `.tak` can exist for one
    session and not another. P17 is the case that forced this: its `post`
    session has 8 takes and can be re-exported, while its `pre` session has
    none and its OptiTrack export is an empty directory. Reporting P17 as
    simply "has takes" would have implied the pre data was recoverable.
    """
    out = {}
    root = os.path.join(OPTI_ROOT, f"Participant_{pid}")
    for path in glob.glob(os.path.join(root, "**", "*.tak"), recursive=True):
        rel = os.path.relpath(path, root).replace("\\", "/").split("/")
        if len(rel) >= 2:
            out[(rel[0].lower(), rel[1].lower())] = out.get(
                (rel[0].lower(), rel[1].lower()), 0) + 1
    return out


def unattributed_folders() -> list:
    """Every Participant_* folder that is NOT recognised as a participant.

    Returned so it can be REPORTED rather than silently skipped. Naming
    conventions have drifted across the study -- Participant_P001_msparticipant2
    is a legacy HPE-benchmark import with an old id scheme and no metadata --
    and a folder that quietly fails the id test looks exactly like a folder that
    was never there. Each entry is (root, folder, n_files) so the size of what
    is being passed over is visible.
    """
    out = []
    for root in (OPTI_ROOT, REC_ROOT):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if not os.path.isdir(full) or not name.startswith("Participant_"):
                continue
            pid = name.replace("Participant_", "").strip()
            if _is_participant_id(pid):
                continue
            n = sum(len(files) for _d, _s, files in os.walk(full))
            out.append((os.path.basename(root), name, n))
    return out


def classify_all() -> dict:
    return {pid: classify(pid) for pid in participant_ids()}


def training_only_participants() -> list:
    """Participants whose recording/IMU data is training-only."""
    return [pid for pid, dp in classify_all().items()
            if dp.imu_purpose == PURPOSE_TRAINING]


def imu_is_results_grade(pid: str) -> bool:
    return classify(pid).imu_purpose == PURPOSE_RESULTS


def write_manifest(out_path: str = None) -> str:
    """Write the purpose of every participant's data to a CSV audit trail."""
    import csv
    out_path = out_path or os.path.join(
        BASE_DIR, "Model_Analysis_Outputs", "data_purpose_manifest.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["participant", "n_tak", "has_optitrack_csv", "has_imu",
                    "imu_purpose", "optical_purpose", "reason"])
        for pid, dp in classify_all().items():
            w.writerow([pid, dp.n_tak, "yes" if dp.has_optitrack_csv else "no",
                        "yes" if dp.has_imu else "no",
                        dp.imu_purpose, dp.optical_purpose, dp.reason])
    return out_path


def main():
    rows = classify_all()
    print(f"{'pid':>4} {'.tak':>5} {'csv':>4} {'imu':>4} {'imu use':<9} "
          f"{'optical use':<12} reason")
    for pid, dp in rows.items():
        print(f"{pid:>4} {dp.n_tak:>5} {'y' if dp.has_optitrack_csv else 'n':>4} "
              f"{'y' if dp.has_imu else 'n':>4} {dp.imu_purpose:<9} "
              f"{dp.optical_purpose:<12} {dp.reason[:58]}")
    print("")
    print("re-exportable sessions (a .tak exists, so the optical export can "
          "be regenerated):")
    for pid in rows:
        conds = reexportable_conditions(pid)
        if conds:
            detail = ", ".join(f"{leg}/{cond} x{n}"
                               for (leg, cond), n in sorted(conds.items()))
            print(f"   P{pid:<4} {detail}")

    training = training_only_participants()
    print(f"\nIMU stream set aside as training-only: {len(training)} "
          f"participant(s): {', '.join('P' + p for p in training) or '(none)'}")
    print("   These participants are NOT disqualified -- their optical and RGB "
          "data stay results-grade, and a participant with no IMU at all is "
          "an acceptable RGB+OptiTrack set.")
    of = [pid for pid, dp in rows.items() if dp.optical_purpose == PURPOSE_TRAINING]
    print(f"optical data set aside:               {len(of)} participant(s): "
          f"{', '.join('P' + p for p in of) or '(none)'}")

    stray = unattributed_folders()
    print("")
    if stray:
        print("folders NOT counted as participants (reported, never hidden -- "
              "naming schemes have drifted, so a folder that quietly fails the "
              "id test looks exactly like one that was never there):")
        for root, name, n_files in stray:
            print(f"   {root}/{name}  ({n_files} file(s))")
    else:
        print("every Participant_* folder was attributed to a participant.")
    print(f"\n-> {write_manifest()}")


if __name__ == "__main__":
    main()
