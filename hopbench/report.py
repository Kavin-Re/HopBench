"""Writers: results.md, leaderboard.csv, detail.csv, scorecards.json, radar plots (SVG always, PNG if matplotlib)."""
from __future__ import annotations

import csv
import json
import math
import os

from .attackers import ATTACKERS
from .randtests import TEST_NAMES
from .scoring import AXES, GATE_AXES, GATE_FLOOR, W_ADV, W_SECOND, verdict

AXIS_ORDER = list(AXES)
SHORT = {"state_recovery": "State\nrecovery", "ngram": "n-gram", "linear_complexity": "Linear\ncomplexity",
         "frequency_bias": "Freq.\nbias", "reactive": "Reactive", "follower": "Follower",
         "sweep_random_band": "Sweep/rand/\nband", "randomness": "Randomness"}


def _rank(cards, builtin):
    names = sorted(builtin, key=lambda n: -cards[n]["score"])
    return {n: i + 1 for i, n in enumerate(names)}


# ----------------------------------------------------------------------------- CSV / JSON
def write_leaderboard(path, cards, builtin, controls=()):
    rank = _rank(cards, builtin)
    cols = ["rank", "schedule", "kind", "score", "verdict", "weighted_mean_pct", "gate_min_predictor_axis_pct"] + \
           [f"axis_{a}_pct" for a in AXIS_ORDER] + ["shannon_bits_per_hop", "min_entropy_bits_per_hop"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for n in list(builtin) + list(controls):
            c = cards[n]
            w.writerow([rank.get(n, ""), n, "builtin" if n in builtin else "control", f"{c['score']:.1f}", verdict(c["score"]),
                        f"{100 * c['weighted_mean']:.1f}", f"{100 * c['gate']:.1f}"] +
                       [f"{100 * c['axes'][a]:.1f}" for a in AXIS_ORDER] + [f"{c['shannon_bits']:.3f}", f"{c['min_entropy_bits']:.3f}"])


def write_detail(path, cards):
    cols = ["schedule", "attacker", "kind", "hit_rate", "hit_ci_lo", "hit_ci_hi", "chance", "significant", "advantage_bits",
            "obs_break_median", "broken_fraction", "throughput_mean", "throughput_p10", "adv_score", "second_score", "resist"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for sn, c in cards.items():
            for ak, a in c["attackers"].items():
                w.writerow([sn, ak, a["kind"], f"{a['hit_mean']:.4f}", f"{a['hit_lo']:.4f}", f"{a['hit_hi']:.4f}", f"{a['chance']:.4f}",
                            a["significant"], f"{a['advantage_bits']:.3f}",
                            "" if a.get("obs_break_median") is None else f"{a['obs_break_median']:.0f}",
                            f"{a.get('broken_fraction', ''):.2f}" if a["kind"] == "predictor" else "",
                            f"{a['tput_mean']:.4f}", f"{a['tput_p10']:.4f}", f"{a['adv_score']:.3f}",
                            f"{a.get('obs_score', a.get('tput_score')):.3f}", f"{a['resist']:.3f}"])


def write_json(path, cards, meta):
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        return o
    with open(path, "w") as f:
        json.dump(clean({"meta": meta, "scorecards": cards}), f, indent=1, default=float)


# ----------------------------------------------------------------------------- radar (pure SVG)
def radar_svg(path, cards, names, cols=3, cell=300, title="HopBench resilience radar (axis score 0-100)"):
    n = len(AXIS_ORDER)
    rows = math.ceil(len(names) / cols)
    W, H = cols * cell, rows * cell + 50
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="{W / 2}" y="26" text-anchor="middle" font-size="16" font-weight="bold">{title}</text>']
    R = cell * 0.30
    for idx, name in enumerate(names):
        cx = (idx % cols) * cell + cell / 2
        cy = (idx // cols) * cell + 50 + cell / 2 - 5
        c = cards[name]
        for ring in (0.25, 0.5, 0.75, 1.0):
            pts = " ".join(f"{cx + ring * R * math.sin(2 * math.pi * i / n):.1f},{cy - ring * R * math.cos(2 * math.pi * i / n):.1f}" for i in range(n))
            out.append(f'<polygon points="{pts}" fill="none" stroke="#d0d4da" stroke-width="{1.2 if ring == 1.0 else 0.7}"/>')
        for i, a in enumerate(AXIS_ORDER):
            x, y = cx + R * math.sin(2 * math.pi * i / n), cy - R * math.cos(2 * math.pi * i / n)
            out.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d0d4da" stroke-width="0.7"/>')
            lx, ly = cx + (R + 16) * math.sin(2 * math.pi * i / n), cy - (R + 16) * math.cos(2 * math.pi * i / n)
            anchor = "middle" if abs(math.sin(2 * math.pi * i / n)) < 0.3 else ("start" if math.sin(2 * math.pi * i / n) > 0 else "end")
            two_lines = 5 if "\n" in SHORT[a] else 0   # hoisted: a backslash inside an f-string expression needs Python 3.12
            for j, line in enumerate(SHORT[a].split("\n")):
                out.append(f'<text x="{lx:.1f}" y="{ly + 3 + 10 * j - two_lines:.1f}" text-anchor="{anchor}" font-size="9" fill="#444">{line}</text>')
        vals = [max(0.0, min(1.0, c["axes"][a])) for a in AXIS_ORDER]
        pts = " ".join(f"{cx + v * R * math.sin(2 * math.pi * i / n):.1f},{cy - v * R * math.cos(2 * math.pi * i / n):.1f}" for i, v in enumerate(vals))
        col = "#1b7f3b" if c["score"] >= 70 else ("#c77700" if c["score"] >= 40 else "#c0262d")
        out.append(f'<polygon points="{pts}" fill="{col}" fill-opacity="0.28" stroke="{col}" stroke-width="2"/>')
        out.append(f'<text x="{cx:.1f}" y="{cy - R - 36:.1f}" text-anchor="middle" font-size="13" font-weight="bold">{name}</text>')
        out.append(f'<text x="{cx:.1f}" y="{cy - R - 21:.1f}" text-anchor="middle" font-size="12" fill="{col}">score {c["score"]:.1f}</text>')
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))


def radar_png(path, cards, names, cols=3):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    import numpy as np
    rows = math.ceil(len(names) / cols)
    fig, axs = plt.subplots(rows, cols, subplot_kw=dict(polar=True), figsize=(4.2 * cols, 4.2 * rows))
    axs = np.atleast_1d(axs).ravel()
    ang = np.linspace(0, 2 * np.pi, len(AXIS_ORDER), endpoint=False)
    for ax, name in zip(axs, names):
        c = cards[name]
        v = [max(0, min(1, c["axes"][a])) * 100 for a in AXIS_ORDER]
        col = "#1b7f3b" if c["score"] >= 70 else ("#c77700" if c["score"] >= 40 else "#c0262d")
        ax.plot(list(ang) + [ang[0]], v + [v[0]], color=col, lw=2)
        ax.fill(list(ang) + [ang[0]], v + [v[0]], color=col, alpha=0.25)
        ax.set_theta_offset(np.pi / 2)
        ax.set_theta_direction(-1)
        ax.set_xticks(ang)
        ax.set_xticklabels([SHORT[a].replace("\n", " ") for a in AXIS_ORDER], fontsize=6.5)
        ax.set_ylim(0, 100)
        ax.set_yticks([25, 50, 75, 100])
        ax.set_yticklabels([])
        ax.set_title(f"{name}\nscore {c['score']:.1f}", fontsize=10, color=col, pad=16)
    for ax in axs[len(names):]:
        ax.axis("off")
    fig.suptitle("HopBench resilience radar (axis score 0-100)", fontsize=13)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


# ----------------------------------------------------------------------------- results.md
def _pct(x):
    return f"{100 * x:.1f}"


def write_markdown(path, cards, builtin, controls, meta, radar_files):
    cfg = meta["config"]
    rank = _rank(cards, builtin)
    L = []
    A = L.append
    A("# HopBench results\n")
    A("Simulation and education only: no RF, no third-party systems. Every schedule and attacker below is a generic scheme implemented in this repository.")
    A("**These scores are relative to this attacker suite. They are not a security proof.** See the caveats at the bottom.\n")
    A(f"Run: M = {cfg['M']} channels, {cfg['trials']} independent secrets per schedule, {cfg['slots']} slots per attack run "
      f"(first {cfg['warmup']} slots = learning window, excluded from hit rate and throughput), randomness stream {cfg['rand_len']} hops, "
      f"p_jam = {cfg['p_jam']}, p_clean = {cfg['p_clean']}, seeds {cfg['seed0']}..{cfg['seed0'] + cfg['trials'] - 1}. Wall time {meta['seconds']:.0f} s.\n")
    A("## Leaderboard (built-in schedules)\n")
    hdr = ["#", "Schedule", "Score", "Verdict"] + [AXES[a][0] for a in AXIS_ORDER]
    A("| " + " | ".join(hdr) + " |")
    A("|" + "|".join(["---"] * 4 + ["---:"] * len(AXIS_ORDER)) + "|")
    for n in sorted(builtin, key=lambda n: rank[n]):
        c = cards[n]
        A(f"| {rank[n]} | {n} | **{c['score']:.1f}** | {verdict(c['score'])} | " + " | ".join(_pct(c['axes'][a]) for a in AXIS_ORDER) + " |")
    A("\nCalibration controls (not ranked): they bound the scale. The constant-channel control should land near 0 and the ideal i.i.d. uniform control near 100.\n")
    A("| Control | Score | " + " | ".join(AXES[a][0] for a in AXIS_ORDER) + " |")
    A("|---|---:|" + "|".join(["---:"] * len(AXIS_ORDER)) + "|")
    for n in controls:
        c = cards[n]
        A(f"| {n} | {c['score']:.1f} | " + " | ".join(_pct(c['axes'][a]) for a in AXIS_ORDER) + " |")
    A("")
    for rf in radar_files:
        A(f"![radar]({os.path.basename(rf)})\n" if rf.endswith(".svg") else "")
    A("Axis values are 0-100 (100 = nothing found). The score is the weighted mean of the axes (weights below) multiplied by a weakest-link factor, so one cheap, complete break cannot be averaged away.\n")

    allnames = list(builtin) + list(controls)
    pred_keys = [k for k, c in ATTACKERS.items() if c.kind == "predictor"]
    jam_keys = [k for k, c in ATTACKERS.items() if c.kind == "jammer"]

    A("## Prediction accuracy vs chance (steady state, 99 % bootstrap CI over trials)\n")
    A("Hit rate = fraction of slots where the attacker's guess equals the actual channel, after the learning window. Chance for a single guess is 1/M = "
      f"{100 / cfg['M']:.1f} %. Entries in **bold** have a CI that excludes chance.\n")
    A("| Schedule | " + " | ".join(ATTACKERS[k].name for k in pred_keys) + " |")
    A("|---|" + "|".join(["---:"] * len(pred_keys)) + "|")
    for n in allnames:
        row = []
        for k in pred_keys:
            a = cards[n]["attackers"][k]
            s = f"{_pct(a['hit_mean'])} [{_pct(a['hit_lo'])}, {_pct(a['hit_hi'])}]"
            row.append(f"**{s}**" if a["significant"] else s)
        A(f"| {n} | " + " | ".join(row) + " |")

    A("\n## Hops observed before the attacker is right "
      f"{cfg['streak']} times in a row (chance of that by luck: {cfg['M']}^-{cfg['streak']})\n")
    A("Median over the trials where it happened, with the fraction of trials broken within "
      f"{cfg['slots']} slots in brackets. `-` = never.\n")
    A("| Schedule | " + " | ".join(ATTACKERS[k].name for k in pred_keys) + " |")
    A("|---|" + "|".join(["---:"] * len(pred_keys)) + "|")
    for n in allnames:
        row = []
        for k in pred_keys:
            a = cards[n]["attackers"][k]
            row.append("-" if a["obs_break_median"] is None else f"{a['obs_break_median']:.0f} ({100 * a['broken_fraction']:.0f}%)")
        A(f"| {n} | " + " | ".join(row) + " |")

    A("\n## Throughput under each attacker\n")
    A(f"Fraction of packets delivered after the learning window, mean over trials (worst-decile trial in brackets). One packet per slot; a packet on a jammed channel is lost with probability {cfg['p_jam']}, otherwise with probability {cfg['p_clean']}. "
      "Predictors jam their single guess.\n")
    akeys = list(ATTACKERS)
    A("| Schedule | " + " | ".join(ATTACKERS[k].name for k in akeys) + " |")
    A("|---|" + "|".join(["---:"] * len(akeys)) + "|")
    for n in allnames:
        A(f"| {n} | " + " | ".join(f"{_pct(cards[n]['attackers'][k]['tput_mean'])} ({_pct(cards[n]['attackers'][k]['tput_p10'])})" for k in akeys) + " |")
    ref = {k: cards[allnames[0]]["attackers"][k].get("tput_ref") for k in jam_keys}
    A("\nReference throughput of an ideal-random schedule for the jammers (analytic): " +
      ", ".join(f"{ATTACKERS[k].name} {_pct(v)}" for k, v in ref.items() if v) + ".\n")

    A("## Jammer hit rate vs chance\n")
    A("| Schedule | " + " | ".join(f"{ATTACKERS[k].name} (chance {_pct(cards[allnames[0]]['attackers'][k]['chance'])})" for k in jam_keys) + " |")
    A("|---|" + "|".join(["---:"] * len(jam_keys)) + "|")
    for n in allnames:
        row = []
        for k in jam_keys:
            a = cards[n]["attackers"][k]
            s = _pct(a["hit_mean"])
            row.append(f"**{s}**" if a["significant"] else s)
        A(f"| {n} | " + " | ".join(row) + " |")

    A("\n## Randomness battery (share of trials failing each test at alpha = 0.001)\n")
    A("| Schedule | " + " | ".join(TEST_NAMES) + " | Shannon / min-entropy (bits per hop; ideal "
      f"{math.log2(cfg['M']):.2f}) |")
    A("|---|" + "|".join(["---:"] * len(TEST_NAMES)) + "|---:|")
    for n in allnames:
        rt = cards[n]["randomness_tests"]
        A(f"| {n} | " + " | ".join(f"{100 * rt[t]['fail_fraction']:.0f}%" for t in TEST_NAMES) +
          f" | {cards[n]['shannon_bits']:.3f} / {cards[n]['min_entropy_bits']:.3f} |")

    A("\n## What the numbers say (generated from the table values)\n")
    for n in sorted(builtin, key=lambda n: rank[n]):
        c = cards[n]
        worst = sorted(((v, a) for a, v in c["axes"].items()), key=lambda t: t[0])[:2]
        broken = [(ATTACKERS[k].name, a["obs_break_median"]) for k, a in c["attackers"].items()
                  if a["kind"] == "predictor" and a["obs_break_median"] is not None and a["broken_fraction"] >= 0.5]
        txt = f"- **{n}** ({c['score']:.1f}): weakest axes " + ", ".join(f"{AXES[a][0]} {_pct(v)}" for v, a in worst) + "."
        if broken:
            txt += " Broken (>= 50 % of trials) by " + "; ".join(f"{b} after {o:.0f} hops" for b, o in broken) + "."
        else:
            txt += " No attacker in the suite produced a sustained break."
        A(txt)

    A("\n## Scoring (summary; full derivation in README.md)\n")
    A(f"- Per attacker: `resist = {W_ADV} * advantage_score + {W_SECOND} * (observations_score | throughput_score)`, advantage = log2(hit/chance)/log2(1/chance).")
    A("- Axis weights: " + ", ".join(f"{AXES[a][0]} {AXES[a][1]}" for a in AXIS_ORDER) + ".")
    A(f"- Weakest-link gate: `score = 100 * weighted_mean * ({GATE_FLOOR} + {1 - GATE_FLOOR:.1f} * min({', '.join(AXES[a][0] for a in GATE_AXES)}))`.\n")

    A("## Caveats (read these)\n")
    for t in [
        "Scores depend entirely on the attacker suite. A schedule with a weakness none of the 11 attackers exploits scores high. A 100 means \"nothing found here\", never \"secure\".",
        "Xorshift-16, LFSR-16 and LCG-24 are scored against a white-box candidate-elimination attacker written for exactly that generator. A custom generator you plug in has no such dedicated attacker, so its score is an upper bound on its real resilience.",
        "The threat model is generous to the attacker in one way (perfect, instant observation of every past hop, knowledge of the algorithm) and generous to the defender in others (no side channels, no key leakage, no synchronisation errors, the receiver always hears the right channel).",
        "Throughput is a toy model: one packet per slot, a binary jam/no-jam loss draw. It is not a link-budget or RF simulation.",
        "The keyed schedules (HMAC-SHA256, SipHash-2-4) score about as high as the ideal i.i.d. control. That means these attackers cannot tell them from random; it says nothing about attacks outside the suite. Their real strength rests on the cryptographic assumptions, not on this benchmark.",
        "Weights, the 16-in-a-row break criterion, the 200-slot learning window and the log-advantage transform are design choices. Changing them re-orders scores of mid-range schedules, though not the broken-vs-unbroken split.",
        f"Sampling: {cfg['trials']} secrets per schedule gives 99 % bootstrap CIs of roughly +-0.3 to 1 percentage points on near-chance hit rates; differences smaller than that are noise. Rerun with more trials to tighten.",
        "The ideal control uses numpy PCG64, itself not cryptographic; it only represents i.i.d. uniform hops.",
    ]:
        A(f"- {t}")
    A("\n## Reproduce\n")
    A("```\npython -m hopbench run --out results --jobs 2\n```")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
