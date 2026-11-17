"""Pricing table: an input, never hard-coded. Unknown models stay unpriced."""

import json
import os

FIELDS = ("input", "output", "cache_write_1h", "cache_write_5m", "cache_read")
_DEFAULT = os.path.join(os.path.dirname(__file__), "data", "pricing.json")


class PricingError(ValueError):
    pass


class Pricing:
    def __init__(self, table):
        if not isinstance(table, dict) or not isinstance(table.get("models"), dict):
            raise PricingError("pricing file must have a 'models' object")
        self.source = table.get("source", "unknown")
        self.as_of = table.get("as_of", "unknown")
        self.models = {}
        for name, row in table["models"].items():
            missing = [f for f in FIELDS if not isinstance(row.get(f), (int, float))]
            if missing:
                raise PricingError("model %r missing numeric fields: %s" % (name, ", ".join(missing)))
            self.models[name] = {f: float(row[f]) for f in FIELDS}

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
        return (inp * p["input"] + out * p["output"] + cw1h * p["cache_write_1h"]
                + cw5m * p["cache_write_5m"] + cr * p["cache_read"]) / 1e6
