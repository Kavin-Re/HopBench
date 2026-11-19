import unittest
import numpy as np
from hopbench.attackers import *
from hopbench.attackers import _lfsr_a, _lcg_a, _xs_a
from hopbench.schedules import *
from hopbench.schedules import lfsr16_step, lcg24_step, xorshift16_step

M = 13


def play(att, h):
    """Run an attacker over stream h; return (hits array, attacker)."""
    hits = []
    for c in h:
        hits.append(int(c) in att.jam_set())
        att.observe(int(c))
    return np.array(hits), att


def rng(s=0):
    return np.random.default_rng(s)


class TestVectorisedSteps(unittest.TestCase):
    def test_match_scalar_steps(self):
        r = np.random.default_rng(0)
        for arr, sc, lo, hi in ((_lfsr_a, lfsr16_step, 1, 65536), (_xs_a, xorshift16_step, 1, 65536), (_lcg_a, lcg24_step, 0, 1 << 24)):
            xs = r.integers(lo, hi, 500).astype(np.uint32)
            self.assertEqual(list(arr(xs)), [sc(int(x)) for x in xs])


class TestStateBreakers(unittest.TestCase):
    CASES = [(LfsrBreaker, LFSR16), (LcgBreaker, LCG24), (XorshiftBreaker, Xorshift16)]

    def test_breaks_matching_generator_fast_and_exactly(self):
        for A, S in self.CASES:
            for seed in range(3):
                h = S(M, seed).hops(60)
                hits, att = play(A(M, rng(seed)), h)
                first = int(np.flatnonzero(np.convolve(hits, np.ones(16), "valid") == 16)[0])
                self.assertLessEqual(first, 25, (A.name, seed))
                self.assertTrue(hits[30:].all(), A.name)
                self.assertTrue(att.identified())

    def test_candidates_shrink_monotonically(self):
        h = LFSR16(M, 2).hops(12)
        att = LfsrBreaker(M, rng()); sizes = []
        for c in h:
            att.observe(int(c)); sizes.append(len(att.cand))
        self.assertTrue(all(a >= b for a, b in zip(sizes, sizes[1:])))
        self.assertEqual(sizes[0], int(np.sum(np.arange(1, 65536) % M == h[0])))

    def test_chance_on_keyed_and_mismatched(self):
        for A, _ in self.CASES:
            for S in (HmacSha256, SipHash24):
                hits, att = play(A(M, rng(1)), S(M, 4).hops(400))
                self.assertLess(hits[50:].mean(), 0.16, (A.name, S.name))
                self.assertFalse(att.identified())

    def test_mismatch_does_not_crash_when_candidates_vanish(self):
        hits, att = play(LfsrBreaker(M, rng()), LCG24(M, 1).hops(100))
        self.assertEqual(len(att.cand), 0)


class TestNGram(unittest.TestCase):
    def test_learns_counter_and_table(self):
        for S, n_learn in ((CounterMod, 40), (TableSchedule, 120)):
            hits, _ = play(NGramPredictor(M, rng()), S(M, 3).hops(400))
            self.assertGreater(hits[n_learn:].mean(), 0.97, S.name)

    def test_chance_on_keyed(self):
        hits, _ = play(NGramPredictor(M, rng()), HmacSha256(M, 2).hops(3000))
        self.assertLess(hits[300:].mean(), 0.105)

    def test_finds_lfsr_mod_m_structure(self):
        hits, _ = play(NGramPredictor(M, rng()), LFSR16(M, 1).hops(2000))
        self.assertGreater(hits[300:].mean(), 0.2)      # successor of hop h has only ~4 possible values


