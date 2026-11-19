import math, unittest
import numpy as np
from hopbench.runner import *
from hopbench.scoring import *
from hopbench.scoring import adv_score, throughput_ref, verdict
from hopbench.schedules import *
from hopbench.attackers import ATTACKERS, ReactiveDelay, FrequencyBias

CFG = SuiteConfig(trials=4, slots=500, warmup=100, rand_len=2000)


class TestRunner(unittest.TestCase):
    def test_first_streak(self):
        h = np.array([0, 1, 1, 0, 1, 1, 1, 1, 0], bool)
        self.assertEqual(first_streak(h, 4), 4)
        self.assertIsNone(first_streak(h, 5))
        self.assertIsNone(first_streak(h[:2], 4))
        self.assertEqual(first_streak(np.ones(10, bool), 3), 0)

    def test_stream_uses_interface(self):
        self.assertEqual(list(stream(CounterMod(13, 1), 5)), list(CounterMod(13, 1).hops(5)))

    def test_trial_constant_channel(self):
        h = stream(ConstantChannel(13, 1), 400)
        r = run_attacker_trial(h, FrequencyBias, CFG, 1)
        self.assertEqual(r["hit"], 1.0)
        self.assertAlmostEqual(r["tput"], 1 - CFG.p_jam, delta=0.04)
        self.assertLessEqual(r["obs_break"], 3)

    def test_trial_jammer_has_no_obs_break(self):
        r = run_attacker_trial(stream(ConstantChannel(13, 1), 300), ReactiveDelay, CFG, 1)
        self.assertIsNone(r["obs_break"])

    def test_unjammed_throughput_matches_p_clean(self):
        h = np.arange(2000) % 13
        from hopbench.attackers import Attacker
        class Never(Attacker):
            def jam_set(self): return []
            def observe(self, ch): pass
        r = run_attacker_trial(h, Never, SuiteConfig(slots=2000), 3)
        self.assertAlmostEqual(r["tput"], 0.99, delta=0.01)

    def test_trial_seed_determinism(self):
        h = stream(LFSR16(13, 1), 300)
        a = run_attacker_trial(h, ATTACKERS["ngram"], CFG, 5)
        b = run_attacker_trial(h, ATTACKERS["ngram"], CFG, 5)
        self.assertEqual(a, b)

    def test_suite_shape_and_jobs_equivalence(self):
        facs = {"LFSR-16": LFSR16, "HMAC-SHA256": HmacSha256}
        cfg = SuiteConfig(trials=3, slots=300, warmup=50, rand_len=1500)
        r1 = run_suite(facs, cfg, jobs=1, progress=None)
        r2 = run_suite(facs, cfg, jobs=2, progress=None)
        for n in facs:
            self.assertEqual(set(r1[n]["attackers"]), set(ATTACKERS))
            for k in ATTACKERS:
                self.assertEqual(len(r1[n]["attackers"][k]["hit"]), 3)
            self.assertEqual(r1[n]["attackers"], r2[n]["attackers"])
            self.assertEqual(r1[n]["randomness"], r2[n]["randomness"])
        self.assertIn("seconds", r1["_meta"])

    def test_config_quick(self):
        self.assertLess(SuiteConfig.quick().trials, SuiteConfig().trials)
        self.assertEqual(SuiteConfig.quick(trials=3).trials, 3)


class TestScoring(unittest.TestCase):
    def test_adv_score_chance_and_perfect(self):
        s, info = adv_score([1 / 13] * 8, 1 / 13)
        self.assertEqual(s, 1.0)
        s, info = adv_score([1.0] * 8, 1 / 13)
        self.assertAlmostEqual(s, 0.0, places=9); self.assertTrue(info["significant"])

    def test_adv_score_noise_gets_benefit_of_doubt(self):
        r = np.random.default_rng(0)
        s, info = adv_score(r.normal(1 / 13, 0.004, 16), 1 / 13)
        self.assertGreater(s, 0.97)

    def test_adv_score_partial(self):
        s, _ = adv_score([0.25] * 8, 1 / 13)
        self.assertAlmostEqual(s, 1 - math.log2(0.25 * 13) / math.log2(13), places=6)

    def test_throughput_ref(self):
        self.assertAlmostEqual(throughput_ref(1, 13, 0.95, 0.01), 1 - 0.95 / 13 - 12 / 13 * 0.01)

    def test_weights_documented(self):
        self.assertEqual(sum(a[1] for a in AXES.values()), 100)
        used = {m for a in AXES.values() for m in a[2]}
        self.assertEqual(used, set(ATTACKERS))                     # every attacker feeds an axis
        self.assertTrue(set(GATE_AXES) <= set(AXES))

    def test_verdict_bands(self):
        self.assertEqual([verdict(x) for x in (95, 80, 50, 10)],
                         ["no weakness found by this suite", "minor weakness", "exploitable", "broken"])

    def test_scorecard_ordering(self):
        facs = {"constant": ConstantChannel, "LFSR-16": LFSR16, "Counter-mod": CounterMod, "ideal": IdealRandom, "HMAC": HmacSha256}
        res = run_suite(facs, CFG, progress=None); res.pop("_meta")
        cards = {n: scorecard(res[n], CFG.as_dict()) for n in facs}
        sc = {n: c["score"] for n, c in cards.items()}
        self.assertLess(sc["constant"], 15)
        self.assertLess(sc["LFSR-16"], 40); self.assertLess(sc["Counter-mod"], 40)
        self.assertGreater(sc["ideal"], 90); self.assertGreater(sc["HMAC"], 90)
        for c in cards.values():
            self.assertTrue(0 <= c["score"] <= 100)
            self.assertTrue(all(0 <= v <= 1 for v in c["axes"].values()))
        self.assertLess(cards["LFSR-16"]["axes"]["state_recovery"], 0.2)
        self.assertGreater(cards["LFSR-16"]["axes"]["linear_complexity"], 0.9)    # BM does not break mod-13 LFSR
        self.assertLess(cards["Counter-mod"]["axes"]["follower"], 0.1)
        self.assertLess(cards["Counter-mod"]["axes"]["linear_complexity"], 0.5)
        self.assertEqual(cards["LFSR-16"]["attackers"]["lfsr_breaker"]["broken_fraction"], 1.0)
        self.assertTrue(cards["HMAC"]["attackers"]["lfsr_breaker"]["obs_break_median"] is None)

    def test_gate_prevents_averaging_away(self):
        res = run_suite({"LFSR-16": LFSR16}, CFG, progress=None); res.pop("_meta")
        c = scorecard(res["LFSR-16"], CFG.as_dict())
        self.assertGreater(c["weighted_mean"], 0.6)       # most axes look fine ...
        self.assertLess(c["score"], 35)                     # ... but one cheap full break caps the score

    def test_subset_of_attackers(self):
        cfg = SuiteConfig(trials=3, slots=300, warmup=50, rand_len=1500, attackers=["ngram", "frequency_bias", "sweep"])
        res = run_suite({"T": TableSchedule}, cfg, progress=None); res.pop("_meta")
        c = scorecard(res["T"], cfg.as_dict())
        self.assertTrue(math.isfinite(c["score"]))
        self.assertTrue(math.isnan(c["axes"]["follower"]))
