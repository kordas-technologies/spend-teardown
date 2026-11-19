import tempfile
import unittest

from spend_teardown.analysis import analyze, project
from spend_teardown.parser import scan
from spend_teardown.pricing import Pricing
from tests import fixtures as fx

PRICING = Pricing({"source": "test", "as_of": "test", "models": {
    "cheap": {"input": 1, "output": 5, "cache_write_1h": 2, "cache_write_5m": 1.25, "cache_read": 0.1},
    "big": {"input": 5, "output": 25, "cache_write_1h": 10, "cache_write_5m": 6.25, "cache_read": 0.5},
}})


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_lines(self, lines, rel=None):
        fx.write(self.root, rel or fx.session_file(), lines)
        return analyze(scan(self.root), PRICING, None)

    def test_floor_excess_priced_once_written_then_read(self):
        r = self.run_lines([
            fx.assistant(0, "a", model="big", cw1h=100_000),
            fx.assistant(1, "b", model="big", cr=100_000),
        ])
        m3 = r["mechanisms"]["session_floor"]
        excess = 100_000 + 10 - 40_000  # ctx includes input tokens
        self.assertEqual(m3["excess_tokens_total"], excess)
        expected = excess * 10 / 1e6 + excess * 0.5 / 1e6
        self.assertAlmostEqual(m3["saving_usd"], expected, places=4)

    def test_cold_rebuild_after_idle(self):
        r = self.run_lines([
            fx.assistant(0, "a", model="big", cw1h=60_000),
            fx.assistant(5, "b", model="big", cr=60_000),
            fx.assistant(5 + 61, "c", model="big", cw1h=60_000),   # 61 min idle, full rewrite
            fx.assistant(5 + 61 + 2, "d", model="big", cr=60_000),
        ])
        m2 = r["mechanisms"]["cold_rebuild"]
        self.assertEqual(m2["events"], 1)
        self.assertAlmostEqual(m2["premium_usd"], 60_000 * (10 - 0.5) / 1e6, places=4)
        self.assertAlmostEqual(m2["saving_usd"], m2["premium_usd"] * 0.5, places=4)

    def test_short_gap_on_5m_cache_counts_after_5_minutes(self):
        r = self.run_lines([
            fx.assistant(0, "a", model="big", cw5m=60_000),
            fx.assistant(7, "b", model="big", cw5m=60_000),
        ])
        self.assertEqual(r["mechanisms"]["cold_rebuild"]["events"], 1)

    def test_compaction_loop_and_excess(self):
        lines = [fx.assistant(0, "a", model="big", cw1h=50_000)]
        for i, t in enumerate((10, 20, 30, 40)):
            lines.append(fx.compact(t, "auto", pre=170_000))
            lines.append(fx.assistant(t + 1, "after%d" % i, model="big", cw1h=90_000))
        r = self.run_lines(lines)
        m1 = r["mechanisms"]["compaction"]
        self.assertEqual(m1["events"], 4)
        self.assertEqual(m1["streams_with_loop"], 1)
        per_event = 170_000 * 5 / 1e6 + 90_000 * (10 - 0.5) / 1e6
        self.assertAlmostEqual(m1["cost_usd"], 4 * per_event, places=4)
        self.assertAlmostEqual(m1["saving_usd"], 2 * per_event, places=4)
        # rewrites right after a compaction are not double counted as idle rebuilds
        self.assertEqual(r["mechanisms"]["cold_rebuild"]["events"], 0)

    def test_bloat(self):
        r = self.run_lines([fx.assistant(0, "a", model="cheap", cr=250_000)])
        m4 = r["mechanisms"]["context_bloat"]
        self.assertEqual(m4["events"], 1)
        self.assertEqual(m4["bloat_tokens"], 250_010 - 200_000)

    def test_expensive_subagents(self):
        lines = [fx.assistant(0, "main", model="cheap", out=1000),
                 fx.assistant(1, "sub", model="big", out=1000, sidechain=True)]
        r = self.run_lines(lines)
        m5 = r["mechanisms"]["expensive_subagents"]
        self.assertEqual(m5["events"], 1)
        self.assertEqual(m5["expensive_models"], ["big"])
        diff = (10 * 5 + 1000 * 25 - (10 * 1 + 1000 * 5)) / 1e6
        self.assertAlmostEqual(m5["saving_usd"], diff * 0.5, places=4)

    def _m5_with_tiny(self, tiny_calls):
        pricing = Pricing({"source": "test", "as_of": "test", "models": {
            "cheap": {"input": 1, "output": 5, "cache_write_1h": 2, "cache_write_5m": 1.25, "cache_read": 0.1},
            "big": {"input": 5, "output": 25, "cache_write_1h": 10, "cache_write_5m": 6.25, "cache_read": 0.5},
            "tiny": {"input": 0.1, "output": 0.5, "cache_write_1h": 0.2, "cache_write_5m": 0.125, "cache_read": 0.01},
        }})
        lines = [fx.assistant(i, "m%d" % i, model="cheap") for i in range(100 - tiny_calls)]
        lines += [fx.assistant(200 + i, "s%d" % i, model="big", out=1000, sidechain=True) for i in range(100)]
        lines += [fx.assistant(400 + i, "t%d" % i, model="tiny") for i in range(tiny_calls)]
        fx.write(self.root, fx.session_file(), lines)
        return analyze(scan(self.root), pricing, None)["mechanisms"]["expensive_subagents"]

    def test_m5_baseline_ignores_model_below_one_percent_of_calls(self):
        # methodology v3: 1 call of 200 (0.5%) on a cheaper model must not become the baseline
        m5 = self._m5_with_tiny(1)
        self.assertEqual(m5["cheapest_model"], "cheap")
        self.assertEqual(m5["expensive_models"], ["big"])
        self.assertEqual(m5["events"], 100)

    def test_m5_baseline_accepts_model_at_one_percent_of_calls(self):
        m5 = self._m5_with_tiny(2)  # 2 of 200 = 1%
        self.assertEqual(m5["cheapest_model"], "tiny")

    def test_unpriced_model_not_priced_as_other(self):
        r = self.run_lines([fx.assistant(0, "a", model="mystery-model", cr=1_000_000)])
        self.assertEqual(r["totals"]["cost_usd"], 0)
        self.assertIn("mystery-model", r["totals"]["unpriced_tokens_by_model"])
        self.assertTrue(r["projection"]["low_confidence"])

    def test_combined_projection_is_not_a_plain_sum(self):
        mech = {"a": {"saving_usd": 50.0}, "b": {"saving_usd": 50.0}}
        p = project(mech, 100.0, 1, 0)
        self.assertAlmostEqual(p["projected_savings_pct"], 75.0)
        self.assertTrue(p["meets_gate"])


if __name__ == "__main__":
    unittest.main()
