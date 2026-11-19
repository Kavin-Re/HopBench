import csv, json, os, tempfile, unittest
import xml.etree.ElementTree as ET
from hopbench import report
from hopbench.cli import main, grade
from hopbench.runner import SuiteConfig
from hopbench.schedules import BUILTIN_SCHEDULES, CONTROL_SCHEDULES, LFSR16, HmacSha256

CFG = SuiteConfig(trials=3, slots=300, warmup=50, rand_len=1500)
_CACHE = {}


def cards_meta():
    if not _CACHE:
        facs = {**BUILTIN_SCHEDULES, **CONTROL_SCHEDULES}
        _CACHE["v"] = grade(facs, CFG, jobs=2, progress=None)
    return _CACHE["v"]


class TestReport(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.cards, self.meta = cards_meta()
        self.b, self.c = list(BUILTIN_SCHEDULES), list(CONTROL_SCHEDULES)

    def p(self, n): return os.path.join(self.d.name, n)

    def test_leaderboard_csv(self):
        report.write_leaderboard(self.p("lb.csv"), self.cards, self.b, self.c)
        rows = list(csv.DictReader(open(self.p("lb.csv"))))
        self.assertEqual(len(rows), 9)
        ranked = [r for r in rows if r["kind"] == "builtin"]
        self.assertEqual(sorted(int(r["rank"]) for r in ranked), list(range(1, 8)))
        scores = [float(r["score"]) for r in sorted(ranked, key=lambda r: int(r["rank"]))]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 <= float(r["score"]) <= 100 for r in rows))
        self.assertTrue(all(r["rank"] == "" for r in rows if r["kind"] == "control"))
        for a in report.AXIS_ORDER:
            self.assertIn(f"axis_{a}_pct", rows[0])

    def test_detail_csv(self):
        report.write_detail(self.p("d.csv"), self.cards)
        rows = list(csv.DictReader(open(self.p("d.csv"))))
        self.assertEqual(len(rows), 9 * 11)

    def test_json_roundtrip_no_nan(self):
        report.write_json(self.p("s.json"), self.cards, self.meta)
        j = json.load(open(self.p("s.json")))
        self.assertEqual(set(j["scorecards"]), set(self.b + self.c))

    def test_svg_wellformed(self):
        report.radar_svg(self.p("r.svg"), self.cards, self.b + self.c)
        root = ET.parse(self.p("r.svg")).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        polys = [e for e in root.iter() if e.tag.endswith("polygon")]
        self.assertEqual(len(polys), 9 * 5)          # 4 rings + 1 data polygon each

    def test_png_optional(self):
        ok = report.radar_png(self.p("r.png"), self.cards, self.b + self.c)
        if ok:
            self.assertGreater(os.path.getsize(self.p("r.png")), 5000)

    def test_markdown_sections_and_caveats(self):
        report.write_markdown(self.p("r.md"), self.cards, self.b, self.c, self.meta, ["radar.svg"])
        t = open(self.p("r.md")).read()
        for s in ("## Leaderboard", "## Prediction accuracy vs chance", "## Throughput under each attacker", "## Randomness battery",
                  "## Caveats", "not a security proof", "![radar](radar.svg)"):
            self.assertIn(s, t)
        for n in self.b:
            self.assertIn(n, t)


class TestCli(unittest.TestCase):
    def test_list(self):
        self.assertEqual(main(["list"]), 0)

    def test_score_custom_schedule(self):
        with tempfile.TemporaryDirectory() as d:
            rc = main(["score", "examples.custom_schedule:WeakCounter", "examples.custom_schedule:HashOfCounter",
                       "--trials", "3", "--slots", "400", "--out", d])
            self.assertEqual(rc, 0)
            rows = list(csv.DictReader(open(os.path.join(d, "custom_detail.csv"))))
            self.assertGreater(len(rows), 20)
            card = json.load(open(os.path.join(d, "custom_scorecards.json")))["scorecards"]
            self.assertLess(card["Example: stride-5"]["score"], 40)
            self.assertGreater(card["Example: SHA256-counter"]["score"], 85)
            self.assertTrue(os.path.exists(os.path.join(d, "custom_radar.svg")))
