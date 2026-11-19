import hashlib, hmac, struct, unittest
import numpy as np
from hopbench.schedules import *
from hopbench.schedules import siphash24, lfsr16_step, xorshift16_step, LCG_A, LCG_C

M = 13
ALL = {**BUILTIN_SCHEDULES, **CONTROL_SCHEDULES}


class TestInterface(unittest.TestCase):
    def test_seven_builtins(self):
        self.assertEqual(len(BUILTIN_SCHEDULES), 7)
        self.assertEqual(set(BUILTIN_SCHEDULES), {"LFSR-16", "LCG-24", "Xorshift-16", "Counter-mod", "Table-32", "HMAC-SHA256", "SipHash-2-4"})

    def test_range_and_determinism(self):
        for n, cls in ALL.items():
            a, b = cls(M, 7), cls(M, 7)
            h = [a.next_channel(t) for t in range(300)]
            self.assertTrue(all(0 <= c < M and isinstance(c, int) for c in h), n)
            self.assertEqual(h, [b.next_channel(t) for t in range(300)], n)

    def test_random_access_matches_sequential(self):
        for n, cls in ALL.items():
            seq = cls(M, 3).hops(200)
            r = cls(M, 3)
            for t in (150, 3, 199, 0, 77):          # out-of-order access
                self.assertEqual(r.next_channel(t), seq[t], n)

    def test_other_channel_counts(self):
        for n, cls in BUILTIN_SCHEDULES.items():
            for m in (5, 16, 79):
                h = cls(m, 2).hops(100)
                self.assertTrue(h.min() >= 0 and h.max() < m, (n, m))

    def test_different_seeds_differ(self):
        for n, cls in BUILTIN_SCHEDULES.items():
            seqs = {tuple(cls(M, s).hops(40)) for s in range(10)}
            self.assertGreater(len(seqs), 5, n)

    def test_negative_slot_rejected(self):
        with self.assertRaises(ValueError):
            LFSR16(M, 1).next_channel(-1)

    def test_custom_plugin(self):
        class Mine(Schedule):
            name = "mine"
            def next_channel(self, slot):
                return (slot * 5) % self.M
        self.assertEqual(list(Mine(13).hops(5)), [0, 5, 10, 2, 7])


class TestGenerators(unittest.TestCase):
    def test_periods_65535(self):
        for f in (lfsr16_step, xorshift16_step):
            x, n = 1, 0
            while True:
                x = f(x); n += 1
                if x == 1:
                    break
            self.assertEqual(n, 65535, f.__name__)

    def test_lcg_full_period_conditions(self):       # Hull-Dobell for modulus 2^24
        self.assertEqual(LCG_C % 2, 1)
        self.assertEqual((LCG_A - 1) % 4, 0)

    def test_state_never_zero(self):
        for cls in (LFSR16, Xorshift16):
            for s in range(30):
                self.assertNotEqual(cls(M, s)._x, 0)

    def test_lfsr_hop_is_state_mod_m(self):
        s = LFSR16(M, 5)
        x = s._x
        for t in range(50):
            self.assertEqual(s.next_channel(t), x % M)
            x = lfsr16_step(x)

    def test_siphash_reference_vectors(self):
        key = bytes(range(16))
        self.assertEqual(siphash24(key, b""), 0x726FDB47DD0E0E31)
        self.assertEqual(siphash24(key, bytes(range(15))), 0xA129CA6149BE45E5)

    def test_hmac_matches_stdlib(self):
        s = HmacSha256(M, 9)
        d = hmac.new(s.key, (12).to_bytes(8, "big"), hashlib.sha256).digest()
        self.assertEqual(s.next_channel(12), int.from_bytes(d[:8], "big") % M)

    def test_sip_matches_function(self):
        s = SipHash24(M, 4)
        self.assertEqual(s.next_channel(5), siphash24(s.key, struct.pack("<Q", 5)) % M)

    def test_counter_mod_formula_and_full_cycle(self):
        s = CounterMod(M, 8)
        h = s.hops(26)
        self.assertEqual(list(h), [(s.a * t + s.b) % M for t in range(26)])
        self.assertEqual(sorted(h[:13]), list(range(13)))

    def test_table_period(self):
        h = TableSchedule(M, 1).hops(96)
        self.assertTrue(np.array_equal(h[:32], h[32:64]) and np.array_equal(h[:32], h[64:]))

    def test_uniformity_of_keyed(self):
        for cls in (HmacSha256, SipHash24):
            c = np.bincount(cls(M, 1).hops(13000), minlength=M)
            self.assertLess(abs(c - 1000).max(), 150, cls.name)

    def test_controls(self):
        self.assertEqual(len(set(ConstantChannel(M, 1).hops(50))), 1)
        self.assertGreater(len(set(IdealRandom(M, 1).hops(500))), 12)


if __name__ == "__main__":
    unittest.main()
