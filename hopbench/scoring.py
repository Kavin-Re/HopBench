"""Turns raw per-trial measurements into a 0-100 resilience scorecard.

Per attacker (see README 'Scoring' for the full derivation):

    adv   = log2(hit / chance) / log2(1 / chance)        in [0, 1]; hit = max(chance, mean hit) unless
            the 99 % bootstrap CI over trials excludes chance (then hit = the measured mean)
    adv_score  = 1 - adv
    obs_score  = mean over trials of 1 if the attacker never produced `streak` correct hops in a row,
                 else clip(log10(obs_to_break) / log10(slots), 0, 1)               (predictors only)
    tput_score = 1 - clip((T_ref - T) / (T_ref - (1 - p_jam)), 0, 1), T_ref = throughput under an
                 ideal-random schedule, T = measured; blended 50/50 mean / worst-decile over trials (jammers)
    resist = 0.6 * adv_score + 0.4 * (obs_score | tput_score)

Axes (weights sum to 100) and the weakest-link gate are in AXES / GATE below.
"""
from __future__ import annotations

import math

import numpy as np

from .attackers import ATTACKERS
from .randtests import ALPHA, TEST_NAMES
from .stats import bootstrap_ci

# axis key -> (label, weight, [attacker keys], how to combine members)
AXES = {
    "state_recovery": ("State recovery", 20, ["lfsr_breaker", "lcg_recovery", "xorshift_breaker"], "min"),
    "ngram": ("n-gram / Markov", 15, ["ngram"], "mean"),
    "linear_complexity": ("Linear complexity (BM)", 10, ["berlekamp_massey"], "mean"),
    "frequency_bias": ("Frequency bias", 10, ["frequency_bias"], "mean"),
    "reactive": ("Reactive jammer", 8, ["reactive_delay"], "mean"),
    "follower": ("Follower jammer", 7, ["follower"], "mean"),
    "sweep_random_band": ("Sweep / random / partial-band", 10, ["sweep", "random", "partial_band"], "mean"),
    "randomness": ("Randomness battery", 20, [], "n/a"),
}
GATE_AXES = ["state_recovery", "ngram", "linear_complexity", "frequency_bias"]   # predictors: weakest link
GATE_FLOOR = 0.2     # final = weighted_mean * (GATE_FLOOR + (1 - GATE_FLOOR) * min(gate axes))
W_ADV, W_SECOND = 0.6, 0.4
assert sum(a[1] for a in AXES.values()) == 100


def adv_score(hits, chance, level=0.99):
    mean = float(np.mean(hits))
    lo, hi = bootstrap_ci(hits, level)
    eff = mean if lo > chance else chance          # benefit of the doubt when indistinguishable from chance
    eff = min(max(eff, chance), 1.0)
    adv = math.log2(eff / chance) / math.log2(1 / chance) if chance < 1 else 0.0
    return 1.0 - min(max(adv, 0.0), 1.0), dict(hit_mean=mean, hit_lo=lo, hit_hi=hi, chance=chance,
                                               significant=bool(lo > chance), advantage_bits=math.log2(max(eff, chance) / chance))


def throughput_ref(k, M, p_jam, p_clean):
    return 1 - (k / M) * p_jam - (1 - k / M) * p_clean


def score_attacker(akey, raw, cfg):
    cls = ATTACKERS[akey]
    M = cfg["M"]
    chance = cls.k_eff / M
    # PartialBand sets k_eff on the instance; mirror it here
    if akey == "partial_band":
        chance = 4 / M
    s_adv, info = adv_score(raw["hit"], chance)
    out = dict(info)
    out["kind"] = cls.kind
    out["tput_mean"] = float(np.mean(raw["tput"]))
    out["tput_p10"] = float(np.quantile(raw["tput"], 0.1))
    out["tput_ci"] = bootstrap_ci(raw["tput"], 0.99)
    out["adv_score"] = s_adv
    if cls.kind == "predictor":
        ob = raw["obs_break"]
        per = [1.0 if o is None else min(max(math.log10(max(o, 1)) / math.log10(cfg["slots"]), 0.0), 1.0) for o in ob]
        broken = [o for o in ob if o is not None]
        out["broken_fraction"] = len(broken) / len(ob)
        out["obs_break_median"] = float(np.median(broken)) if broken else None
        out["obs_break_min"] = int(min(broken)) if broken else None
        out["obs_break_max"] = int(max(broken)) if broken else None
        second = float(np.mean(per))
        out["obs_score"] = second
    else:
        tref = throughput_ref(cls.k_eff if akey != "partial_band" else 4, M, cfg["p_jam"], cfg["p_clean"])
        floor = 1 - cfg["p_jam"]
        per = [1 - min(max((tref - t) / (tref - floor), 0.0), 1.0) for t in raw["tput"]]
        second = 0.5 * float(np.mean(per)) + 0.5 * float(np.quantile(per, 0.1))
        out["tput_ref"] = tref
        out["tput_score"] = second
    out["resist"] = W_ADV * s_adv + W_SECOND * second
    return out


def randomness_score(rnd):
    fails, total = 0, 0
    per_test = {}
    for t in TEST_NAMES:
        ps = np.asarray(rnd["p"][t])
        f = int((ps < ALPHA).sum())
        per_test[t] = dict(fail_fraction=f / len(ps), min_p=float(ps.min()), median_p=float(np.median(ps)))
        fails += f
        total += len(ps)
    return 1.0 - fails / total, per_test


def scorecard(result_for_schedule, cfg):
    att = {k: score_attacker(k, v, cfg) for k, v in result_for_schedule["attackers"].items()}
    rscore, per_test = randomness_score(result_for_schedule["randomness"])
    axes = {}
    for ak, (label, w, members, how) in AXES.items():
        if ak == "randomness":
            axes[ak] = rscore
            continue
        vals = [att[m]["resist"] for m in members if m in att]
        axes[ak] = (min(vals) if how == "min" else float(np.mean(vals))) if vals else float("nan")
    ok = {k: v for k, v in axes.items() if not math.isnan(v)}
    wsum = sum(AXES[k][1] for k in ok)
    weighted = sum(AXES[k][1] * v for k, v in ok.items()) / wsum
    gate_vals = [axes[g] for g in GATE_AXES if g in ok]
    gate = min(gate_vals) if gate_vals else 1.0
    factor = GATE_FLOOR + (1 - GATE_FLOOR) * gate
    final = 100.0 * weighted * factor
    ent = result_for_schedule["randomness"]
    return dict(score=final, weighted_mean=weighted, gate=gate, gate_factor=factor, axes=axes,
                attackers=att, randomness_tests=per_test,
                shannon_bits=float(np.mean(ent["shannon"])), min_entropy_bits=float(np.mean(ent["minentropy"])))


def verdict(score):
    if score >= 90:
        return "no weakness found by this suite"
    if score >= 70:
        return "minor weakness"
    if score >= 40:
        return "exploitable"
    return "broken"
