"""Small numpy-only statistics toolbox (kept SciPy-free so the only hard dependency is NumPy).

* special functions: regularized incomplete gamma, normal tail
* bootstrap confidence intervals over trials
* Berlekamp-Massey over GF(2) (online) and linear complexity
"""
from __future__ import annotations

import math

import numpy as np


# ----------------------------------------------------------------------------- special functions
def gammainc_lower(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) (Numerical-Recipes series / continued fraction)."""
    if x <= 0:
        return 0.0
    if a <= 0:
        raise ValueError("a must be > 0")
    gln = math.lgamma(a)
    if x < a + 1:  # series
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(10000):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return s * math.exp(-x + a * math.log(x) - gln)
    # continued fraction for Q
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    q = math.exp(-x + a * math.log(x) - gln) * h
    return 1.0 - q


def gammaincc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) = 1 - P(a, x), computed stably in the tail."""
    if x <= 0:
        return 1.0
    if x < a + 1:
        return 1.0 - gammainc_lower(a, x)
    tiny = 1e-300
    gln = math.lgamma(a)
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(chi2: float, df: int) -> float:
    """P(X >= chi2) for X ~ chi-square(df)."""
    return gammaincc(df / 2.0, chi2 / 2.0)


def norm_sf2(z: float) -> float:
    """Two-sided normal tail P(|Z| >= |z|)."""
    return math.erfc(abs(z) / math.sqrt(2.0))


# ----------------------------------------------------------------------------- confidence intervals
def bootstrap_ci(values, level: float = 0.95, n_boot: int = 2000, seed: int = 12345, stat=np.mean):
    """Percentile bootstrap CI of `stat` over independent trials. Returns (lo, hi)."""
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return (float("nan"), float("nan"))
    if len(v) == 1 or np.all(v == v[0]):
        return (float(stat(v)), float(stat(v)))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(n_boot, len(v)))
    boots = np.array([stat(v[i]) for i in idx])
    a = (1 - level) / 2
    return (float(np.quantile(boots, a)), float(np.quantile(boots, 1 - a)))


def wilson_ci(k: int, n: int, z: float = 1.96):
    """Wilson score interval for a binomial proportion (used by the in-browser game and tests)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


# ----------------------------------------------------------------------------- Berlekamp-Massey over GF(2)
class OnlineBM:
    """Online Berlekamp-Massey over GF(2) using Python ints as bit vectors.

    feed(bit) updates the shortest LFSR that generates the stream so far. predict() returns the
    next bit that LFSR would emit. Linear complexity is `.L`. Needs >= 2L bits to be determined.
    """

    def __init__(self):
        self.C = 1      # connection polynomial, bit i = c_i, c_0 = 1
        self.B = 1
        self.L = 0
        self.m = 1
        self.n = 0
        self.w = 0      # bit i = s_{n-1-i}  (most recent bit at position 0)

    def feed(self, bit: int) -> None:
        bit &= 1
        w = (self.w << 1) | bit  # bit i = s_{n-i}
        d = (self.C & w).bit_count() & 1
        if d == 0:
            self.m += 1
        else:
            t = self.C
            self.C ^= self.B << self.m
            if 2 * self.L <= self.n:
                self.L = self.n + 1 - self.L
                self.B = t
                self.m = 1
            else:
                self.m += 1
        self.w = w
        self.n += 1

    def predict(self) -> int:
        return ((self.C >> 1) & self.w).bit_count() & 1


def linear_complexity(bits) -> int:
    bm = OnlineBM()
    for b in bits:
        bm.feed(int(b))
    return bm.L


def lc_pmf(l: int, n: int) -> float:
    """Exact P(linear complexity = l) for a uniformly random n-bit sequence (Gustavson / Rueppel):
    the number of sequences with complexity l >= 1 is 2^(min(2l, 2(n-l)+1) - 1); l = 0 only for all-zeros."""
    if l < 0 or l > n:
        return 0.0
    if l == 0:
        return math.ldexp(1.0, -n)
    return math.ldexp(1.0, min(2 * l, 2 * (n - l) + 1) - 1 - n)


def lc_pvalue(L: int, n: int) -> float:
    """Exact two-sided p-value of an observed linear complexity under H0 = uniform random bits:
    2 * min(P(L' <= L), P(L' >= L)), capped at 1."""
    mid = n // 2
    if L >= mid:
        tail = sum(lc_pmf(l, n) for l in range(L, min(n, L + 80) + 1))
    else:
        tail = sum(lc_pmf(l, n) for l in range(max(0, L - 80), L + 1))
    return min(1.0, 2.0 * tail)
