# Does pre-release material corrupt the scores?

> **RETRACTION, 2026-09-11.** Every number in this report derived from
> `bench_dryrun.py` is **invalid** and has been struck through below. That
> probe's synthesised phone log does not reproduce an angle it was given: the
> replayed angle reads ~175 deg during the pre-release hold for *every*
> commanded amplitude (90, 70, 50, 25, 15), carrying no amplitude information,
> while real corpus trials through the identical code path score sensibly.
> The "sync pulses" headline was wrong on two counts -- release detection
> actually lands in the **ramp**, not the pulses, and the amplitude figures
> that appeared to demonstrate it were arithmetic on a signal with no
> amplitude in it.
>
> `bench_dryrun.check_synthesis()` now gates the probe: it refuses to print
> any accuracy figure until a known hold angle round-trips. Ruled out as
> causes: accelerometer units (the AHRS normalises accel -- g vs m/s^2 is
> identical to 2 dp), role assignment (real trials are solo `proximal` too),
> the concurrently staged replay changes (old and new agree to 0.04 deg), and
> the calibration config (unchanged since 2026-08-17). The synthesis defect
> itself is unfixed.
>
> **What survives** is everything measured on the real corpus, which never
> touched the probe: the corpus exposure figures, the trim A/B, and the ICC
> table. Those sections are marked as standing.

**Answer: not in the corpus. The candidate "fix" was measured and rejected.
The bench-side story in this report is retracted pending a working oracle.**

The bench dry-run *appeared* to show A0 moving across 11.08 / 80.90 / 63.31 /
49.12 on a single identical 90 deg swing when only the preamble length changed.
Those four numbers are among the retracted ones -- but the question they raised
was the right question, and it was worth answering on real data: participant recordings also carry a
variable pre-release hold, and A0's test-retest is poor (ICC 0.29,
MDC95 29.0 deg on a mean of 54.8). If the same mechanism reached the corpus it
would be an accuracy lever, not a bench detail.

It does not. The mechanism is different from the one first proposed, the
corpus is almost entirely unexposed, and trimming makes reliability worse.

## The first explanation was wrong

The initial reading was that `_active_oscillation_window_end` measures its 10 s
cap from the start of the series (`pendulastic_pt_score.py:2468`,
`t_r[0] + _ACTIVE_WINDOW_CAP_SEC`), so a long preamble would eat the window.

`compute_pt_params` already slices at release before that point --
`t_r = t_c[rel_i:]` (line 2816) -- so `t_r[0]` *is* release and the cap is
measured from release, exactly as its docstring claims. The window is not
being eaten by raw series length.

## What actually happens ~~(RETRACTED)~~

~~**The sync pulses fool the scorer's own release detection.**~~

**RETRACTED.** Measured directly afterwards, `_detect_release` lands in the
**ramp**, not in the pulses, for a0 = 90 and 50 whether or not the pulses are
present. And the table below came from the broken probe, so it demonstrates
nothing either way. The one durable observation in this section is that a
displacement detector with a 0.6 s baseline and an 8%-of-range threshold will
fire on ANY pre-release motion -- ramp, pulse, or an examiner's handling --
which is a real fragility worth knowing about, but is not evidenced by what
follows.

Original text, struck:

> **The sync pulses fool the scorer's own release detection.** A 5 deg pulse
edge in one 1 ms sample is 5000 deg/s and clears any velocity threshold
instantly, so `rel_i` lands on a pulse instead of on the swing, and everything
downstream is measured from the wrong instant.

Same swing, same preamble length, only the pulses differ:

| `RAMP_S` | with pulses | pulses removed |
|---|---|---|
| 3.0 | 80.90 | 88.91 |
| 4.0 | 63.31 | 88.96 |
| 5.0 | 49.12 | 89.55 |

True amplitude is 90 deg. With the pulses removed the recovery is stable and
accurate regardless of ramp length; with them present it degrades as the gap
between the pulses and the real release grows.

The same trap caught the release detector written for this investigation: its
first version locked onto the pulse at 0.200 s instead of the release at
9.190 s, an 8990 ms error. Requiring the departure to be *sustained* for
100 ms fixed it (max error 150 ms against a known release), and that is also
what distinguishes a real release from an examiner's handling twitch.

## The corpus is not exposed  *(STANDS -- measured on real trials)*

