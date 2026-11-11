# Neuromech session: score the delivered motion from the delivered release (2026-11-11)

Branch `neuromech/session-release-alignment`, cut from `origin/main` at `e9a3a01`. Changes
`neuromech/session.py`, `neuromech/dryrun.py` (the ILC demo's scoring call and one note),
`neuromech/tests/test_session_dryrun.py` and `neuromech/README.md`. The scorer
(`pendulastic_pt_score.py`), the scenario definitions, the band thresholds, the Rust core and the C#
designer are untouched.

Everything under "Measured" was run in this worktree with the project `.venv` on a library built here
(`python -m neuromech.build`, 67 scenarios). It is all simulation: the dry run's plant, Player and phone
models. No rig and no phone were involved. "Inferred" is labelled.

## Summary

- `session.score` handed the scorer the command's release. It now hands it the release of the delivered
  motion, measured per run on the encoder trace and separately on the phone trace, records commanded
  release, delivered release and lag in `report.csv`, and flags (does not score) a trace whose release it
  cannot establish.
- **Measured, all 67 scenarios through the dry-run delivery, seed 11:** encoder N differs from the
  intended N in 41 scenarios before and 8 after; encoder band differs from the intended band in 5 before
  and 0 after. Phone: N 31 to 13, band 5 to 1. Seeds 3 and 42 give the same band result (5 to 0 on the
  encoder) and N 44 to 10 and 42 to 11.
- **Measured, default dry run:** 9 of 9 runs in band at seeds 1, 3, 7, 11 and 42 (7 of 9 at every seed
  before). `B-claspknife` and `M-catch_release_high` go from PT7 0.115-0.117 (band 0) to 0.127-0.129
  (band 1) against an intended 0.139.
- **The lag the scorer needs is not the tracking lag.** The cross-correlation lag of command and delivery
  over the first drop is 19.2 ms. Scored from there, the spurious extremum goes in only 19 of the 44
  scenarios that have it, and the two known misses come into band with 0.2 ms to spare. The release is
  therefore taken from the **onset**: the shift at which the delivered trace lies on the command's first
  2 degrees of fall, which is 30.4 ms after the true command (27.3 ms in the session's own time base).
  This is a design decision with a measured basis; section 1 gives it and section 5 gives what it leaves.
- **Three scenarios get a wrong N that was right before** (`C-N-high`, `C-wmin-high`, `C-area-high`), none
  moves PT7 by more than 0.002 or changes band. Five keep a wrong N that no release removes. Section 4.
- Not verified on hardware. The plausibility gates are judgements set on a simulated plant. Section 7.

## 1. Design

### What "the delivered release" has to mean

The scorer's count is knife-edged at the start of the record (2026-11-10 diagnosis; mechanism in
`origin/docs/oscillation-count-sensitivity`, 2026-11-11: the second peak pass searches a signal centred
on a boxcar padded with the release value, so each 10 ms of still samples after the given release adds
about 0.3 prominence thresholds to a start-of-record shoulder). What it needs from the session is the
sample at which the delivered trace starts to fall in the way the command starts to fall at its own
release.

Two candidates were measured on the 67 scenarios (seed 11, command through `dryrun.player_hold` and
`dryrun.plant_response` plus 0.02 deg noise; "command's N" is the command scored as a log, which equals
the manifest N in 67 of 67):

| release handed to the scorer | lag, median (min to max) | delivered N differs from the command's | band differs from intended |
|---|---|---|---|
| commanded release (before) | 0 | 44 | 5 |
| + cross-correlation lag over the first drop | 19.2 ms (16.5 to 35.5) | 25 | 3 |
| + onset lag, first 2 deg (chosen) | 30.4 ms (25.5 to 31.3; D-wobble 36.4) | 10 | 0 |

Three independent estimates of the whole-drop lag agree (correlation of angle 19.2 ms, of velocity
19.5 ms, equal-displacement at 10-50 % of the drop 19.5 ms), and it is what the models predict
(plant 2 zeta / wn = 11.5 ms plus about 6.5 ms of Player hold). It is the right number for "how late is
the swing". It is the wrong number for the scorer, for a reason that is physical and not a tuning
artefact: a closed loop rounds the corner at the release, so the arm trails the command by more while it
leaves the hold than over the drop as a whole. Measured as the time for the delivered trace to leave the
hold by x degrees, less the same for the command: 30.5 ms at 1 deg, 29.7 at 2, 28.2 at 3, 25.1 at 5,
20.6 at 10. Scored from the 19 ms point, the rounded corner is still inside the record.

The cross-correlation result is also fragile where it does work. Release shifts were scanned in 1 ms
steps: `B-claspknife` and `M-catch_release_high` need at least 19 ms and the cross-correlation gives
19.15-19.17 ms (margin 0.15-0.17 ms); `B-contracture`, `M-contracture_stop`, `C-wmax-low` and `C-f-low`
have margins of 0.3-0.6 ms. The 20 scenarios it does not fix need 21 to 30 ms. With the onset lag the two
known misses have 11 ms of margin.

A pure delay is a separate case and is handled exactly: the command delayed by 15 or 20 ms, with the
release moved by the measured lag, scores the command's N in 67 of 67 (28 and 30 of 67 differ when it is
scored from the commanded release). That count was taken with the cross-correlation estimate; on a pure
delay the onset estimate returns the same lag (error at most 0.008 ms over 67 scenarios and four delays),
and four scenarios are pinned through `session.score` in the tests. At 30 ms, 4 of 67 differed in that run
because the record was not extended past the command's end, which `session.score` now does.

### The method (`session._delivered_release`)

