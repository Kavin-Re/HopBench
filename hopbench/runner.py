"""Runs the attacker suite against schedules and collects raw per-trial measurements."""
from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .attackers import ATTACKERS
from .randtests import run_battery, entropy_bits, TEST_NAMES
from .schedules import derive

# ---- experiment parameters (documented in README; override through SuiteConfig) -------------
class SuiteConfig:
    def __init__(self, M=13, trials=16, slots=2000, warmup=200, streak=16, p_jam=0.95, p_clean=0.01,
                 rand_len=6000, seed0=1000, attackers=None):
        self.M, self.trials, self.slots, self.warmup, self.streak = M, trials, slots, warmup, streak
        self.p_jam, self.p_clean, self.rand_len, self.seed0 = p_jam, p_clean, rand_len, seed0
        self.attackers = list(attackers) if attackers else list(ATTACKERS)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def quick(cls, **kw):
        base = dict(trials=6, slots=800, warmup=150, rand_len=3000)
        base.update(kw)
        return cls(**base)


def first_streak(hits: np.ndarray, k: int):
    """Index of the first run of k consecutive True values (= observations seen before it), else None."""
    if len(hits) < k:
        return None
    s = np.convolve(hits.astype(np.int32), np.ones(k, dtype=np.int32), mode="valid")
    idx = np.flatnonzero(s == k)
    return int(idx[0]) if len(idx) else None


def stream(schedule, n):
    """Draw n hops through the public interface only (next_channel)."""
    return np.fromiter((schedule.next_channel(t) for t in range(n)), dtype=np.int64, count=n)


def run_attacker_trial(h, attacker_cls, cfg: SuiteConfig, seed: int):
    """One attacker against one hop stream h. Returns dict(hit, tput, obs_break)."""
    M = cfg.M
    rng = np.random.default_rng(seed)
    loss_rng = np.random.default_rng(seed + 7_000_003)
    att = attacker_cls(M, rng)
    n = len(h)
    hit = np.zeros(n, dtype=bool)
    delivered = np.zeros(n, dtype=bool)
    u = loss_rng.random(n)
    for t in range(n):
        js = att.jam_set()
        c = int(h[t])
        hit[t] = c in js
        delivered[t] = u[t] >= (cfg.p_jam if hit[t] else cfg.p_clean)
        att.observe(c)
    w = cfg.warmup
    out = dict(hit=float(hit[w:].mean()), tput=float(delivered[w:].mean()))
    out["obs_break"] = first_streak(hit, cfg.streak) if attacker_cls.kind == "predictor" else None
    return out


def _task(args):
    sname, factory, akey, cfg = args
    acls = ATTACKERS[akey]
    res = dict(hit=[], tput=[], obs_break=[])
    for i in range(cfg.trials):
        seed = cfg.seed0 + i
        h = stream(factory(cfg.M, seed), cfg.slots)
        r = run_attacker_trial(h, acls, cfg, seed * 31 + 5)
        for k in res:
            res[k].append(r[k])
    return sname, akey, res


def run_randomness(factory, cfg: SuiteConfig):
    p = {t: [] for t in TEST_NAMES}
    ent = []
    for i in range(cfg.trials):
        h = stream(factory(cfg.M, cfg.seed0 + i), cfg.rand_len)
        for t, v in run_battery(h, cfg.M).items():
            p[t].append(float(v))
        ent.append(entropy_bits(h, cfg.M))
    return dict(p=p, shannon=[e[0] for e in ent], minentropy=[e[1] for e in ent])


def run_suite(factories: dict, cfg: SuiteConfig | None = None, jobs: int = 1, progress=print):
    """factories: {name: callable(M, seed) -> Schedule} (a Schedule subclass works directly).
    Returns {name: {"attackers": {key: {...}}, "randomness": {...}}} plus timing info."""
    cfg = cfg or SuiteConfig()
    t0 = time.time()
    tasks = [(n, f, a, cfg) for n, f in factories.items() for a in cfg.attackers]
    results = {n: {"attackers": {}, "randomness": None} for n in factories}
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for i, (sn, ak, res) in enumerate(ex.map(_task, tasks)):
                results[sn]["attackers"][ak] = res
                if progress and (i + 1) % 11 == 0:
                    progress(f"  [{i + 1}/{len(tasks)}] attack runs done ({time.time() - t0:.0f}s)")
    else:
        for i, tk in enumerate(tasks):
            sn, ak, res = _task(tk)
            results[sn]["attackers"][ak] = res
            if progress and (i + 1) % 11 == 0:
                progress(f"  [{i + 1}/{len(tasks)}] attack runs done ({time.time() - t0:.0f}s)")
    for n, f in factories.items():
        results[n]["randomness"] = run_randomness(f, cfg)
    results["_meta"] = dict(config=cfg.as_dict(), seconds=time.time() - t0)
    return results
