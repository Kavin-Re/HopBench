"""Randomness test battery for a hop stream over M channels.

Every test returns a p-value; a test FAILS when p < ALPHA. These tests catch statistical
defects (bias, short period, linear structure). Passing them says nothing about whether
an attacker who knows the generator can recover the secret: LFSR-16 passes most of them
and is still broken in a handful of hops by the state-recovery attacker.
"""
from __future__ import annotations

import math

import numpy as np

from .stats import chi2_sf, linear_complexity, lc_pvalue, norm_sf2

ALPHA = 1e-3
TEST_NAMES = ["chi2_uniform", "serial_corr_lag1", "runs", "pair_chi2", "autocorr_max64",
              "coincidence_scan", "lc_plane0", "lc_plane1", "lc_plane2", "lc_plane3"]


def chi2_uniform(h, M):
    n = len(h)
    cnt = np.bincount(h, minlength=M)
    e = n / M
    return chi2_sf(float(((cnt - e) ** 2 / e).sum()), M - 1)


def _corr(x, k):
    x = x - x.mean()
    den = float((x * x).sum())
    if den == 0:
        return 1.0
    return float((x[:-k] * x[k:]).sum() / den)


def serial_corr(h, M, lag=1):
    n = len(h)
    r = _corr(h.astype(float), lag)
    return norm_sf2(r * math.sqrt(n))


def runs_test(h, M):
    b = h > (M - 1) / 2.0
    n1 = int(b.sum())
    n0 = len(b) - n1
    if n0 == 0 or n1 == 0:
        return 0.0
    n = len(b)
    runs = 1 + int((b[1:] != b[:-1]).sum())
    mu = 2.0 * n1 * n0 / n + 1
    var = (mu - 1) * (mu - 2) / (n - 1)
    return norm_sf2((runs - mu) / math.sqrt(var)) if var > 0 else 0.0


def pair_chi2(h, M):
    n = len(h) - 1
    cnt = np.bincount(h[:-1] * M + h[1:], minlength=M * M)
    e = n / (M * M)
    if e < 5:
        return 1.0  # not enough data: do not fail
    return chi2_sf(float(((cnt - e) ** 2 / e).sum()), M * M - 1)


def autocorr_max(h, M, maxlag=64):
    n = len(h)
    x = h.astype(float)
    ps = [norm_sf2(_corr(x, k) * math.sqrt(n)) for k in range(1, maxlag + 1)]
    return min(1.0, min(ps) * maxlag)  # Bonferroni


def coincidence_scan(h, M, maxlag=2048):
    """For each lag k the fraction of t with h[t] == h[t+k] is ~1/M for random streams and ~1 at
    a period. Bonferroni-corrected over all lags: detects short periods (tables, counters)."""
    n = len(h)
    maxlag = min(maxlag, n // 3)
    best = 1.0
    for k in range(1, maxlag + 1):
        m = n - k
        c = int((h[:-k] == h[k:]).sum())
        mu = m / M
        sd = math.sqrt(m * (1 / M) * (1 - 1 / M))
        z = (c - mu) / sd
        p = 0.5 * math.erfc(z / math.sqrt(2.0))  # one-sided: only too MANY coincidences
        best = min(best, p)
    return min(1.0, best * maxlag)


def lc_planes(h, M, nbits=2000):
    """Linear complexity (Berlekamp-Massey over GF(2)) of each bit plane of the channel index."""
    nb = max(1, (M - 1).bit_length())
    out = []
    for p in range(4):
        if p >= nb:
            out.append(1.0)
            continue
        bits = (h[:nbits] >> p) & 1
        out.append(lc_pvalue(linear_complexity(bits), len(bits)))
    return out


def run_battery(h, M) -> dict:
    """Returns {test_name: p_value} for a hop stream h (numpy int array)."""
    h = np.asarray(h, dtype=np.int64)
    p = {
        "chi2_uniform": chi2_uniform(h, M),
        "serial_corr_lag1": serial_corr(h, M, 1),
        "runs": runs_test(h, M),
        "pair_chi2": pair_chi2(h, M),
        "autocorr_max64": autocorr_max(h, M),
        "coincidence_scan": coincidence_scan(h, M),
    }
    for i, v in enumerate(lc_planes(h, M)):
        p[f"lc_plane{i}"] = v
    return p


def entropy_bits(h, M):
    """Plug-in Shannon entropy and min-entropy (bits/hop) of the empirical channel distribution."""
    p = np.bincount(h, minlength=M) / len(h)
    nz = p[p > 0]
    return float(-(nz * np.log2(nz)).sum()), float(-math.log2(p.max()))