Participant recordings carry no sync pulses, and their pre-release material is
short relative to the window:

| | seconds |
|---|---|
| median | 2.67 |
| p90 | 3.69 |
| max | 10.95 |
| trials leaving < 5 s of swing | 4 / 147 |
| trials leaving < 1 s of swing | 2 / 147 |

The length effect is detectable but weak: Spearman(preamble, \|dA0\|) = +0.166,
p = 0.049, n = 142. Median \|dA0\| rises only from 10.17 deg to 13.95 deg
across preamble bins from under 2 s to over 4 s.

Two trials are genuinely exposed and behave exactly as the mechanism predicts:

- `14_left_pre_T2` -- 10.95 s of preamble, longer than the whole cap.
  Production scores A0 = 60.90; trimmed it is 7.90.
- `23_left_pre_T1` -- 9.71 s. Production cannot score it at all; trimmed it
  gives A0 = 72.54.

These two are worth flagging in trial quality. They are not evidence of a
systemic problem.

## Trimming was tested and rejected  *(STANDS -- measured on real trials)*

Trimming every trial to release (keeping 0.5 s of lead) is not a no-op --
A0 moves by a median of 12.69 deg and 123 of 142 trials move by more than
10%. But it makes test-retest **worse** on 7 of 8 parameters:

| parameter | ICC full | ICC trimmed | delta |
|---|---|---|---|
| R2n | 0.322 | 0.301 | -0.021 |
| N | 0.779 | 0.697 | -0.081 |
| phi_max_ratio | 0.674 | 0.499 | **-0.175** |
| omega_max_n | 0.137 | 0.338 | +0.201 |
| omega_min_n | -0.010 | -0.025 | -0.016 |
| f | 0.187 | 0.179 | -0.007 |
| area_ratio | 0.463 | 0.430 | -0.034 |
| A0_deg | 0.292 | 0.251 | -0.041 |

A0's MDC95 worsens from 29.8 to 33.6 deg. `omega_max_n` is the lone riser and
is not a real gain: its MDC95 also worsens (5.68 to 8.19), so its ICC rose
because between-group variance rose, not because repeats agreed better.

The reason is that the pre-release hold is *informative* -- it feeds `neutral`
and the baseline drift estimate. Cutting it to 0.5 s starves both. This is the
same lesson as the drift-fallback and seed-window defects: the hold is load
bearing, and code that assumes it away gets punished.

## What this means for the bench ~~(RETRACTED)~~

**RETRACTED in full.** The cut-point table below was produced by the broken
probe; its percentages are not measurements. The bench's ground truth is the
ENCODER, and the accurate path is to stop guessing release altogether:
`compute_pt_params` already accepts a `release_idx` argument, and the bench
knows release exactly from `motor_reference.release_offset_s()`. That removes
`_detect_release` from the bench path entirely, which is the fix this section
should have reached.

Original text, struck:

The bench must remove the sync pulses from the phone series before scoring.
Where to cut is not obvious, and no cut point is good everywhere. Against a
known commanded amplitude:

| cut point | a0 >= 40 | a0 < 40 |
|---|---|---|
| end of the sync pulses (keeps the ramp) | **-1.1% to -3.9%** | returns None |
| start of the pre-release hold | -8% to -13% | -23% to -45%, amplitude-dependent |
| exactly at release | -33% | -33%, uniform |

Cutting at the pulses is much the most accurate where it works. Below about
40 deg it fails outright: `rel_i` falls back to the start of the series, where
the arm sits at zero and `neutral` is also near zero, so `phi[0]` is under the
3 deg floor at `pendulastic_pt_score.py:2845` and the function returns None.

Cutting exactly at release -- which the bench can do exactly, via
`motor_reference.release_offset_s()` -- is uniformly biased rather than
variably biased, which is the better failure mode for a calibration exercise
even though the bias is larger.

This is a simulation result. It says where to look on bench day; it does not
substitute for the bench. The one firm conclusion is that scoring the raw
captured series, pulses included, would have produced amplitude errors of tens
of percent that no one would have caught from the numbers alone.

## Provenance

`probe_preamble_sensitivity.py` (untracked, per this repo's `probe_*`
convention), 171 trials discovered, 147 scored by the production path.
ICC(1,1) formula copied verbatim from `test_retest_reliability.py` so the
numbers stay comparable with what has already been reported for this corpus.
