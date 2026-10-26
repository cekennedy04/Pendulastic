"""Scenario library for the knee-spasticity emulator.

A scenario = model parameters (neuromech.model_rt.Params) + release conditions +
optional KINEMATIC transforms applied to the simulated trace. The transforms are
exact, model-free ways to push ONE parameter out of range while holding the rest:

  time_scale k   t -> t/k        f and both omega terms scale by k; R2n, N, phi, area unchanged
  amp_scale  a   theta_rel -> a*theta_rel   A0 scales; every A0-normalised ratio unchanged
  tail_creep     slow drift of the resting angle after settle -> moves neutral_deg,
                 the exact pathway that saturates area_ratio on the phone (2026-09-08)
  hold_wobble    oscillation during the pre-release hold -> release detection stress
  truncate_s     recording stops this long after release

Groups:
  A  PT7-band presets P0-P3: the published severity axis calibrated to the centre of each
                      PT7 band. NOT MAS settings (plan D5): calibrated to the scorer's own bands,
                      right for phone-vs-encoder testing, circular for validating grades
  M  mechanism profiles   neuromech.profiles.PROFILES (severity AND kind of severity).
                      Was rig_neuromuscular_model.PROFILES, rendered by that module (plan D1);
                      B9 ported its mechanisms into model_rt, so these now render from the
                      validated core and that module is retired
  R  rig / mechanism   the four mechanisms B9 ported: bearing friction, a named person's
                      anthropometry, a tonic arrest-and-hold, and the velocity vs length vs
                      force reflex channels
  S  severity axis     the published model's tone+reflex path, incl. the inverting branch
  B  behaviours        flexor/extensor dominance, catch, clonus, rigidity, contracture,
                       hypotonia, guarding, spasm, habituation
  C  out-of-range      each of the 7 parameters (and the A0 gate) pushed past its bounds
  D  artefacts         push release, low release, pre-release wobble, short recording
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from neuromech.model_rt import Params, catch_and_hold, limb_from_anthropometry, rig_params


@dataclass(frozen=True)
class Scenario:
    sid: str
    group: str
    title: str
    params: Params = field(default_factory=Params)
    theta0_deg: float = 0.0          # release angle, model convention (0 = full extension)
    omega0: float = 0.0              # rad/s at release (a push), negative = into flexion
    duration: float = 14.0
    time_scale: float = 1.0
    amp_scale: float = 1.0
    tail_creep_dps: float = 0.0      # deg/s drift of rest, starting tail_creep_t after release
    tail_creep_t: float = 6.0
    hold_wobble_deg: float = 0.0
    truncate_s: float | None = None
    probes: str = ""                 # what this scenario is designed to exercise
    source: str = "model_rt"         # "model_rt" or "prof:<name>" (neuromech.profiles)
    intent: int | None = None        # intended severity level, where the source defines one


def severity(lam: float, **kw) -> Params:
    """The published model's severity axis: tone, force-feedback and derivative gain scaled together
    (He et al. 1997: no single reflex parameter spans the severity range; De Groote et al. 2018 nominal = 1)."""
    return Params(tone=2.0 * lam, k_f=0.75 * lam, k_df=0.025 * lam, **kw)


#: PT7 targets: the centre of each MAS band in pendulastic_pt_score._MAS (<= .12/.28/.44/.60/.78).
MAS_TARGETS = {"0": 0.06, "1": 0.20, "1+": 0.36, "2": 0.52, "3": 0.69}


def build(mas_lambda: dict[str, float]) -> list[Scenario]:
    """mas_lambda: calibrated severity per MAS grade (from build.calibrate_ladder)."""
    L = mas_lambda
    # Bands the scorer cannot reach are ABSENT from this dict rather than
    # present with a made-up lambda (build.calibrate_ladder), so the A group is
    # only as long as the axis actually supports and the B/C/D groups fall back
    # to whichever mid rung exists.
    _mid = sorted(L.values())[len(L) // 2] if L else 0.5
    m1p, m2 = severity(L.get("1+", _mid)), severity(L.get("2", _mid))
    sc: list[Scenario] = []
    # ── A: PT7-band presets ───────────────────────────────────────────────
    for g in ("0", "1", "1+", "2", "3"):
        if g not in L:
            continue
        sc.append(Scenario(f"A-P{g.replace('+', 'p')}", "A", f"PT7-band preset {g} (PT7 {MAS_TARGETS[g]}, lambda {L[g]:.3f})",
                           severity(L[g]), probes=f"phone vs encoder at a programmed PT7 of {MAS_TARGETS[g]}"))
    # ── S: severity axis incl. the inverting branch ───────────────────────
    for lam in (1.0, 1.5, 2.0, 3.0, 6.0):
        sc.append(Scenario(f"S-lam{lam:g}", "S", f"Published severity axis, lambda {lam:g}", severity(lam),
                           probes="severe leg that the composite may grade milder or refuse (U-shape)"))
    # ── B: behaviours ─────────────────────────────────────────────────────
    sc += [
        Scenario("B-extensor", "B", "Extensor-dominant (quadriceps reflex only)", m2, probes="area_ratio direction, phi_max"),
        Scenario("B-flexor", "B", "Flexor-dominant (hamstring reflex on the return)",
                 Params(tone=0.4, flex_gain=2.5, flex_vth=0.8), probes="phi_max low, area_ratio sign"),
        Scenario("B-mixed", "B", "Co-spastic: extensor + flexor reflexes", replace(m1p, flex_gain=2.0, flex_vth=0.8),
                 probes="both halves damped"),
        Scenario("B-claspknife", "B", "Clasp-knife catch then release on the first drop",
                 Params(catch_gain=4.0, catch_vth=1.2, catch_release_deg=25.0), probes="R2n, omega_min_n, A1 shape"),
        Scenario("B-clonus", "B", "Clonus: 6 Hz beats after the first drop", replace(m1p, clonus_deg=4.0, clonus_hz=6.0),
                 probes="N inflation by beats, f contamination, 6 Hz at the phone's ~60 Hz rate"),
        Scenario("B-leadpipe", "B", "Lead-pipe rigidity (velocity-independent)", Params(rigid_nm=0.9),
                 probes="linear (not exponential) decay, early stop off-vertical"),
        Scenario("B-cogwheel", "B", "Cogwheel rigidity (5 Hz ratchet)", Params(rigid_nm=0.9, cog_depth=0.8, cog_hz=5.0),
                 probes="ratchet ripple on the swing"),
        Scenario("B-contracture", "B", "Contracture: flexion stopped 25 deg past rest",
                 Params(contract_k=25.0, contract_deg=-80.0), probes="asymmetric swing, higher f, area_ratio"),
        Scenario("B-hypotonic", "B", "Hypotonic / flaccid (low damping)", Params(B=0.03), probes="N high, R2n ceiling"),
        Scenario("B-guarding", "B", "Non-relaxation: voluntary guarding torque", Params(guard_nm=0.8, guard_seed=3),
                 probes="irregular period; the relaxation check the literature requires"),
        Scenario("B-spasm", "B", "Mid-swing extensor spasm at 1.5 s", replace(m1p, spasm_nm=6.0, spasm_t=1.5),
                 probes="period variation, phi_max, N"),
    ]
    for k, gsc in enumerate((1.0, 0.85, 0.72, 0.62, 0.55), start=1):
        sc.append(Scenario(f"B-habit{k}", "B", f"Habituation series, drop {k} (gain x{gsc:g})", replace(m2, gain_scale=gsc),
                           probes="trial-to-trial drift in repeated drops"))
    # ── C: out-of-range, one parameter at a time ──────────────────────────
    base = Params()
    sc += [
        Scenario("C-R2n-low", "C", "R2n low: strong first-drop catch", Params(catch_gain=8.0, catch_vth=0.8, catch_release_deg=40.0), probes="R2n"),
        Scenario("C-R2n-high", "C", "R2n at its ceiling: undamped swing", Params(B=0.0), probes="R2n -> 1.25, N"),
        Scenario("C-N-zero", "C", "N = 0: rigid leg, no oscillation", Params(rigid_nm=3.5), probes="N, refusal path"),
        Scenario("C-N-high", "C", "N very high: almost no damping, long record", Params(B=0.01), duration=30.0, probes="N count window"),
        Scenario("C-phi-low", "C", "phi_max ~ 0: no return to extension", Params(flex_gain=6.0, flex_vth=0.3), probes="phi_max_ratio"),
        Scenario("C-phi-high", "C", "phi_max > 1: energy injected on the return", replace(base, spasm_nm=12.0, spasm_t=0.7, spasm_w=0.25),
                 probes="phi_max_ratio > 1 is physically implausible for a passive leg"),
        Scenario("C-wmax-high", "C", "omega_max high: same swing, 2x faster", base, time_scale=2.0, probes="omega_max_n, f"),
        Scenario("C-wmax-low", "C", "omega_max low: same swing, 2x slower", base, time_scale=0.5, probes="omega_max_n, f"),
        Scenario("C-wmin-high", "C", "omega_min high: velocity never stops at reversals", Params(guard_nm=1.6, guard_seed=7, B=0.1),
                 probes="omega_min_n"),
        Scenario("C-f-low", "C", "f low: 0.46 Hz", base, time_scale=0.5, probes="f"),
        Scenario("C-f-high", "C", "f above the 2.0 Hz plausibility flag (2.3 Hz)", base, time_scale=2.5, probes="f_out_of_range flag"),
        Scenario("C-area-high", "C", "area_ratio high: rest creeps after settling", base, tail_creep_dps=3.0, tail_creep_t=6.0,
                 probes="neutral_deg -> area_ratio; the phone-drift failure, with encoder truth"),
        Scenario("C-A0-low", "C", "A0 below the 25 deg gate", base, theta0_deg=-37.0, probes="INSUFFICIENT_EXCURSION refusal"),
        Scenario("C-A0-high", "C", "A0 above the 120 deg gate (kinematic only)", base, amp_scale=2.3,
                 probes="IMPOSSIBLE_EXCURSION refusal; not physically renderable on a leg"),
        Scenario("C-A0-small-ok", "C", "A0 just above the gate (28 deg)", base, theta0_deg=-27.0, probes="behaviour at the gate"),
    ]
    # ── D: release / operator artefacts ───────────────────────────────────
    sc += [
        Scenario("D-push", "D", "Pushed, not dropped (-1.5 rad/s at release)", base, omega0=-1.5, probes="A0 vs drop, R2n inflation"),
        Scenario("D-wobble", "D", "Pre-release wobble (3 deg, 2.5 Hz during hold)", base, hold_wobble_deg=3.0, probes="release detection"),
        Scenario("D-short", "D", "Recording stopped 3 s after release", base, truncate_s=3.0, probes="settle / N on a cut record"),
        Scenario("D-lowlift", "D", "Released from 30 deg short of full extension", base, theta0_deg=-30.0, probes="A0 and ratios on a partial lift"),
    ]
    # ── M: mechanism profiles ─────────────────────────────────────────────
    # Source changed 2026-10-26 (B9). These were rendered by
    # rig_neuromuscular_model, a second physics core kept alongside this one
    # under plan D1 "until ported into the core". B9 ported its mechanisms into
    # model_rt and the Formula Node, so the profiles now render from the
    # validated core on the same code path as every other scenario -- and the
    # rig plays one model, not two that have to be kept in agreement by hand.
    from neuromech import profiles as PR
    # rig_passive is the same Params as R-friction below, which carries the
    # identification probe; rendering both would put two byte-identical 1 kHz
    # setpoint files in the run sheet. It stays in PROFILES because
    # severity_grading_test iterates that dict and wants the hardware baseline.
    for name, prm in PR.PROFILES.items():
        if name == "rig_passive":
            continue
        it = PR.SEVERITY_INTENT.get(name)
        sc.append(Scenario(f"M-{name}", "M", f"{name} (intent {it if it is not None else 'n/a'})",
                           prm, source=f"prof:{name}", intent=it,
                           probes=PR.PROBES.get(name, "")))
    # ── R: the four mechanisms B9 ported, each exercised on its own ────────
    _anth = dict(zip(("lc_m", "m_kg", "j_kgm2"), limb_from_anthropometry(72.0, 1.75)))
    sc += [
        Scenario("R-friction", "R", "The rig's own dry bearing friction, no pathology", rig_params(),
                 probes="N and R2n on the real hardware: Coulomb decay is arithmetic where viscous "
                        "decay is geometric, so this is the trace that identifies coulomb_nm"),
        Scenario("R-friction-heavy", "R", "Bearing friction at 0.4 N.m (a tight or cold bearing)",
                 rig_params(coulomb_nm=0.4),
                 probes="how much of a healthy-looking N deficit the rig itself can manufacture"),
        Scenario("R-anthro-72kg", "R", "Winter anthropometry, 72 kg / 1.75 m, no pathology", Params(**_anth),
                 probes="f against anthropometric_frequency_hz; the mass-free period prediction"),
        Scenario("R-anthro-heavy", "R", "Same height, 95 kg: identical period, heavier limb",
                 Params(**dict(zip(("lc_m", "m_kg", "j_kgm2"), limb_from_anthropometry(95.0, 1.75)))),
                 probes="f must match R-anthro-72kg -- mass cancels in m*g*lc/J; only torque differs"),
        Scenario("R-reflex-force", "R", "Extensor reflex: FORCE feedback only (the published channel)",
                 Params(tone=2.0, k_f=0.75, k_df=0.025),
                 probes="first-swing vs second-swing ordering; De Groote's own discriminator"),
        Scenario("R-reflex-vel", "R", "Extensor reflex: VELOCITY feedback only, same tone",
                 Params(tone=2.0, k_v=3.0, k_v_thr=0.5),
                 probes="the classical alternative; must not invert the swing ordering where force does"),
        Scenario("R-reflex-len", "R", "Extensor reflex: LENGTH feedback only, same tone",
                 Params(tone=2.0, k_l=3.0, k_l_thr_deg=5.0),
                 probes="tonic stretch drive: resistance grows with excursion, not with speed"),
        Scenario("R-reflex-sat", "R", "Velocity feedback driven hard into a 4 N.m ceiling",
                 Params(tone=2.0, k_v=8.0, k_v_thr=0.2, reflex_max_nm=4.0),
                 probes="saturation: a muscle cannot produce unbounded force, and an unbounded "
                        "force-feedback loop above gain 1 is a numerical artefact, not a patient"),
    ]
    return sc
