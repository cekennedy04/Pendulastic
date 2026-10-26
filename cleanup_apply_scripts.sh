#!/usr/bin/env bash
# One-off: remove the throwaway probes / orphan spikes, finish the sweep_p16 merge, prune the vulture whitelist.
# Run from the repo root:   bash cleanup_apply_scripts.sh [--with-plots]
# --with-plots additionally removes 4 legacy plot scripts (see bottom). Delete this script afterwards.
set -euo pipefail
cd "$(dirname "$0")"
PY=./.venv/Scripts/python.exe
WITH_PLOTS=0; [ "${1:-}" = "--with-plots" ] && WITH_PLOTS=1

git tag -f archive/pre-script-consolidation HEAD

# 1) Approved deletions (13): throwaway probes, orphan spikes, merged-away sweep_p16.
DEL=(
  probe_active_window_cap.py probe_native_vs_tick.py probe_omega_estimator.py probe_omega_truth.py
  probe_optical_noise.py probe_optical_noise_and_N.py probe_preamble_sensitivity.py
  probe_settle_window.py probe_smoothing_accuracy.py ab_requalify.py
  spike_metrabs_rmse.py evaluate_flex_axis_methods.py
  sweep_p16_preprocessing.py
)
# 2) Optional: legacy plots. Two belong to the removed pose-model bake-off; two hardcode P5 folder
#    names, which CLAUDE.md forbids. Kept: plot_multisource_trials.py (has tests).
if [ "$WITH_PLOTS" = 1 ]; then
  DEL+=(plot_3component_optitrack_vs_hpe.py plot_model_vs_optitrack_comparison.py
        plot_comprehensive_parameter_matrix.py plot_extension_hypertonia_comparison.py)
fi
git rm -q -- "${DEL[@]}"
git add sweep_mediapipe_preprocessing.py   # now has --participant (replaces sweep_p16_preprocessing.py)
git commit -q -m "chore: remove throwaway probes and orphan spikes; fold sweep_p16 into sweep_mediapipe_preprocessing --participant

Recoverable via tag archive/pre-script-consolidation.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git log -1 --oneline

# 3) Prune .vulture_whitelist.py entries whose file is no longer tracked
#    (vendored models/ code, removed scripts). Header/non-entry lines are kept.
cat > .prune_whitelist.tmp.py <<'PYEOF'
import re, subprocess
tracked = set(subprocess.check_output(["git", "ls-files"], text=True).splitlines())
keep, dropped = [], 0
for line in open(".vulture_whitelist.py", encoding="utf-8"):
    m = re.search(r"\((.+?):\d+\)\s*$", line)
    if m and m.group(1).replace(chr(92), "/") not in tracked:
        dropped += 1
        continue
    keep.append(line)
open(".vulture_whitelist.py", "w", encoding="utf-8", newline="").writelines(keep)
print("whitelist: dropped", dropped, "entries, kept", len(keep), "lines")
PYEOF
$PY .prune_whitelist.tmp.py
rm .prune_whitelist.tmp.py
git add .vulture_whitelist.py
git commit -q -m "chore: prune vulture whitelist of entries for vendored/removed files

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git log -1 --oneline

# 4) Sanity: everything left still compiles
$PY -m compileall -q $(git ls-files '*.py' | grep -v / ) > /dev/null && echo "compile OK"
