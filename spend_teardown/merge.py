"""Merge several private exports (one per developer) into one team export."""

import collections

from . import EXPORT_SCHEMA, METHODOLOGY_VERSION, __version__
from .analysis import project


class MergeError(ValueError):
    pass


_SUM_MECH = {
    "compaction": ("events", "auto", "manual_or_other", "streams_with_loop", "summarise_tokens_est",
                   "rewrite_tokens", "cost_usd", "saving_usd", "unpriced_events"),
    "cold_rebuild": ("events", "rewrite_tokens", "premium_usd", "saving_usd"),
    "session_floor": ("streams", "streams_over_reference", "excess_tokens_total", "saving_usd"),
    "context_bloat": ("events", "bloat_tokens", "cost_usd", "saving_usd"),
    "expensive_subagents": ("events", "cost_usd", "cost_at_cheapest_usd", "saving_usd"),
}


def validate(export, label="export"):
    if not isinstance(export, dict) or export.get("schema") != EXPORT_SCHEMA:
        raise MergeError("%s: unknown export schema %r (expected %r)" % (
            label, export.get("schema") if isinstance(export, dict) else None, EXPORT_SCHEMA))
    if export.get("methodology_version") != METHODOLOGY_VERSION:
        raise MergeError("%s: methodology v%s cannot be merged with v%s" % (
            label, export.get("methodology_version"), METHODOLOGY_VERSION))


def merge(exports):
    if not exports:
        raise MergeError("nothing to merge")
    for i, e in enumerate(exports):
        validate(e, "export #%d" % (i + 1))

    by_day = collections.defaultdict(lambda: collections.Counter())
    priced = {}
    by_project = []
    totals = collections.Counter()
    unpriced = collections.Counter()
    ctx_hist = collections.Counter()
    mech = {k: collections.Counter() for k in _SUM_MECH}
    floor_hist = collections.Counter()
    m5_models = set()
    cheapest = set()
    windows = []
    for i, e in enumerate(exports, 1):
        windows.append(e["window"])
        for row in e["by_day"]:
            key = (row["day"], row["model"])
            priced[key] = row["priced"]
            for k, v in row.items():
                if k not in ("day", "model", "priced"):
                    by_day[key][k] += v
        for row in e["by_project"]:
            by_project.append(dict(row, project="m%d:%s" % (i, row["project"])))
        t = e["totals"]
        for k in ("calls", "streams", "tokens", "cost_usd", "subagent_cost_usd", "compactions"):
            totals[k] += t.get(k) or 0
        unpriced.update(t.get("unpriced_tokens_by_model") or {})
        ctx_hist.update(t.get("ctx_histogram") or {})
        for name, fields in _SUM_MECH.items():
            m = e["mechanisms"][name]
            for f in fields:
                mech[name][f] += m.get(f) or 0
        floor_hist.update(e["mechanisms"]["session_floor"].get("floor_histogram") or {})
        m5 = e["mechanisms"]["expensive_subagents"]
        m5_models.update(m5.get("expensive_models") or [])
        if m5.get("cheapest_model"):
            cheapest.add(m5["cheapest_model"])

    mechanisms = {k: {f: round(v, 4) if isinstance(v, float) else v for f, v in c.items()} for k, c in mech.items()}
    mechanisms["session_floor"]["floor_histogram"] = dict(floor_hist)
    mechanisms["session_floor"]["reference_tokens"] = exports[0]["mechanisms"]["session_floor"]["reference_tokens"]
    mechanisms["context_bloat"]["line_tokens"] = exports[0]["mechanisms"]["context_bloat"]["line_tokens"]
    mechanisms["expensive_subagents"]["expensive_models"] = sorted(m5_models)
    mechanisms["expensive_subagents"]["cheapest_model"] = ",".join(sorted(cheapest)) or None

    total_cost = float(totals["cost_usd"])
    projection = project(mechanisms, total_cost, totals["tokens"], sum(unpriced.values()))
    return {
        "schema": EXPORT_SCHEMA,
        "tool_version": __version__,
        "methodology_version": METHODOLOGY_VERSION,
        "generated_on": max(e["generated_on"] for e in exports),
        "window": {"since": min(w["since"] for w in windows), "until": max(w["until"] for w in windows),
                   "tz": ",".join(sorted({str(w.get("tz")) for w in windows}))},
        "pricing": exports[0]["pricing"],
        "members": len(exports),
        "totals": {**{k: (round(v, 4) if isinstance(v, float) else v) for k, v in totals.items()},
                   "unpriced_tokens_by_model": dict(unpriced), "ctx_histogram": dict(ctx_hist),
                   "median_ctx": None, "p90_ctx": None, "median_floor": None, "p90_floor": None,
                   "cache_read_share": None},
        "by_day": [dict(day=k[0], model=k[1], priced=priced[k],
                        **{kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()})
                   for k, v in sorted(by_day.items())],
        "by_project": sorted(by_project, key=lambda r: -r["cost_usd"]),
        "mechanisms": mechanisms,
        "projection": projection,
    }