1. Take the command from 0.05 s before its release (`RELEASE_ONSET_PRE_S`) until it has moved 2 deg
   (`RELEASE_ONSET_DEG`).
2. For each lag from -60 to +130 ms in 0.5 ms steps, take the recorded trace over the same window shifted
   by that lag and compute the standard deviation of (recorded - command). The standard deviation removes
   any constant angle offset between the two. The lag is the minimum, refined by a parabola through its
   three nearest points.
3. Delivered release = commanded release + lag.

Nothing in it is a constant lag. The constants are window sizes and gates.

The scorer's 100 Hz grid is then laid out **from** the delivered release (`session._score_trace`), so the
release is a grid sample whatever its sub-sample value. `build.score_angle` rounds a release between
samples up to the next one; with the grid started at the hold, as before, a 1 ms change in release could
move the scored release by 10 ms. The scored span is unchanged: the same length of hold before the
release (3.05 s) and the same length after it as the command has. For the encoder the samples after the
command's end are taken from the rest of the log when it has them (the dry run's logs have 1 s), because
a late arm is still moving then.

### The phone, its 60 Hz sampling and its clock

The phone's release is measured by the same method on the phone's own trace, against the command, after
`_align_phone` has put the trace on the encoder clock. It is not copied from the encoder.

- **Clock offset.** The 2026-11-10 study found the phone trace 10.3-13.3 ms early after the sync-pulse
  alignment. Measured here on the dry run's phone (59 Hz, jitter, staircase delivery): phone lag minus
  encoder lag has median -7.9 ms, range -19.5 to -2.7 ms (seed 11, 67 runs; -17.1 to -1.6 at seed 3,
  -15.0 to -1.5 at seed 42). Because the phone's release is measured on the phone's trace, that offset
  moves the release with the trace and costs nothing; it is written out per run as
  `ph_minus_enc_lag_ms` so that it can be seen. The offset itself is not corrected: `_align_phone` is
  unchanged.
- **60 Hz sampling.** The fit is over every phone sample in the onset window, on a linearly interpolated
  trace, not a threshold crossing on one sample. Measured on pure delays of 0, 15, 20 and
  30 ms seen at 59 Hz with 1.5 ms jitter, a 2 % gain error and a 1.7 deg offset, all 67 scenarios: lag
  error median 0.13 ms, 95th percentile 0.79 ms, maximum 2.87 ms. At 1 kHz the maximum is 0.008 ms.
- **Cross-check.** Both sensors watched one arm, and the pulse alignment can legitimately be off by up to
  one phone sample. A phone lag more than 35 ms (two samples, `PHONE_SYNC_TOL_S`) from the encoder's is
  flagged as a sync fault.

### Failing loudly

A trace is **flagged** and given no score when any of these holds. There is no fallback to the commanded
release.

| condition | gate | reason for the value |
|---|---|---|
| lag outside the plausible range | -10 to +80 ms (`RELEASE_LAG_PLAUSIBLE_S`) | The dry run gives 22.5 to 34.3 ms. Below zero is acausal, with 10 ms allowed because ILC measures against the scenario and can bring the arm level with it. 80 ms is about three times the simulated value. A judgement, not a measurement of the rig. |
| onset does not match the command's | rms over the window above 0.3 deg (`RELEASE_RESID_MAX_DEG`) | 15 % of the 2 deg onset. Dry run, three seeds: median 0.03, D-wobble 0.20 (encoder) and 0.23 (phone), everything else at most 0.12 (encoder) and 0.14 (phone). |
| the command never moves 2 deg within 1 s of release | | no release to find |
| the recording does not cover the search window | | |
| phone more than 35 ms from the encoder | `PHONE_SYNC_TOL_S` | above |

The search range is wider than the plausible range so that an optimum at the edge of the search is
recognisably implausible. A flagged encoder trace sets the run's `status` to `release flagged: <reason>`,
leaves `enc_*` empty, sets `delivered_band_ok` false and skips the phone. A flagged phone trace sets
`phone_status` the same way and leaves `ph_*` empty. Whatever was measured (`cmd_release_s`,
`enc_release_s`, `enc_release_lag_ms`, `enc_release_resid_deg`, and the `ph_` equivalents) is still
written. The printed summary lists every flagged trace under "RELEASE FLAGGED" and says they are not in
the percentages above it, and `python -m neuromech.session score` exits with status 1.

### Sensitivity to the two window sizes

Same simplified delivery, seed 11, 67 scenarios. Lag statistics exclude D-wobble, which is given beside
them.

| pre (s) | onset (deg) | lag median (min to max), ms | D-wobble lag, ms | N differs from intended | band differs |
|---|---|---|---|---|---|
| 0.02 | 2 | 30.5 (25.4 to 32.5) | -47.5 | 8 | 0 |
| 0.03 | 2 | 30.5 (25.4 to 31.6) | 35.6 | 10 | 0 |
| **0.05** | 1 | 30.5 (25.4 to 32.6) | 42.6 | 10 | 0 |
| **0.05** | **2** | **30.4 (25.5 to 31.3)** | **36.4** | **10** | **0** |
| **0.05** | 3 | 29.5 (23.5 to 31.6) | 34.4 | 8 | 0 |
| **0.05** | 5 | 27.4 (20.5 to 30.9) | 32.9 | 14 | 0 |
| 0.10 | 2 | 30.4 (25.6 to 31.4) | 4.6 | 12 | 0 |
| 0.20 | 2 | 30.4 (26.4 to 31.5) | 15.8 | 12 | 0 |

