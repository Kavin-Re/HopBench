"""The attacker suite.

Every attacker is a *simulated* adversary that sees all past hops perfectly and, each slot,
commits to a set of channels to jam (`jam_set`) BEFORE the hop is revealed, then `observe`s it.
Predictors jam exactly one channel (their best guess). Jammers jam k channels by their own rule.

Kerckhoffs: state-recovery attackers know the generator and parameters, not the secret.

    kind == "predictor":  accuracy and 'observations to break' are meaningful.
    kind == "jammer":     only throughput / hit rate are meaningful.
"""
from __future__ import annotations

import numpy as np

from .schedules import lfsr16_step, lcg24_step, xorshift16_step, LCG_A, LCG_C, LCG_MASK
from .stats import OnlineBM


class Attacker:
    name = "attacker"
    kind = "jammer"
    k_eff = 1          # channels jammed per slot (for the chance level k/M)
    summary = ""

    def __init__(self, M: int, rng: np.random.Generator):
        self.M = M
        self.rng = rng

    def jam_set(self) -> list:  # pragma: no cover
        raise NotImplementedError

    def observe(self, ch: int) -> None:  # pragma: no cover
        raise NotImplementedError


class Predictor(Attacker):
    kind = "predictor"

    def predict(self) -> int:  # pragma: no cover
        raise NotImplementedError

    def jam_set(self):
        return [int(self.predict())]


# ============================================================================ state recovery
_FIRST_CACHE: dict = {}


def _array_steps():
    """Vectorised step / out functions on uint32 arrays (match schedules.py exactly)."""
    def lfsr(x):
        bit = ((x >> 0) ^ (x >> 2) ^ (x >> 3) ^ (x >> 5)) & 1
        return (x >> 1) | (bit << 15)

    def lcg(x):
        return (np.uint32(LCG_A) * x + np.uint32(LCG_C)) & np.uint32(LCG_MASK)  # wraps mod 2^32, mask keeps 24 bits

    def xs(x):
        x = x ^ ((x << 7) & 0xFFFF)
        x = x ^ (x >> 9)
        return x ^ ((x << 8) & 0xFFFF)
    return lfsr, lcg, xs


class StateBreaker(Predictor):
    """Candidate elimination: keep every internal state consistent with the hops seen so far.
    One consistent state left => every future hop is predicted exactly.
    Subclasses set `universe` (all possible states), `step_arr`, `out_arr`."""

    cap_vote = 400_000  # above this many candidates a vote is no better than chance: guess randomly

    universe_fn = None
    step_arr = None
    out_arr = None

    def __init__(self, M, rng):
        super().__init__(M, rng)
        self.cand = None   # None = nothing observed yet (all states possible)
        self.seen = 0

    @classmethod
    def _first(cls, M):
        key = (cls.__name__, M)
        if key not in _FIRST_CACHE:
            u = cls.universe_fn()
            o = cls.out_arr(u, M)
            order = np.argsort(o, kind="stable")
            counts = np.bincount(o, minlength=M)
            ends = np.cumsum(counts)
            starts = ends - counts
            _FIRST_CACHE[key] = [u[order[s:e]] for s, e in zip(starts, ends)]
        return _FIRST_CACHE[key]

    def observe(self, ch):
        if self.cand is None:
            self.cand = self._first(self.M)[ch]
        elif len(self.cand):
            c = self.step_arr(self.cand)
            self.cand = c[self.out_arr(c, self.M) == ch]
        self.seen += 1

    def predict(self):
        c = self.cand
        if c is None or len(c) == 0 or len(c) > self.cap_vote:
            return int(self.rng.integers(self.M))
        nxt = self.out_arr(self.step_arr(c), self.M)
        if len(c) == 1:
            return int(nxt[0])
        return int(np.bincount(nxt, minlength=self.M).argmax())

    def identified(self) -> bool:
        return self.cand is not None and len(self.cand) == 1


