"""Group-prefixed participant folder naming, and the resolver that reads it.

Participant recording folders used to be named ``Participant_{pid}`` in both
``Recordings/`` and ``OptiTrack_Recordings/``.  As of 2026-09-01 they carry the
parent study instead, so a folder can be identified at a glance:

    Control_{pid}   unaffected controls
    CHAT_{pid}      stroke participants
    MINT_{pid}      MS participants (spelled DOSA_{pid} earlier the same day)
    Participant_{pid}   legacy / ungrouped (e.g. "Other Motor Impairment",
                        Participant_test, Participant_P001_msparticipant2)

Every prefix stays readable.  Nothing in the pipeline should hard-code a
prefix: build paths with :func:`find_participant_dir` / :func:`participant_dir_name`,
parse them with :func:`pid_from_dir_name`, and enumerate them with
:func:`iter_participant_dirs` or :func:`glob_participants`.  All of those accept
BOTH the new and the legacy names, so a half-migrated tree still resolves.

The diagnosis -> prefix mapping is derived from the participant's own
``metadata.json`` (the primary record written by master_app), which is why
renaming is reversible: the folder name is a cache of a fact stored inside it.
"""

import glob as _glob
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")

#: Study prefix per participant group.
CONTROL_PREFIX = "Control"
STROKE_PREFIX = "CHAT"
MS_PREFIX = "MINT"
#: The MS study's former name.  Never written for a new folder, but still
#: resolved so an un-renamed archive (or a half-migrated tree) finds its data.
LEGACY_MS_PREFIX = "DOSA"
#: Retained for folders whose diagnosis maps to no study (and for pre-rename data).
LEGACY_PREFIX = "Participant"

GROUP_PREFIXES = (CONTROL_PREFIX, STROKE_PREFIX, MS_PREFIX)
ALL_PREFIXES = GROUP_PREFIXES + (LEGACY_MS_PREFIX, LEGACY_PREFIX)

# Longest-first so "Participant" cannot be shadowed, and case-insensitive
# because Windows paths round-trip with inconsistent casing.
_DIR_RE = re.compile(
    r"^(?:%s)_(?P<pid>.+)$" % "|".join(sorted(ALL_PREFIXES, key=len, reverse=True)),
    re.IGNORECASE,
)


#: Matches "<prefix>_<number>" ANYWHERE in a path, for pulling a participant
#: number out of a relative path rather than off a single folder name.
PATH_PID_RE = re.compile(
    r"(?:%s)_(\d+)" % "|".join(sorted(ALL_PREFIXES, key=len, reverse=True)),
    re.IGNORECASE,
)

#: Same, anchored, with the trailing separator -- for stripping the
#: "<prefix>_<number>_" head off a folder name to leave its condition label.
_LEADING_PID_RE = re.compile(
    r"^(?:%s)_\d+_?" % "|".join(sorted(ALL_PREFIXES, key=len, reverse=True)),
    re.IGNORECASE,
)


def pids_in_path(rel_path):
    """Every distinct participant number appearing in `rel_path`, sorted."""
    return sorted(set(PATH_PID_RE.findall(str(rel_path))))


def strip_leading_pid(part):
    """``"MINT_13_left_post"`` -> ``"left_post"``; unprefixed names pass through."""
    return _LEADING_PID_RE.sub("", str(part))


def has_pid_prefix(part):
    """True when `part` starts with a "<prefix>_<number>" head."""
    return _LEADING_PID_RE.match(str(part)) is not None


def group_for_diagnosis(diagnosis):
    """Map a free-text diagnosis to its folder prefix, or None if ungrouped.

    Accepts every spelling the study has used: "MS", "multiple sclerosis",
    "Unaffected Control", "control", "Stroke".  "Other Motor Impairment" and
    anything unrecognised return None so the caller falls back to
    ``Participant_{pid}`` rather than guessing a study.
    """
    d = (diagnosis or "").strip().lower()
    if not d:
        return None
    if "control" in d:
        return CONTROL_PREFIX
    if "stroke" in d:
        return STROKE_PREFIX
    if d == "ms" or "multiple sclerosis" in d or re.search(r"\bms\b", d):
        return MS_PREFIX
    return None


def pid_from_dir_name(name):
    """``"Control_10"`` -> ``"10"``.  Returns None if `name` is not a participant folder.

    The remainder after the prefix is returned verbatim, matching the old
    ``name.replace("Participant_", "")`` behaviour, so oddities such as
    ``Participant_P001_msparticipant2`` and the legacy
    ``Participant_5_left_side`` layout still yield the same ids they always did.
    """
    m = _DIR_RE.match(os.path.basename(str(name).rstrip("/\\")))
    return m.group("pid").strip() if m else None


def strip_prefix(value):
    """``"Control_10"`` -> ``"10"``; an unprefixed value passes through unchanged.

    Drop-in for the old ``value.replace("Participant_", "")`` calls that turn a
    folder name recorded in a results table back into a participant id.
    """
    return pid_from_dir_name(value) or str(value)


def is_participant_dir_name(name):
    """True when `name` is a participant folder under either naming scheme."""
    return pid_from_dir_name(name) is not None


def dir_name_candidates(pid):
    """Every folder name `pid` could legitimately live under, new names first."""
    return ["%s_%s" % (p, pid) for p in ALL_PREFIXES]


