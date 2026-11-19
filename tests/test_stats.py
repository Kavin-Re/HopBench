import math, unittest
import numpy as np
from hopbench.stats import *
from hopbench.schedules import lfsr16_step

try:
    import scipy.stats as st, scipy.special as sp
except Exception:
    st = sp = None


class TestSpecial(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(chi2_sf(3.841458820694124, 1), 0.05, places=6)
        self.assertAlmostEqual(chi2_sf(18.307038053275146, 10), 0.05, places=6)
        self.assertAlmostEqual(norm_sf2(1.959963984540054), 0.05, places=7)
        self.assertEqual(gammaincc(2.0, 0.0), 1.0)

    def test_gamma_complements(self):
        for a, x in [(0.5, 0.3), (3, 1), (84, 100), (84, 150), (6.5, 30)]:
            self.assertAlmostEqual(gammainc_lower(a, x) + gammaincc(a, x), 1.0, places=9)

    @unittest.skipIf(sp is None, "scipy not installed")
    def test_vs_scipy(self):
        for a, x in [(0.5, 0.3), (3, 1), (84, 100), (84, 150), (6.5, 30), (1, 5), (200, 190)]:
            self.assertAlmostEqual(gammaincc(a, x) / sp.gammaincc(a, x), 1.0, places=6)
        self.assertAlmostEqual(chi2_sf(190, 168) / st.chi2.sf(190, 168), 1.0, places=6)


class TestCI(unittest.TestCase):
    def test_bootstrap_brackets_mean(self):
        v = np.random.default_rng(0).normal(5, 1, 40)
        lo, hi = bootstrap_ci(v)
        self.assertLess(lo, v.mean()); self.assertGreater(hi, v.mean())
        self.assertLess(hi - lo, 1.0)

    def test_bootstrap_degenerate(self):
        self.assertEqual(bootstrap_ci([0.5, 0.5, 0.5]), (0.5, 0.5))
        self.assertEqual(bootstrap_ci([0.3]), (0.3, 0.3))

    def test_bootstrap_coverage(self):
        rng = np.random.default_rng(3); hit = 0
        for i in range(200):
            v = rng.normal(0, 1, 20)
            lo, hi = bootstrap_ci(v, 0.95, n_boot=300, seed=i)
            hit += lo <= 0 <= hi
        self.assertGreater(hit / 200, 0.88)

    def test_wilson(self):
        lo, hi = wilson_ci(50, 100)
        self.assertAlmostEqual(lo, 0.4038, places=3); self.assertAlmostEqual(hi, 0.5962, places=3)
        self.assertEqual(wilson_ci(0, 0), (0.0, 1.0))
        self.assertEqual(wilson_ci(10, 10)[1], 1.0)


class TestBM(unittest.TestCase):
    def lfsr_bits(self, n):
        x, out = 0xACE1, []
        for _ in range(n):
            out.append(x & 1); x = lfsr16_step(x)
        return out

    def test_lfsr_complexity_is_16(self):
        self.assertEqual(linear_complexity(self.lfsr_bits(200)), 16)

    def test_trivial_sequences(self):
        self.assertEqual(linear_complexity([0] * 50), 0)
        self.assertEqual(linear_complexity([1] * 50), 1)
        self.assertEqual(linear_complexity([0, 0, 0, 1]), 4)           # classic textbook case
        self.assertEqual(linear_complexity([1, 0] * 30), 2)

    def test_random_complexity_near_half(self):
        rng = np.random.default_rng(1)
        Ls = [linear_complexity(rng.integers(0, 2, 1000)) for _ in range(40)]
        self.assertAlmostEqual(np.mean(Ls), 500.17, delta=0.8)

    def test_online_prediction(self):
        bits = self.lfsr_bits(120)
        bm = OnlineBM(); ok = 0
        for i, b in enumerate(bits):
            if i >= 34:
                ok += bm.predict() == b
            bm.feed(b)
        self.assertEqual(ok, 120 - 34)

    def test_pmf_sums_to_one_and_pvalue(self):
        for n in (10, 11, 500):
            self.assertAlmostEqual(sum(lc_pmf(l, n) for l in range(n + 1)), 1.0, places=12)
        self.assertEqual(lc_pvalue(500, 1000), 1.0)
        self.assertLess(lc_pvalue(16, 1000), 1e-100)
        self.assertLess(lc_pvalue(506, 1000), 1e-3)

    def test_pvalue_calibration_on_random(self):
        rng = np.random.default_rng(9)
        ps = [lc_pvalue(linear_complexity(rng.integers(0, 2, 600)), 600) for _ in range(300)]
        self.assertLess(np.mean(np.array(ps) < 0.01), 0.03)    # exact test is conservative
