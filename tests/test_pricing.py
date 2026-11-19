import unittest

from spend_teardown.pricing import Pricing, PricingError

BASE = {"input": 0.10, "output": 0.50, "cache_write_1h": 0.20, "cache_write_5m": 0.125, "cache_read": 0.01}
LONG = {"input": 0.50, "output": 2.50, "cache_write_1h": 1.0, "cache_write_5m": 0.625, "cache_read": 0.05}


def tiered(**extra):
    lc = dict(LONG, over_prompt_tokens=100000)
    lc.update(extra)
    return Pricing({"models": {"h": dict(BASE, long_context=lc)}})


class PricingTests(unittest.TestCase):
    def test_threshold_is_strictly_over(self):
        p = tiered()
        # exactly 100,000 prompt tokens: base rates (the page says "over 100,000")
        self.assertAlmostEqual(p.cost("h", inp=100000, out=1000), (100000 * 0.10 + 1000 * 0.50) / 1e6)
        # 100,001: long-context rates for the whole request, output included
        self.assertAlmostEqual(p.cost("h", inp=100001, out=1000), (100001 * 0.50 + 1000 * 2.50) / 1e6)

    def test_prompt_counts_cache_reads_and_writes(self):
        p = tiered()
        c = p.cost("h", inp=10, cw5m=40000, cw1h=10000, cr=50000)  # 100,010 prompt tokens
        self.assertAlmostEqual(c, (10 * 0.50 + 40000 * 0.625 + 10000 * 1.0 + 50000 * 0.05) / 1e6)

    def test_output_does_not_count_toward_prompt(self):
        p = tiered()
        self.assertAlmostEqual(p.cost("h", inp=99000, out=50000), (99000 * 0.10 + 50000 * 0.50) / 1e6)

    def test_get_returns_base_rates(self):
        self.assertEqual(tiered().get("h")["input"], 0.10)

    def test_bad_long_context_rejected(self):
        with self.assertRaises(PricingError):
            tiered(over_prompt_tokens=0)
        with self.assertRaises(PricingError):
            tiered(over_prompt_tokens="100000")
        with self.assertRaises(PricingError):
            Pricing({"models": {"h": dict(BASE, long_context={"over_prompt_tokens": 100000})}})

    def test_untiered_model_unchanged(self):
        p = Pricing({"models": {"m": BASE}})
        self.assertAlmostEqual(p.cost("m", inp=500000), 500000 * 0.10 / 1e6)

    def test_bundled_table_loads_and_has_current_models(self):
        p = Pricing.load()
        for m in ("claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
            self.assertIsNotNone(p.get(m), m)
        self.assertEqual(p.get("claude-sonnet-5-5")["cache_read"], 0.10)
        self.assertIn("claude-haiku-5-5", p.long)


if __name__ == "__main__":
    unittest.main()