def diagnosis_from_metadata(root, pid):
    """Read the diagnosis out of `pid`'s metadata.json, or None if unavailable."""
    d = find_participant_dir(root, pid)
    if not d:
        return None
    try:
        with open(os.path.join(d, "metadata.json"), encoding="utf-8") as f:
            return json.load(f).get("diagnosis")
    except (OSError, ValueError):
        return None


def find_participant_dir(root, pid):
    """Return the existing folder for `pid` under `root`, or None.

    Exact prefix matches win over the trailing-suffix forms
    (``Participant_5_left_side``) so a participant with both never resolves to
    the legacy sub-layout by accident.
    """
    for name in dir_name_candidates(pid):
        full = os.path.join(root, name)
        if os.path.isdir(full):
            return full
    # Legacy sub-layout: Participant_5_left_side.  Match on the id as a whole
    # segment so pid "2" can never claim Participant_20.
    for found_pid, _name, full in iter_participant_dirs(root):
        if found_pid.startswith("%s_" % pid):
            return full
    return None


def participant_dir_name(pid, root=None, diagnosis=None):
    """The folder name to USE for `pid` -- the one on disk, else a derived one.

    Resolution order: an existing folder under `root` (so a rename is never
    fought), then `diagnosis`, then `pid`'s recorded metadata, then the legacy
    ``Participant_{pid}``.  This is what writers (master_app) should call so new
    captures land beside the participant's existing data.
    """
    if root:
        existing = find_participant_dir(root, pid)
        if existing:
            return os.path.basename(existing)
    prefix = group_for_diagnosis(diagnosis)
    if prefix is None and root:
        prefix = group_for_diagnosis(diagnosis_from_metadata(root, pid))
    if prefix is None:
        for other in (REC_ROOT, OPTI_ROOT):
            if other != root:
                prefix = group_for_diagnosis(diagnosis_from_metadata(other, pid))
                if prefix:
                    break
    return "%s_%s" % (prefix or LEGACY_PREFIX, pid)


def participant_dir(root, pid, diagnosis=None, create=False):
    """Path to `pid`'s folder under `root`; created (with the right prefix) if asked."""
    existing = find_participant_dir(root, pid)
    if existing:
        if create:
            os.makedirs(existing, exist_ok=True)
        return existing
    path = os.path.join(root, participant_dir_name(pid, root, diagnosis))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def iter_participant_dirs(root):
    """Yield ``(pid, name, full_path)`` for every participant folder under `root`."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return
    for name in names:
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        pid = pid_from_dir_name(name)
        if pid is not None:
            yield pid, name, full


def participant_globs(root, *tail):
    """Glob patterns covering every prefix: ``participant_globs(REC_ROOT, "metadata.json")``."""
    return [os.path.join(root, "%s_*" % p, *tail) for p in ALL_PREFIXES]


def glob_participants(root, *tail, **kwargs):
    """Union of :func:`participant_globs` matches, de-duplicated and sorted."""
    recursive = kwargs.pop("recursive", False)
    if kwargs:
        raise TypeError("unexpected keyword arguments: %s" % ", ".join(sorted(kwargs)))
    hits = set()
    for pattern in participant_globs(root, *tail):
        hits.update(_glob.glob(pattern, recursive=recursive))
    return sorted(hits)


def participant_glob_for(root, pid, *tail, **kwargs):
    """Glob under a single participant, whichever prefix their folder uses."""
    recursive = kwargs.pop("recursive", False)
    if kwargs:
        raise TypeError("unexpected keyword arguments: %s" % ", ".join(sorted(kwargs)))
    base = find_participant_dir(root, pid)
    if not base:
        return []
    return sorted(_glob.glob(os.path.join(base, *tail), recursive=recursive))


def strip_group_prefix(pid):
    """``"MINT_25"`` -> ``("25", "MINT")``.  ``"25"`` -> ``("25", None)``.

    An id that already carries a study prefix, given to ``participant_dir``,
    produces a doubly-prefixed folder: ``MINT_MINT_25``. ``pid_from_dir_name``
    then returns ``"MINT_25"``, which is not a participant id, so the folder
    resolves to nobody and every discovery path skips it silently.

    That is not hypothetical. The 2026-09-08 session -- the only capture in this
    project where a clinician graded the same legs the instrument measured --
    landed in ``Recordings/MINT_MINT_25`` and was invisible to the whole
    pipeline until someone went looking by hand. Nothing downstream could have
    flagged it, because nothing downstream could see it.
    """
    text = str(pid).strip()
    for prefix in ALL_PREFIXES:
        head = prefix.rstrip("_")
        if text.upper().startswith(head.upper() + "_"):
            return text[len(head) + 1:].strip(), head
    return text, None


def check_participant_id(pid):
    """Return a warning string when `pid` would build an unreachable folder.

    Returns None when the id is fine. Deliberately a check rather than a silent
    repair: the operator typed something, and rewriting it without saying so
    would fix this session while leaving them doing the same thing next time.
    """
    bare, prefix = strip_group_prefix(pid)
    if prefix is None:
        return None
    if not bare:
        return (f"Participant ID {pid!r} is only a study prefix with no id "
                "after it.")
    return (
        f"Participant ID {pid!r} already carries the study prefix {prefix!r}. "
        f"The study prefix is added automatically from the diagnosis, so this "
        f"would create a folder named {prefix}_{pid} -- which resolves to no "
        f"participant and would be skipped by every analysis path. "
        f"Enter {bare!r} instead."
    )