_lfsr_a, _lcg_a, _xs_a = _array_steps()


class LfsrBreaker(StateBreaker):
    name = "LFSR-16 breaker"
    summary = "candidate elimination over all 65535 LFSR-16 states"
    universe_fn = staticmethod(lambda: np.arange(1, 65536, dtype=np.uint32))
    step_arr = staticmethod(_lfsr_a)
    out_arr = staticmethod(lambda x, M: (x % M).astype(np.int64))


class LcgBreaker(StateBreaker):
    name = "LCG-24 recovery"
    summary = "candidate elimination over all 2^24 LCG states (known a, c, output map)"
    universe_fn = staticmethod(lambda: np.arange(0, 1 << 24, dtype=np.uint32))
    step_arr = staticmethod(_lcg_a)
    out_arr = staticmethod(lambda x, M: ((x >> 8) % M).astype(np.int64))


class XorshiftBreaker(StateBreaker):
    name = "Xorshift-16 breaker"
    summary = "candidate elimination over all 65535 xorshift-16 states"
    universe_fn = staticmethod(lambda: np.arange(1, 65536, dtype=np.uint32))
    step_arr = staticmethod(_xs_a)
    out_arr = staticmethod(lambda x, M: (x % M).astype(np.int64))


# ============================================================================ n-gram / Markov
class NGramPredictor(Predictor):
    """Order-1..3 context model with back-off: predict the most frequent successor of the longest
    context seen before. Learns periodic tables, constant strides and Markov structure."""
    name = "n-gram / Markov"
    summary = "order-3 context model with back-off (orders 3,2,1,0)"

    def __init__(self, M, rng, max_order=3):
        super().__init__(M, rng)
        self.K = max_order
        self.tables = [dict() for _ in range(max_order + 1)]  # order -> {context tuple: counts}
        self.hist: list = []

    def predict(self):
        for k in range(min(self.K, len(self.hist)), -1, -1):
            ctx = tuple(self.hist[len(self.hist) - k:]) if k else ()
            cnt = self.tables[k].get(ctx)
            if cnt is not None:
                m = cnt.max()
                if m > 0:
                    return int(self.rng.choice(np.flatnonzero(cnt == m)))
        return int(self.rng.integers(self.M))

    def observe(self, ch):
        for k in range(min(self.K, len(self.hist)) + 1):
            ctx = tuple(self.hist[len(self.hist) - k:]) if k else ()
            cnt = self.tables[k].get(ctx)
            if cnt is None:
                cnt = self.tables[k][ctx] = np.zeros(self.M, dtype=np.int32)
            cnt[ch] += 1
        self.hist.append(int(ch))


# ============================================================================ linear complexity (BM)
class BerlekampMassey(Predictor):
    """Linear-complexity attacker. The channel index is split into bit planes; an online
    Berlekamp-Massey over GF(2) fits the shortest LFSR to each plane. If every plane has low
    linear complexity (L <= n/4) the attacker trusts the fitted LFSRs to predict the next index,
    otherwise it guesses. Breaks anything GF(2)-linear in the channel bits or periodic with a short
    period; says nothing about mod-M mappings of an LFSR (those are not linear in the bits)."""
    name = "Berlekamp-Massey"
    summary = "online BM over GF(2) on each channel-bit plane"
    min_obs = 12

    def __init__(self, M, rng):
        super().__init__(M, rng)
        self.nb = max(1, (M - 1).bit_length())
        self.bms = [OnlineBM() for _ in range(self.nb)]
        self.n = 0

    def predict(self):
        if self.n >= self.min_obs and all(b.L <= self.n / 4 for b in self.bms):
            v = sum(b.predict() << i for i, b in enumerate(self.bms))
            if v < self.M:
                return int(v)
        return int(self.rng.integers(self.M))

    def observe(self, ch):
        for i, b in enumerate(self.bms):
            b.feed((ch >> i) & 1)
        self.n += 1

    def max_complexity(self):
        return max(b.L for b in self.bms)


