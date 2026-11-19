"""Pricing table: an input, never hard-coded. Unknown models stay unpriced."""

import json
import os

FIELDS = ("input", "output", "cache_write_1h", "cache_write_5m", "cache_read")
_DEFAULT = os.path.join(os.path.dirname(__file__), "data", "pricing.json")


class PricingError(ValueError):
    pass


def _rates(name, row):
    missing = [f for f in FIELDS if not isinstance(row.get(f), (int, float)) or isinstance(row.get(f), bool)]
    if missing:
        raise PricingError("model %r missing numeric fields: %s" % (name, ", ".join(missing)))
    return {f: float(row[f]) for f in FIELDS}


class Pricing:
    def __init__(self, table):
        if not isinstance(table, dict) or not isinstance(table.get("models"), dict):
            raise PricingError("pricing file must have a 'models' object")
        self.source = table.get("source", "unknown")
        self.as_of = table.get("as_of", "unknown")
        self.models = {}
        self.long = {}
        for name, row in table["models"].items():
            self.models[name] = _rates(name, row)
            lc = row.get("long_context")
            if lc is not None:
                # Prompt-length tier (Claude Haiku 5.5): a request whose prompt (input + cache
                # writes + cache reads) is over the threshold pays these rates for the whole request.
                thr = lc.get("over_prompt_tokens") if isinstance(lc, dict) else None
                if not isinstance(thr, int) or isinstance(thr, bool) or thr <= 0:
                    raise PricingError("model %r long_context needs a positive integer over_prompt_tokens" % name)
                self.long[name] = (thr, _rates(name + " long_context", lc))

    @classmethod
    def load(cls, path=None):
        with open(path or _DEFAULT, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def get(self, model):
        return self.models.get(model)

    def cost(self, model, inp=0, out=0, cw1h=0, cw5m=0, cr=0):
        """USD for one record, or None when the model is unpriced."""
        p = self.models.get(model)
        if p is None:
            return None
        tier = self.long.get(model)
        if tier is not None and inp + cw1h + cw5m + cr > tier[0]:
            p = tier[1]
        return (inp * p["input"] + out * p["output"] + cw1h * p["cache_write_1h"]
                + cw5m * p["cache_write_5m"] + cr * p["cache_read"]) / 1e6
