import unittest
import numpy as np
from hopbench.randtests import *
from hopbench.schedules import *

M = 13
N = 6000


def stream(cls, seed=1):
    return cls(M, seed).hops(N)


class TestBattery(unittest.TestCase):
    def test_names_complete(self):
        p = run_battery(stream(IdealRandom), M)
        self.assertEqual(list(p), TEST_NAMES)
        self.assertTrue(all(0 <= v <= 1 for v in p.values()))

    def test_ideal_and_keyed_pass(self):
        for cls in (IdealRandom, HmacSha256, SipHash24, LCG24):
            fails = sum(v < ALPHA for s in range(4) for v in run_battery(stream(cls, s), M).values())
            self.assertLessEqual(fails, 1, cls.name)

    def test_constant_fails_everything_relevant(self):
        p = run_battery(stream(ConstantChannel), M)
        self.assertEqual(sum(v < ALPHA for v in p.values()), len(p))

    def test_counter_fails_periodicity_and_lc(self):
        p = run_battery(stream(CounterMod), M)
        self.assertLess(p["coincidence_scan"], ALPHA)
        self.assertLess(p["lc_plane0"], ALPHA)

    def test_table_fails_bias_and_period(self):
        p = run_battery(stream(TableSchedule), M)
        self.assertLess(p["coincidence_scan"], ALPHA)
        self.assertLess(p["pair_chi2"], ALPHA)

    def test_lfsr_mod_m_fails_serial_structure(self):
        p = run_battery(stream(LFSR16), M)
        self.assertLess(p["serial_corr_lag1"], ALPHA)
        self.assertLess(p["pair_chi2"], ALPHA)

    def test_individual_tests_detect_bias(self):
        rng = np.random.default_rng(2)
        biased = rng.choice(M, N, p=np.r_[0.2, np.full(M - 1, 0.8 / (M - 1))])
        self.assertLess(chi2_uniform(biased, M), 1e-6)
        trend = (np.arange(N) // 3) % M
        self.assertLess(serial_corr(trend, M), 1e-6)
        alt = np.tile([0, 12], N // 2)
        self.assertLess(runs_test(alt, M), 1e-6)
        self.assertEqual(runs_test(np.zeros(100, dtype=int), M), 0.0)

    def test_coincidence_finds_period(self):
        rng = np.random.default_rng(4)
        per = np.tile(rng.integers(0, M, 500), 12)
        self.assertLess(coincidence_scan(per, M), 1e-10)

    def test_entropy(self):
        s, m = entropy_bits(stream(IdealRandom), M)
        self.assertAlmostEqual(s, np.log2(M), delta=0.01)
        self.assertGreater(m, 3.4)
        self.assertEqual(entropy_bits(np.zeros(10, dtype=int), M), (0.0, 0.0))

    def test_false_positive_rate_low(self):
        fails = 0
        for s in range(12):
            fails += sum(v < ALPHA for v in run_battery(stream(IdealRandom, s), M).values())
        self.assertLessEqual(fails, 2)      # 120 tests at alpha 1e-3