The band result (0 of 67) held in all 20 combinations tried (pre 0.02 to 0.20 s, onset 1 to 5 deg), and
the median lag of the other 66 scenarios moves by 0.2 ms across pre-windows at a 2 deg onset. **D-wobble is the exception and is
ill-conditioned**: its command wobbles 3 deg at 2.5 Hz during the hold, the plant amplifies that, and the
wobble inside the pre-window outweighs the 2 deg onset. A 0.10 s pre-window, the first value tried, gave
its encoder trace a lag of 0.6 ms in the real dry run without flagging it (its phone trace was flagged,
at -13.4 ms). 0.05 s was chosen after seeing that. With
0.05 s it gets 32.6 to 34.3 ms over three seeds against 27 for the rest, and its N is the intended one, but
its residual (0.19-0.20 deg) is twice any other scenario's. Its release should be read as uncertain by
several ms.

### Time base

`enc_release_lag_ms` is measured against the command as `ilc.locate` places it in the log's setpoint
channel. In the dry run that channel is the Player's staircase, and `locate` puts the command 1 to 5 ms
(median 3.5) after its true start. That is why the session reports 27.3 ms where the simplified delivery
above reports 30.4 ms. The delivered release in log time is the same either way.

## 2. Tests (`neuromech/tests/test_session_dryrun.py`)

| test | asserts |
|---|---|
| `test_an_undelayed_command_scores_as_the_scenario` | a command read back as a log through `session.score` has the intended N and band (A-P0, B-claspknife, M-tone_L2, C-N-high) |
| `test_a_pure_delay_changes_neither_N_nor_the_band` (15, 20, 30 ms) | the same four, delivered 15 / 20 / 30 ms late through `session.score`: N and band equal the undelayed ones and PT7 is within 0.001 |
| `test_the_commanded_release_counts_a_late_delivery_as_an_extra_half_cycle` | B-claspknife 20 ms late gains 0.5 in N from the commanded release and not from its own: the artefact, and proof the test above can fail |
| `test_the_reported_lag_is_the_injected_lag` (0, 15, 20, 30 ms) | reported lag within 0.5 ms of the injected one; `enc_release_s - cmd_release_s` equals the lag |
| `test_the_lag_is_recovered_at_the_phones_sample_rate` | within 3.0 ms at 59 Hz with jitter, 2 % gain error and an offset (A-P0, B-claspknife, C-f-high; delays 0, 8, 19, 33 ms) |
| `test_an_implausible_lag_flags_the_run_and_does_not_score_it` (-30, 150 ms) | status `release flagged: lag ...`, no `enc_pt7` or `enc_band`, `delivered_band_ok` false, measured release still recorded |
| `test_score_exits_with_status_1_when_a_release_is_flagged` | the CLI's exit status |
| `test_a_delivery_that_is_not_the_command_is_flagged` | an arm that never left the hold, and one that went the wrong way |
| `test_a_phone_that_disagrees_with_the_encoder_is_flagged` | a phone release 38 ms from the encoder's is flagged as a sync fault, one 32 ms from it is not |
| `test_the_release_is_recorded_for_every_scored_trace` | the dry-run report carries the release columns for encoder and phone, they add up, and nothing is flagged |
| `test_default_session_delivers_every_band_and_the_edge_scenarios_stay_on_their_edge` | replaces `test_default_session_misses_exactly_the_two_edge_scenarios`: 9 of 9 in band at seed 3, the two catch runs have the intended N, and their PT7 is between 0.12 and 0.132 and at least 0.005 under the intended value |

The pinned test was rewritten, not deleted. It now fails if the two edge runs leave their band again and
also if they stop looking like edge runs.

## 3. Before and after, all 67 scenarios