# ============================================================================ frequency bias
class FrequencyBias(Predictor):
    """Guess the most frequently seen channel so far. Pays off only on biased schedules."""
    name = "Frequency-bias"
    summary = "jam the most-used channel so far"

    def __init__(self, M, rng):
        super().__init__(M, rng)
        self.cnt = np.zeros(M, dtype=np.int64)

    def predict(self):
        m = self.cnt.max()
        return int(self.rng.choice(np.flatnonzero(self.cnt == m)))

    def observe(self, ch):
        self.cnt[ch] += 1


# ============================================================================ jammers
class ReactiveDelay(Attacker):
    """Reactive jammer: senses the channel in use, but needs `delay` slots to retune, so it jams the
    channel that was in use `delay` slots ago. Hits only when the schedule repeats at that lag."""
    name = "Reactive (delay 1)"
    summary = "jams the channel used `delay` slots ago"

    def __init__(self, M, rng, delay=1):
        super().__init__(M, rng)
        self.delay = delay
        self.hist: list = []

    def jam_set(self):
        if len(self.hist) >= self.delay:
            return [self.hist[-self.delay]]
        return [int(self.rng.integers(self.M))]

    def observe(self, ch):
        self.hist.append(int(ch))


class Sweep(Attacker):
    name = "Sweep"
    summary = "steps through the band one channel per slot"

    def __init__(self, M, rng):
        super().__init__(M, rng)
        self.t = int(rng.integers(M))

    def jam_set(self):
        return [self.t % self.M]

    def observe(self, ch):
        self.t += 1


class RandomJammer(Attacker):
    name = "Random"
    summary = "jams one uniformly random channel per slot"

    def jam_set(self):
        return [int(self.rng.integers(self.M))]

    def observe(self, ch):
        pass


class PartialBand(Attacker):
    """Jams a contiguous band of `width` channels at equal power each; the band relocates every `move_every` slots."""
    name = "Partial-band (4 ch)"
    summary = "jams a 4-channel band that relocates every 250 slots"

    def __init__(self, M, rng, width=4, move_every=250):
        super().__init__(M, rng)
        self.width, self.move_every, self.t = width, move_every, 0
        self.k_eff = width
        self.start = int(rng.integers(M))

    def jam_set(self):
        if self.t and self.t % self.move_every == 0 and self.t != getattr(self, "_moved", -1):
            self.start = int(self.rng.integers(self.M))
            self._moved = self.t
        return [(self.start + i) % self.M for i in range(self.width)]

    def observe(self, ch):
        self.t += 1


class Follower(Attacker):
    """Trend follower: extrapolates the last hop step (2*h[t-1] - h[t-2]) and jams that channel and
    both neighbours. Defeats arithmetic-progression schedules, ~3/M elsewhere."""
    name = "Follower (trend, +-1)"
    summary = "extrapolates the last hop step; jams guess and both neighbours"
    k_eff = 3

    def __init__(self, M, rng):
        super().__init__(M, rng)
        self.hist: list = []

    def jam_set(self):
        if len(self.hist) >= 2:
            g = (2 * self.hist[-1] - self.hist[-2]) % self.M
        else:
            g = int(self.rng.integers(self.M))
        return [(g - 1) % self.M, g, (g + 1) % self.M]

    def observe(self, ch):
        self.hist.append(int(ch))


# Order matters only for reporting. Keys are stable ids used in CSV / JSON output.
ATTACKERS = {
    "lfsr_breaker": LfsrBreaker,
    "lcg_recovery": LcgBreaker,
    "xorshift_breaker": XorshiftBreaker,
    "ngram": NGramPredictor,
    "berlekamp_massey": BerlekampMassey,
    "frequency_bias": FrequencyBias,
    "reactive_delay": ReactiveDelay,
    "sweep": Sweep,
    "random": RandomJammer,
    "partial_band": PartialBand,
    "follower": Follower,
}
