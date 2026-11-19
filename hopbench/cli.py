"""Command line: `python -m hopbench run | score | list`."""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from .runner import SuiteConfig, run_suite
from .schedules import BUILTIN_SCHEDULES, CONTROL_SCHEDULES, Schedule
from .scoring import scorecard, verdict
from . import report


def _load(spec: str):
    mod, _, cls = spec.partition(":")
    sys.path.insert(0, os.getcwd())
    m = importlib.import_module(mod)
    return getattr(m, cls)


def grade(factories: dict, cfg: SuiteConfig, jobs=1, progress=print):
    res = run_suite(factories, cfg, jobs=jobs, progress=progress)
    meta = res.pop("_meta")
    cards = {n: scorecard(res[n], cfg.as_dict()) for n in factories}
    return cards, meta


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hopbench", description="Grade frequency-hop schedules against a simulated attacker suite.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "score"):
        p = sub.add_parser(name)
        p.add_argument("--quick", action="store_true", help="small run (about 10 s)")
        p.add_argument("--trials", type=int)
        p.add_argument("--slots", type=int)
        p.add_argument("--channels", type=int, default=13)
        p.add_argument("--jobs", type=int, default=1)
        p.add_argument("--out", default="results")
        if name == "score":
            p.add_argument("schedule", nargs="+", help="module:Class of your Schedule subclass (constructor (M, seed))")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for n, c in {**BUILTIN_SCHEDULES, **CONTROL_SCHEDULES}.items():
            print(f"{n:22s} {c.description}")
        return 0
    kw = {k: v for k, v in (("trials", a.trials), ("slots", a.slots)) if v}
    cfg = SuiteConfig.quick(M=a.channels, **kw) if a.quick else SuiteConfig(M=a.channels, **kw)
    os.makedirs(a.out, exist_ok=True)
    if a.cmd == "score":
        facs = {}
        for s in a.schedule:
            c = _load(s)
            assert issubclass(c, Schedule) or callable(c)
            facs[getattr(c, "name", c.__name__)] = c
        facs.update({"Control: ideal-random": CONTROL_SCHEDULES["Control: ideal-random"]})
        cards, meta = grade(facs, cfg, a.jobs)
        for n in facs:
            c = cards[n]
            print(f"{n:24s} score {c['score']:5.1f}  ({verdict(c['score'])})")
        report.write_detail(os.path.join(a.out, "custom_detail.csv"), cards)
        report.write_json(os.path.join(a.out, "custom_scorecards.json"), cards, meta)
        report.radar_svg(os.path.join(a.out, "custom_radar.svg"), cards, list(facs), cols=min(3, len(facs)))
        return 0
    facs = {**BUILTIN_SCHEDULES, **CONTROL_SCHEDULES}
    cards, meta = grade(facs, cfg, a.jobs)
    builtin, controls = list(BUILTIN_SCHEDULES), list(CONTROL_SCHEDULES)
    out = a.out
    svg, png = os.path.join(out, "radar.svg"), os.path.join(out, "radar.png")
    report.radar_svg(svg, cards, builtin + controls)
    has_png = report.radar_png(png, cards, builtin + controls)
    report.write_leaderboard(os.path.join(out, "leaderboard.csv"), cards, builtin, controls)
    report.write_detail(os.path.join(out, "detail.csv"), cards)
    report.write_json(os.path.join(out, "scorecards.json"), cards, meta)
    report.write_markdown(os.path.join(out, "results.md"), cards, builtin, controls, meta, [svg])
    for n in builtin + controls:
        print(f"{n:24s} {cards[n]['score']:5.1f}  {verdict(cards[n]['score'])}")
    print("wrote", out, "(radar.png skipped: matplotlib missing)" if not has_png else "")
    return 0