`before` is `origin/main`'s `session.py` loaded in place of the new one; everything else is identical.
Both score the same synthetic logs and phone recordings: the 67 scenarios in manifest order in sessions
of eight, each planned and synthesised by `dryrun.run(sids, seed)`. (One session of 67 does not fit on
this machine's disk. The first, unchunked `before` run at seed 11 gave the same 62 of 67.) Intended values
are the manifest's. Bands are `session._summ`'s: "refused" when the scorer says not interpretable.

### Headline

| | seed 11 | seed 3 | seed 42 |
|---|---|---|---|
| encoder N differs from intended, before / after | 41 / 8 | 44 / 10 | 42 / 11 |
| encoder band differs from intended, before / after | 5 / 0 | 5 / 0 | 5 / 0 |
| scenarios whose encoder N changed | 39 | 38 | 37 |
| scenarios whose encoder band changed | 5 | 5 | 5 |
| phone N differs from intended, before / after | 31 / 13 | 35 / 15 | 32 / 13 |
| phone band differs from intended, before / after | 5 / 1 | 6 / 2 | 5 / 1 |
| scenarios whose phone N changed | 22 | 27 | 27 |
| scenarios whose phone band changed | 4 | 4 | 4 |
| encoder, median distance of PT7 from intended, before / after | 0.0066 / 0.0061 | 0.0067 / 0.0058 | 0.0070 / 0.0063 |
| traces flagged | 0 | 0 | 0 |

The five encoder band changes are the same at every seed and are the five flips of the diagnosis:
`B-claspknife`, `M-catch_release_high`, `R-friction` (0 to 1), `B-flexor` (1 to 1+), `D-short` (1+ to 2).
All five now match the intended band. The four phone band changes are the first four; the phone's
`D-short` stays in band 1 against an intended 2 with PT7 0.142 before and after (see section 4).

### Measured lag

| | seed 11 | seed 3 | seed 42 |
|---|---|---|---|
| encoder lag, ms: min / 5 % / 25 % / median / 75 % / 95 % / max | 22.6 / 24.6 / 26.4 / 27.3 / 27.4 / 28.4 / 34.3 | 22.6 / 24.7 / 26.4 / 27.4 / 27.6 / 28.5 / 32.6 | 22.5 / 24.6 / 26.5 / 27.4 / 27.5 / 28.4 / 33.4 |
| encoder lag, mean and sd | 26.8, 1.5 | 27.0, 1.6 | 27.0, 1.5 |
| phone lag, ms: min / median / max | 6.1 / 18.6 / 24.6 | 8.4 / 18.7 / 24.9 | 8.4 / 18.4 / 26.8 |
| phone - encoder, ms: min / median / max | -19.5 / -7.9 / -2.7 | -17.1 / -8.4 / -1.6 | -15.0 / -8.0 / -1.5 |
| onset residual, deg, encoder / phone: median, max | 0.03, 0.20 / 0.03, 0.22 | 0.03, 0.20 / 0.03, 0.22 | 0.03, 0.19 / 0.03, 0.23 |

The only encoder lag outside 22.5 to 28.6 ms at any seed is `D-wobble` (34.3, 32.6, 33.4). The low end is
the fast and high-velocity scenarios (`C-wmax-low`, `C-f-low`, `C-f-high`, 22.6 to 23.2).

### Per scenario, seed 11

Each cell is PT7 / N / band. Session order.

| scenario | intended | encoder before | encoder after | phone before | phone after | lag, encoder / phone (ms) |
|---|---|---|---|---|---|---|
| S-lam1 | 0.328 / 3.5 / 1+ | 0.302 / 4.0 / 1+ | 0.305 / 4.0 / 1+ | 0.306 / 4.0 / 1+ | 0.307 / 4.0 / 1+ | 28.3 / 20.6 |
| A-P1 | 0.192 / 5.0 / 1 | 0.171 / 5.5 / 1 | 0.185 / 5.0 / 1 | 0.171 / 5.5 / 1 | 0.185 / 5.0 / 1 | 27.3 / 20.0 |
| S-lam6 | 0.251 / 2.5 / refused | 0.238 / 3.0 / refused | 0.237 / 3.0 / refused | 0.240 / 3.0 / refused | 0.239 / 3.0 / refused | 27.5 / 20.0 |
| S-lam2 | 0.185 / 4.0 / 1 | 0.188 / 4.0 / 1 | 0.189 / 4.0 / 1 | 0.188 / 4.0 / 1 | 0.189 / 4.0 / 1 | 24.4 / 17.3 |
| S-lam1.5 | 0.189 / 4.5 / 1 | 0.183 / 4.5 / 1 | 0.183 / 4.5 / 1 | 0.182 / 4.5 / 1 | 0.181 / 4.5 / 1 | 26.5 / 12.5 |
| A-P1p | 0.365 / 2.5 / 1+ | 0.350 / 3.0 / 1+ | 0.350 / 3.0 / 1+ | 0.351 / 3.0 / 1+ | 0.351 / 3.0 / 1+ | 26.7 / 20.6 |
| A-P0 | 0.060 / 6.5 / 0 | 0.053 / 7.0 / 0 | 0.053 / 6.5 / 0 | 0.053 / 7.0 / 0 | 0.054 / 6.5 / 0 | 26.6 / 16.5 |
| S-lam3 | 0.251 / 3.0 / refused | 0.232 / 3.0 / refused | 0.235 / 3.0 / refused | 0.238 / 3.0 / refused | 0.238 / 3.0 / refused | 28.3 / 18.2 |
| B-claspknife | 0.139 / 6.0 / 1 | 0.116 / 6.5 / 0 | 0.128 / 6.0 / 1 | 0.116 / 6.5 / 0 | 0.128 / 6.0 / 1 | 27.4 / 19.6 |
| B-flexor | 0.292 / 3.0 / 1+ | 0.270 / 3.5 / 1 | 0.287 / 3.0 / 1+ | 0.271 / 3.5 / 1 | 0.288 / 3.0 / 1+ | 27.3 / 19.9 |
| B-contracture | 0.039 / 7.0 / 0 | 0.032 / 7.5 / 0 | 0.034 / 7.0 / 0 | 0.033 / 7.5 / 0 | 0.035 / 7.0 / 0 | 26.4 / 18.9 |
| B-leadpipe | 0.343 / 1.0 / 1+ | 0.337 / 1.0 / 1+ | 0.336 / 1.0 / 1+ | 0.338 / 1.0 / 1+ | 0.337 / 1.0 / 1+ | 27.4 / 19.1 |
| B-clonus | 0.080 / 5.5 / 0 | 0.075 / 5.5 / 0 | 0.075 / 5.5 / 0 | 0.075 / 5.5 / 0 | 0.076 / 5.5 / 0 | 27.4 / 12.8 |
| B-mixed | 0.365 / 2.5 / 1+ | 0.350 / 3.0 / 1+ | 0.350 / 3.0 / 1+ | 0.351 / 3.0 / 1+ | 0.351 / 3.0 / 1+ | 26.7 / 20.6 |
| B-extensor | 0.192 / 5.0 / 1 | 0.171 / 5.5 / 1 | 0.185 / 5.0 / 1 | 0.188 / 5.0 / 1 | 0.187 / 5.0 / 1 | 28.6 / 18.5 |
| B-cogwheel | 0.346 / 1.0 / 1+ | 0.337 / 1.0 / 1+ | 0.337 / 1.0 / 1+ | 0.338 / 1.0 / 1+ | 0.337 / 1.0 / 1+ | 27.4 / 16.9 |
| B-habit1 | 0.192 / 5.0 / 1 | 0.171 / 5.5 / 1 | 0.186 / 5.0 / 1 | 0.172 / 5.5 / 1 | 0.186 / 5.0 / 1 | 27.4 / 19.6 |
| B-guarding | 0.043 / 13.5 / 0 | 0.039 / 14.0 / 0 | 0.038 / 13.5 / 0 | 0.033 / 14.5 / 0 | 0.033 / 14.0 / 0 | 26.3 / 18.7 |
| B-habit5 | 0.159 / 5.5 / 1 | 0.137 / 6.0 / 1 | 0.151 / 5.5 / 1 | 0.138 / 6.0 / 1 | 0.153 / 5.5 / 1 | 27.4 / 19.9 |
| B-habit3 | 0.180 / 5.0 / 1 | 0.159 / 5.5 / 1 | 0.173 / 5.0 / 1 | 0.175 / 5.0 / 1 | 0.174 / 5.0 / 1 | 27.4 / 19.1 |
| B-habit2 | 0.185 / 5.0 / 1 | 0.165 / 5.5 / 1 | 0.179 / 5.0 / 1 | 0.179 / 5.0 / 1 | 0.179 / 5.0 / 1 | 27.4 / 12.8 |
| B-spasm | 0.195 / 6.0 / 1 | 0.188 / 6.0 / 1 | 0.189 / 6.0 / 1 | 0.190 / 6.0 / 1 | 0.189 / 6.0 / 1 | 26.7 / 20.6 |
| B-hypotonic | 0.037 / 12.5 / 0 | 0.034 / 13.0 / 0 | 0.034 / 12.5 / 0 | 0.048 / 13.5 / 0 | 0.049 / 13.0 / 0 | 25.6 / 15.4 |
| B-habit4 | 0.175 / 5.0 / 1 | 0.154 / 5.5 / 1 | 0.168 / 5.0 / 1 | 0.170 / 5.0 / 1 | 0.169 / 5.0 / 1 | 27.4 / 17.0 |
| C-N-high | 0.031 / 27.5 / 0 | 0.027 / 27.5 / 0 | 0.026 / 27.0 / 0 | 0.008 / 27.5 / 0 | 0.008 / 27.5 / 0 | 25.4 / 12.3 |
| C-R2n-high | 0.040 / 12.5 / 0 | 0.038 / 13.0 / 0 | 0.037 / 12.5 / 0 | 0.118 / 12.5 / 0 | 0.115 / 12.5 / 0 | 25.5 / 13.6 |
| C-wmax-low | 0.188 / 7.0 / 1 | 0.186 / 7.5 / 1 | 0.186 / 7.0 / 1 | 0.185 / 7.5 / 1 | 0.186 / 7.0 / 1 | 22.6 / 16.5 |
| C-phi-high | 0.055 / 8.0 / 0 | 0.052 / 8.0 / 0 | 0.052 / 8.0 / 0 | 0.053 / 8.0 / 0 | 0.052 / 8.0 / 0 | 25.4 / 17.9 |
| C-phi-low | 0.680 / 0.5 / 3 | 0.645 / 0.5 / 3 | 0.700 / 0.5 / 3 | 0.693 / 0.5 / 3 | 0.714 / 0.5 / 3 | 27.5 / 17.5 |
| C-N-zero | 1.458 / 0.0 / refused | 1.460 / 0.0 / refused | 1.442 / 0.0 / refused | 1.455 / 0.0 / refused | 1.445 / 0.0 / refused | 26.5 / 22.4 |
| C-R2n-low | 0.275 / 5.0 / 1 | 0.259 / 5.0 / 1 | 0.259 / 5.0 / 1 | 0.261 / 5.0 / 1 | 0.260 / 5.0 / 1 | 27.5 / 24.2 |
| C-wmax-high | 0.153 / 7.0 / 1 | 0.157 / 7.5 / 1 | 0.154 / 7.0 / 1 | 0.157 / 7.5 / 1 | 0.157 / 7.5 / 1 | 25.0 / 9.8 |
| C-area-high | 0.055 / 7.5 / 0 | 0.051 / 7.5 / 0 | 0.050 / 8.0 / 0 | 0.065 / 8.0 / 0 | 0.065 / 8.0 / 0 | 26.4 / 18.6 |
| C-f-low | 0.188 / 7.0 / 1 | 0.186 / 7.5 / 1 | 0.187 / 7.0 / 1 | 0.187 / 7.0 / 1 | 0.187 / 7.0 / 1 | 22.6 / 7.9 |
| D-push | 0.036 / 7.0 / 0 | 0.032 / 7.0 / 0 | 0.032 / 7.0 / 0 | 0.033 / 7.0 / 0 | 0.033 / 7.0 / 0 | 27.5 / 13.8 |
| C-A0-high | 0.042 / 7.0 / refused | 0.037 / 7.5 / refused | 0.038 / 7.0 / refused | 0.037 / 7.5 / refused | 0.038 / 7.0 / refused | 25.3 / 19.8 |
| C-A0-low | 0.039 / 6.5 / refused | 0.034 / 7.0 / refused | 0.035 / 6.5 / refused | 0.034 / 7.0 / refused | 0.036 / 6.5 / refused | 25.5 / 18.4 |
| C-f-high | 0.235 / 7.0 / 1 | 0.238 / 7.5 / 1 | 0.233 / 7.0 / 1 | 0.240 / 7.5 / 1 | 0.240 / 7.5 / 1 | 23.2 / 6.1 |
| C-wmin-high | 0.046 / 15.0 / 0 | 0.041 / 15.0 / 0 | 0.040 / 14.5 / 0 | 0.055 / 15.0 / 0 | 0.055 / 14.5 / 0 | 25.5 / 19.4 |
| C-A0-small-ok | 0.039 / 7.0 / refused | 0.034 / 7.5 / refused | 0.035 / 7.0 / refused | 0.035 / 7.5 / refused | 0.036 / 7.0 / refused | 25.6 / 19.3 |
| M-tone_L0 | 0.042 / 7.0 / 0 | 0.037 / 7.5 / 0 | 0.038 / 7.0 / 0 | 0.036 / 7.5 / 0 | 0.037 / 7.0 / 0 | 26.4 / 18.6 |
| D-short | 0.440 / 2.5 / 2 | 0.408 / 3.0 / 1+ | 0.446 / 2.5 / 2 | 0.142 / 3.0 / 1 | 0.142 / 3.0 / 1 | 26.4 / 12.7 |
| M-tone_L4 | 0.420 / 2.0 / 1+ | 0.416 / 2.0 / 1+ | 0.425 / 2.0 / 1+ | 0.417 / 2.0 / 1+ | 0.424 / 2.0 / 1+ | 27.6 / 22.5 |
| M-tone_L2 | 0.225 / 4.5 / 1 | 0.204 / 5.0 / 1 | 0.219 / 4.5 / 1 | 0.220 / 4.5 / 1 | 0.220 / 4.5 / 1 | 27.3 / 24.6 |
| M-tone_L1 | 0.114 / 5.5 / 0 | 0.093 / 6.0 / 0 | 0.107 / 5.5 / 0 | 0.093 / 6.0 / 0 | 0.094 / 6.0 / 0 | 27.4 / 18.5 |
| D-lowlift | 0.039 / 7.0 / refused | 0.035 / 7.5 / refused | 0.036 / 7.0 / refused | 0.033 / 7.5 / refused | 0.035 / 7.0 / refused | 25.6 / 15.3 |
| D-wobble | 0.042 / 7.0 / 0 | 0.039 / 7.5 / 0 | 0.037 / 7.0 / 0 | 0.038 / 7.0 / 0 | 0.037 / 7.0 / 0 | 34.3 / 14.8 |
| M-tone_L3 | 0.373 / 2.5 / 1+ | 0.369 / 2.5 / 1+ | 0.368 / 2.5 / 1+ | 0.369 / 2.5 / 1+ | 0.369 / 2.5 / 1+ | 28.4 / 17.6 |
| M-fall_no_ringdown | 0.343 / 1.0 / 1+ | 0.337 / 1.0 / 1+ | 0.336 / 1.0 / 1+ | 0.339 / 1.0 / 1+ | 0.338 / 1.0 / 1+ | 27.4 / 19.5 |
| M-catch_and_hold_140 | 1.672 / 0.0 / refused | 1.660 / 0.0 / refused | 1.662 / 0.0 / refused | 1.589 / 0.0 / refused | 1.586 / 0.0 / refused | 26.6 / 19.9 |
| R-anthro-72kg | 0.070 / 10.5 / 0 | 0.066 / 11.0 / 0 | 0.066 / 10.5 / 0 | 0.069 / 11.0 / 0 | 0.070 / 10.5 / 0 | 26.3 / 18.8 |
| R-friction | 0.137 / 3.5 / 1 | 0.115 / 4.0 / 0 | 0.129 / 3.5 / 1 | 0.116 / 4.0 / 0 | 0.131 / 3.5 / 1 | 26.4 / 18.1 |
| M-contracture_stop | 0.039 / 7.0 / 0 | 0.034 / 7.5 / 0 | 0.035 / 7.0 / 0 | 0.036 / 7.0 / 0 | 0.036 / 7.0 / 0 | 26.5 / 11.6 |
| M-catch_and_hold_127 | 1.670 / 0.0 / refused | 1.660 / 0.0 / refused | 1.661 / 0.0 / refused | 1.511 / 0.0 / refused | 1.504 / 0.0 / refused | 27.3 / 20.8 |
| M-catch_release_high | 0.139 / 6.0 / 1 | 0.116 / 6.5 / 0 | 0.128 / 6.0 / 1 | 0.118 / 6.5 / 0 | 0.130 / 6.0 / 1 | 27.6 / 17.4 |
| R-friction-heavy | 0.191 / 2.5 / 1 | 0.169 / 3.0 / 1 | 0.186 / 2.5 / 1 | 0.169 / 3.0 / 1 | 0.185 / 2.5 / 1 | 27.5 / 16.9 |
| R-reflex-len | 0.179 / 14.5 / 1 | 0.172 / 15.0 / 1 | 0.170 / 14.5 / 1 | 0.179 / 14.5 / 1 | 0.179 / 14.5 / 1 | 27.3 / 19.6 |
| R-reflex-force | 0.328 / 3.5 / 1+ | 0.302 / 4.0 / 1+ | 0.303 / 4.0 / 1+ | 0.303 / 4.0 / 1+ | 0.306 / 4.0 / 1+ | 26.7 / 19.9 |
| H-HM3 | 0.291 / 2.5 / 1+ | 0.285 / 2.5 / 1+ | 0.288 / 2.5 / 1+ | 0.285 / 2.5 / 1+ | 0.289 / 2.5 / 1+ | 28.4 / 20.9 |
| H-HM1 | 0.244 / 4.5 / 1 | 0.222 / 5.0 / 1 | 0.238 / 4.5 / 1 | 0.239 / 4.5 / 1 | 0.240 / 4.5 / 1 | 27.4 / 19.1 |
| R-reflex-sat | 1.574 / 0.0 / refused | 1.558 / 0.0 / refused | 1.554 / 0.0 / refused | 1.506 / 0.0 / refused | 1.504 / 0.0 / refused | 28.3 / 13.7 |
| R-reflex-vel | 0.339 / 3.0 / 1+ | 0.328 / 3.0 / 1+ | 0.328 / 3.0 / 1+ | 0.330 / 3.0 / 1+ | 0.329 / 3.0 / 1+ | 26.6 / 20.5 |
| R-anthro-heavy | 0.069 / 11.0 / 0 | 0.068 / 11.5 / 0 | 0.068 / 11.0 / 0 | 0.071 / 11.5 / 0 | 0.072 / 11.0 / 0 | 26.5 / 16.4 |
| H-HM2 | 1.556 / 0.0 / refused | 1.544 / 0.0 / refused | 1.538 / 0.0 / refused | 1.570 / 0.0 / refused | 1.568 / 0.0 / refused | 27.5 / 16.9 |
| K-HM1 | 0.175 / 5.5 / 1 | 0.154 / 6.0 / 1 | 0.169 / 5.5 / 1 | 0.155 / 6.0 / 1 | 0.169 / 5.5 / 1 | 27.4 / 19.6 |
| H-HM4 | 0.225 / 3.5 / 1 | 0.218 / 3.5 / 1 | 0.218 / 3.5 / 1 | 0.220 / 3.5 / 1 | 0.219 / 3.5 / 1 | 27.3 / 19.7 |
| K-HM2 | 0.327 / 3.0 / 1+ | 0.320 / 3.0 / 1+ | 0.319 / 3.0 / 1+ | 0.321 / 3.0 / 1+ | 0.321 / 3.0 / 1+ | 27.4 / 19.9 |

## 4. What got worse, and what did not improve

**Measured, encoder.**

- **N right before and wrong after: 3 at seed 11** (`C-N-high` 27.5 to 27.0, `C-wmin-high` 15.0 to 14.5,
  `C-area-high` 7.5 to 8.0), the first two at seed 3, all three at seed 42. PT7 moves by 0.002 or less on
  each and no band changes; N above 6.5 is not penalised. For `C-N-high` and `C-wmin-high` the intended N
  itself contains the start-of-record extremum: their own command, scored with its release moved 4 ms
  later, loses the same half-cycle (27.5 to 27.0, 15.0 to 14.5), and stays there out to 60 ms.
  `C-area-high`'s command gains a half-cycle when its release is moved 24 ms or more. So in these three
  the delivered score no longer matches a manifest value that is itself on the knife edge.
- **N wrong before and after: 5 at seed 11** (`S-lam1`, `S-lam6`, `A-P1p`, `B-mixed`, `R-reflex-force`),
  each 0.5 above intended. For four of them no release shift from 0 to 60 ms gives the command's N: the
  extra extremum is not a release artefact. `S-lam6` needs 30 ms or more in the simplified delivery and
  gets less. Seeds 3 and 42 add `B-habit4`, `C-R2n-low`, `C-area-high`, `C-wmax-high` and `C-f-high` in
  various combinations; `C-R2n-low` and `B-habit4` change N with the 10 ms phase of the scoring grid.
- **PT7 further from intended by more than 0.005: 2 per seed**, all in scenarios the scorer refuses
  (`C-N-zero` by 0.007 to 0.015, `H-HM2` by 0.006) except `M-tone_L4` at seed 3 (0.0051, still 1+).
- **Largest PT7 distance from intended** is 0.035 before (`C-phi-low`) and 0.024 after (`R-reflex-force`)
  at seed 11. At seeds 3 and 42 it is `C-R2n-low`, 0.088 before and 0.089 after: delivered about 0.187
  against an intended 0.275, band 1 either way (at seed 11 it delivers 0.259). That scenario's score
  moves with the 10 ms phase of the scoring grid, and this change does not improve it.
- **No scenario newly fails**: none is flagged, none is newly refused, none newly leaves its band.
- **Edge scenarios remain edge scenarios.** `B-claspknife` / `M-catch_release_high` deliver 0.128 against
  an edge at 0.12. `D-short` intends 0.4404 against an edge at 0.44 and delivers 0.446. `R-friction`
  delivers 0.129. Being in band is not margin.

**Measured, phone.**

- N right before and wrong after: `C-wmin-high` (all three seeds) and `C-f-high` (seed 42).
- `D-short`'s phone score is 0.142 (band 1) against the encoder's 0.446, before and after. `C-R2n-high`'s
  phone band is wrong at seed 3 before and after. `C-N-high` reads 0.008 on the phone against 0.026.
  These are not release effects and were not investigated. One difference between the two traces that
  this change leaves in place: the phone trace is scored to the end of the phone recording, the encoder
  trace to the end of the command's span.
- PT7 further from intended by more than 0.005: `C-phi-low` (0.021, still band 3) and refused scenarios.

**Default dry run** (`python -m neuromech.dryrun --seed k`, nine runs), runs in band, encoder / phone:

| seed | before | after | B-claspknife encoder PT7 before / after | M-catch_release_high before / after |
|---|---|---|---|---|
| 1 | 7 / 8 | 9 / 9 | 0.116 / 0.128 | 0.115 / 0.127 |
| 3 | 7 / 7 | 9 / 9 | 0.115 / 0.128 | 0.116 / 0.128 |
| 7 | 7 / 7 | 9 / 9 | 0.116 / 0.128 | 0.117 / 0.129 |
| 11 | 7 / 8 | 9 / 9 | 0.115 / 0.127 | 0.116 / 0.128 |
| 42 | 7 / 8 | 9 / 9 | 0.115 / 0.127 | 0.116 / 0.128 |

Seed 11 after: median tracking 0.73 deg, phone against encoder |dPT7| median 0.001 and maximum 0.005,
encoder lag median 27.4 ms (22.7 to 28.4), phone minus encoder median -8.3 ms (-14.6 to -7.1), plant
identified at 6.34 Hz, zeta 0.234.

## 5. What this change does not settle

- **Inferred.** The remaining undershoot of the catch scenario (0.139 intended, 0.128 delivered) is the
  plant's: the diagnosis attributed 0.0070 to peak velocity and 0.0036 to three smaller terms, which sums
  to 0.0106 against the 0.011 seen here. It was not re-decomposed after this change.
- **The knife edge is still in the scorer.** This change stops the session from stepping on it in the way
  the diagnosis found. It does not make N robust: 8 to 11 of 67 delivered N still differ from intended,
  and which ones depends on the seed.
- **Interaction with the scorer remedy under consideration** (count from the first trough,
  `origin/docs/oscillation-count-sensitivity`). Inferred, not run: with that remedy a peak before the
  first counted trough is not counted, so the start-of-record half-cycle disappears whatever release the
  session gives, and the N part of this change's effect would be reproduced by the scorer alone. The two
  do not conflict. This change would still matter for what else is measured from the release, which that
  report says remains sensitive (`area_ratio` integrates from it), and for the audit columns and flags.
  Expect three things if it lands: the manifest N of `C-N-high` and `C-wmin-high` should drop by 0.5 and
  then agree with what is delivered here; every intended value, and the pinned bounds in
  `test_default_session_delivers_every_band_and_the_edge_scenarios_stay_on_their_edge`, must be
  re-baselined; and the choice between the 19 ms and 30 ms definitions of the release would stop
  mattering for N and should be revisited on `area_ratio` alone.
- **The edge flag** the diagnosis recommended alongside this change (mark runs whose intended PT7 is
  within 0.03 of a band edge) is not in this branch. It was not in the brief.
- **The phone clock offset** is absorbed and reported, not fixed.

## 6. Gates

All run in this worktree on the final tree, Windows, project `.venv`.

| gate | result |
|---|---|
| `python -m pytest neuromech/tests -q` | **133 passed** in 154 s, 0 failed, 0 skipped |
| `python neuromech/designer/parity/run_parity.py --regen` | **PARITY OK**: 355 cases, every field within tolerance, 0 mismatches |
| `node neuromech/labview/verify_formula_node.js` | **PASS** on every vector (max difference 1.66e-11) |
| CI python list, `tests/test_web_export_contract.py` + the twelve `tests/` files of `.github/workflows/ci.yml` | **536 passed, 5 skipped, 3 xfailed** in 88 s |
| CI python list, `neuromech/tests` | the first row |

Two earlier runs of `pytest neuromech/tests` failed and are not hidden: one on
`test_a_phone_that_disagrees_with_the_encoder_is_flagged`, which still assumed a 25 ms tolerance after it
was set to 35 ms (the test was corrected), and two on `test_csharp_port_matches_python_scorer`, because
`run_parity.py` was started by hand while pytest was writing the same `parity/cases` folder and left it
half written. `--regen` rebuilt the cases and the test then passed inside the full run. The suite took
about three minutes here, not an hour.

The Rust and webapp CI jobs were not run: no file they build or test is changed.

## 7. Not verified

- **Rig hardware is not available. Nothing here was run on the rig, on a real LabVIEW log, or on a real
  phone recording.** Every lag, margin and band above comes from `dryrun`'s plant (second order, 6.35 Hz,
  zeta 0.23), Player and phone models.
