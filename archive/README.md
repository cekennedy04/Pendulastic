# archive/

Legacy code kept for reference only. **Nothing here is maintained, tested, or
run in CI.** It is not imported by anything outside this folder; if you need
something from it, copy it back out deliberately and bring it up to the current
quality bar (see `STATUS.md`).

Moved here on 2026-11-16 (STATUS workstream 8, owner decision 2026-11-10: archive,
do not develop). History is preserved (`git log --follow`).

| Path | What it was |
| --- | --- |
| `web/` | FastAPI + frontend for the video/HPE capture-and-review workflow (idle since August). `web/api/models/` was hidden by a bare `models/` entry in `.gitignore` and is committed here for the first time. |
| `mobile/` | Expo / React Native camera app. The webapp (`webapp/`) replaced it. |
| `Evaluate Models Py/` | One-off per-participant pose-model evaluation scripts. |
| `scripts/` | Root-level scripts with no importers and no CI test: video batch/review tools (`batch_pendulastic`, `browse_videos`, `reextract_all_mediapipe`, `run_hpe_analysis`, `run_hpe_inference`, `viewer_optitrack_comparison`, `run_p5_post_again`, `calibrate_phase_aware`), spikes and one-offs (`spike_wss_selfsigned`, `fix_tak_ole2`, `integration_test_task10`), the superseded benchmark PPTX builder (`gen_pptx`), and `evaluate_healthy_ref_v04` (superseded by `derive_healthy_ref.py`). |

## Running archived scripts

The scripts in `scripts/` were written to run from the repository root and import
sibling modules there. To run one anyway: `PYTHONPATH=. python archive/scripts/<name>.py`.
Several depend on frozen-in-place modules (`pendulastic_viewer.py`, `batch_mediapipe.py`,
...) and on model weights or recordings that are not in the repository.

Still in place and still frozen (see `STATUS.md`): `workbench_engine.py`,
`pendulastic_app.py`, `analysis_pipeline.py`, `batch_mediapipe.py`,
`pendulastic_viewer.py`.