class TestBerlekampMassey(unittest.TestCase):
    class LinearBits(Schedule):
        """GF(2)-linear schedule on 16 channels: 4 consecutive LFSR output bits."""
        def __init__(self, M=16, seed=0):
            super().__init__(M, seed)
            x, bits = 0xACE1 + seed, []
            for _ in range(4 * 600):
                bits.append(x & 1); x = lfsr16_step(x)
            self.bits = bits

        def next_channel(self, slot):
            return sum(self.bits[4 * slot + i] << i for i in range(4))

    def test_breaks_linear_schedule(self):
        h = self.LinearBits(16, 3).hops(500)
        hits, att = play(BerlekampMassey(16, rng()), h)
        self.assertGreater(hits[100:].mean(), 0.97)
        self.assertLessEqual(att.max_complexity(), 16)

    def test_breaks_counter(self):
        hits, _ = play(BerlekampMassey(M, rng()), CounterMod(M, 5).hops(300))
        self.assertGreater(hits[100:].mean(), 0.95)

    def test_chance_on_keyed_and_lfsr_mod_m(self):
        for S in (HmacSha256, LFSR16):
            hits, _ = play(BerlekampMassey(M, rng()), S(M, 2).hops(1500))
            self.assertLess(hits[200:].mean(), 0.11, S.name)


class TestFrequencyAndJammers(unittest.TestCase):
    def test_frequency_bias_exploits_bias(self):
        r = np.random.default_rng(1)
        h = r.choice(M, 3000, p=np.r_[0.4, np.full(M - 1, 0.6 / (M - 1))])
        hits, _ = play(FrequencyBias(M, rng()), h)
        self.assertGreater(hits[200:].mean(), 0.37)

    def test_frequency_bias_chance_on_uniform(self):
        hits, _ = play(FrequencyBias(M, rng()), HmacSha256(M, 1).hops(4000))
        self.assertLess(hits.mean(), 0.1)

    def test_reactive_hits_repeats_only(self):
        hits, _ = play(ReactiveDelay(M, rng(), delay=1), ConstantChannel(M, 1).hops(100))
        self.assertTrue(hits[1:].all())
        hits, _ = play(ReactiveDelay(M, rng(), delay=2), np.tile([1, 2], 50))
        self.assertTrue(hits[2:].all())
        hits, _ = play(ReactiveDelay(M, rng(), delay=1), CounterMod(M, 1).hops(200))
        self.assertEqual(hits[1:].sum(), 0)

    def test_sweep_covers_every_channel_once_per_cycle(self):
        a = Sweep(M, rng(3)); seen = []
        for _ in range(M):
            seen.append(a.jam_set()[0]); a.observe(0)
        self.assertEqual(sorted(seen), list(range(M)))

    def test_sweep_locks_onto_stride_one(self):
        a = Sweep(M, rng(3)); start = a.jam_set()[0]
        h = [(start + t) % M for t in range(50)]
        hits, _ = play(Sweep(M, rng(3)), h)
        self.assertTrue(hits.all())

    def test_random_jammer_rate(self):
        hits, _ = play(RandomJammer(M, rng(2)), ConstantChannel(M, 1).hops(5200))
        self.assertAlmostEqual(hits.mean(), 1 / M, delta=0.02)

    def test_partial_band_geometry_and_relocation(self):
        a = PartialBand(M, rng(1), width=4, move_every=10)
        js = a.jam_set()
        self.assertEqual(len(js), 4); self.assertEqual(len(set(js)), 4)
        self.assertTrue(all((js[i + 1] - js[i]) % M == 1 for i in range(3)))
        starts = set()
        for _ in range(100):
            starts.add(a.jam_set()[0]); a.observe(0)
        self.assertGreater(len(starts), 3)
        self.assertEqual(a.k_eff, 4)

    def test_follower_defeats_arithmetic_progression(self):
        hits, _ = play(Follower(M, rng()), CounterMod(M, 7).hops(100))
        self.assertTrue(hits[3:].all())
        self.assertEqual(Follower.k_eff, 3)
        self.assertEqual(len(Follower(M, rng()).jam_set()), 3)

    def test_follower_near_chance_on_random(self):
        hits, _ = play(Follower(M, rng()), HmacSha256(M, 1).hops(5000))
        self.assertAlmostEqual(hits.mean(), 3 / M, delta=0.03)

    def test_registry(self):
        self.assertEqual(len(ATTACKERS), 11)
        kinds = [c.kind for c in ATTACKERS.values()]
        self.assertEqual(kinds.count("predictor"), 6); self.assertEqual(kinds.count("jammer"), 5)
        for c in ATTACKERS.values():
            a = c(M, rng())
            self.assertTrue(all(0 <= x < M for x in a.jam_set()))