- Whether the real loop rounds the release corner as the model does. The 19 ms against 30 ms distinction
  depends on it, and so does the size of the onset lag.
- The plausibility gates (-10 to +80 ms, 0.3 deg, 35 ms) against real data. If the real rig's onset lag
  is outside them every run will be flagged and unscored. That is the intended behaviour, but the first
  rig session should be expected to test the gates, not just the scenarios.
- A real phone's clock offset and sample timing. The -19.5 to -1.5 ms range is the simulated phone's.
- A log that stops exactly when the command ends. The encoder trace is then scored on a span shorter by
  the lag, with no warning. The dry run's logs all have 1 s of post-roll.
- `ilc_demo` after the change beyond `test_ilc_converges_through_the_real_tools`, which checks tracking
  RMSE and not the delivered score. The ILC table of the 2026-11-10 diagnosis was not re-run.
- A session-level consistency check on the lag (it is a rig property, so a run far from the session's
  median is suspect) was considered and not built. The summary prints the range instead.
- The figures in `neuromech/README.md` section 6 that date from 2026-10-26 and are marked there as not
  re-measured.
- The sensitivity table in section 1 and the 1 ms release scans use a simplified delivery (no `.bin`
  round trip, no `ilc.locate`, no phone); the before/after tables use the real dry-run path.
- `nm_figs.fig_dryrun` was not regenerated.

## Reproduce

```
python -m neuromech.build                        # 67 scenarios, about 11 minutes
python -m neuromech.dryrun --seed 11             # 9 of 9
python -m pytest neuromech/tests/test_session_dryrun.py -q
```

The all-67 before/after harness loads `origin/main`'s `neuromech/session.py` under the name
`neuromech.session`, runs `dryrun.run` on the manifest's scenarios eight at a time, and compares the two
`report.csv` sets. It is not committed.
