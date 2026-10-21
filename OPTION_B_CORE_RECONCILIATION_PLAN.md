# Option B — Core reconciliation plan (the full shipping build)

**Goal:** one branch that ships **both** the validated core (angle + scorer) **and**
the full restyle UI (settle [done in Option A], colors, research recorder,
workbench). Right now no single branch has both.

## The two lineages (why this is needed)
| | `feat/rust-abs-flex-projection` (@ 9a555de) | `feat/webapp-workbench-restyle` (@ 33450c5) |
|---|---|---|
| **Angle** | ✅ flex-axis projection — **bit-identical to the corpus-validated Python abs on 34/34 bench trials** | ❌ total-rotation magnitude (yaw-drift bug) + stale stillness bug |
| **Scorer** | ✅ corpus-validated | ❌ regresses A0/N/area_ratio on the corpus (matched test, this session) |
| **Settle** | ✅ (Option A) | ✅ |
| **UI** | ❌ old/minimal (no colors, no research recorder, no workbench) | ✅ full (router/views, colors, research recorder, countdown) |

The two `mobile-imu-core` lineages diverge across scorer, angle, `flex_axis`,
`signal.rs`, `resample.rs`, and the **`wasm.rs` API** (≈1225/1155 lines, 11 files).

## Decisions already settled by evidence (do not re-litigate)
- **Angle:** use the abs-port's flex-axis projection. NOT total-magnitude, NOT the
  signed variant (signed regresses corpus test-retest — see
  `project-abs-extraction-corpus-unsafe` memory).
- **Scorer:** keep the abs-port's (corpus-validated). NOT restyle's.
- **UI:** take restyle's webapp.
- ⇒ target = **restyle's webapp + abs-port's `mobile-imu-core`.**

## The hard part: the wasm API
restyle's webapp calls a wasm API that differs from the abs-port core's `wasm.rs`
(≈161-line diff), so you can't just drop the abs-port core under restyle's webapp.
Two paths — measure both diffs first, pick the smaller:

1. **Bring restyle's webapp onto the abs-port branch** (keeps the validated core
   untouched; adapt the JS↔wasm calls where restyle's views expect API the
   abs-port `wasm.rs` doesn't have). Likely cleaner — the core is the validated,
   don't-touch part.
2. **Apply the abs-port core changes onto restyle's core** (flex projection +
   stillness fix + gyro-SNR + the corpus-safe scorer, replacing restyle's
   total-magnitude angle and regressing scorer). Keeps restyle's webapp untouched
   but means disentangling restyle's divergent core — higher risk of
   re-introducing its scorer regression.

## Validation gates — ALL must pass before shipping
1. **Angle bit-identical:** `scratchpad/rust_vs_py_angle.py` (Rust `score_jsonl --dump`
   vs Python `replay_trial`, matched beta) → median ~0.00° on the bench trials.
   Proves the shipping core still matches the validated reference.
2. **Corpus not-worse:** `test_retest_reliability.py` from the shipping branch vs the
   abs-parent baseline (a904cac) → no regression on the 7 Popović params. Keeps the
   scorer corpus-safe.
3. **Tests:** `cargo test --release --lib` (Rust) + `node --test` (webapp) all green.
4. **On-device smoke:** capture a swing → confirm angle/score look right, the settle
   countdown runs and auto-stops, colors show, research recorder (`?research=1`) works.

## Concrete steps
1. Measure `git diff --stat` both directions (webapp and mobile-imu-core) to pick Path 1 vs 2.
2. New branch off the chosen base; do the port.
3. Rebuild wasm + dist; run all tests (gate 3).
4. Run gates 1 and 2.
5. `npx vercel deploy --yes --project pendulastic-app dist` (PREVIEW); on-device smoke (gate 4).
6. Promote to prod only after the smoke test and explicit go.

## Coordination / cleanup
- A concurrent session is on **restyle** (and committed the **signed** angle on
  `fix/swing-angle-signed-abs-fold`, which **regresses the corpus** — hold/revert it,
  it's superseded by the abs port). Coordinate so the reconciliation doesn't clash.
- Relevant memory: `project-scorer-branch-topology`, `project-abs-extraction-corpus-unsafe`.

## Branch/artifact map
- `feat/rust-abs-flex-projection` @ `9a555de` — **the shipping core base** (validated
  angle+scorer + settle). Commits: `f429356` (flex port), `e6e5969` (stillness fix),
  `9a555de` (settle).
- `feat/webapp-workbench-restyle` @ `33450c5` — the UI to bring over.
- Current preview (validated core + settle, old UI): the link from Option A.
