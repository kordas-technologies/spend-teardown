"""Mechanism detectors M1-M5 and the combined projection (METHODOLOGY.md v1).

Everything returned here is still keyed by raw project slug; export.py replaces
slugs with salted hashes before anything leaves the machine.
"""

import bisect
import collections
import datetime as dt

# Frozen constants, METHODOLOGY v1
LOOP_MIN_AUTO = 3
LOOP_SPAN_S = 3600
FREE_COMPACTIONS_PER_STREAM = 2
COLD_MIN_CTX = 20_000
COLD_MIN_WRITE_SHARE = 0.5
TTL_1H_S = 3600
TTL_5M_S = 300
COLD_AVOIDABLE = 0.5
FLOOR_REFERENCE = 40_000
BLOAT_LINE = 200_000
BLOAT_AVOIDABLE = 0.5
EXPENSIVE_RATIO = 3.0
ROUTING_ADOPTION = 0.5
GATE_PCT = 20.0
LOW_CONFIDENCE_UNPRICED_SHARE = 0.10

FLOOR_BUCKETS = [(20_000, "<20k"), (40_000, "20-40k"), (60_000, "40-60k"), (80_000, "60-80k"),
                 (100_000, "80-100k"), (150_000, "100-150k"), (10**15, ">=150k")]
CTX_BUCKETS = [(50_000, "<50k"), (100_000, "50-100k"), (150_000, "100-150k"), (200_000, "150-200k"),
               (300_000, "200-300k"), (500_000, "300-500k"), (10**15, ">=500k")]


def bucket(value, buckets):
    for lim, name in buckets:
        if value < lim:
            return name
    return buckets[-1][1]


def day_of(ts, tz):
    return dt.datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d") if tz else \
        dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")


def _write_premium(p, cw1h, cw5m):
    """Extra cost of writing tokens to cache instead of reading them from cache."""
    return (cw1h * (p["cache_write_1h"] - p["cache_read"]) + cw5m * (p["cache_write_5m"] - p["cache_read"])) / 1e6


def _round(x, nd=4):
    return round(x, nd)


def analyze(scan, pricing, tz=None):
    calls = scan.calls
    by_stream = collections.defaultdict(list)
    for c in calls:
        by_stream[c.stream].append(c)
    comp_by_stream = collections.defaultdict(list)
    for k in scan.compactions:
        comp_by_stream[k.stream].append(k)

    # ---- totals --------------------------------------------------------------
    by_day = collections.defaultdict(lambda: dict(calls=0, input=0, cache_write_1h=0, cache_write_5m=0,
                                                  cache_read=0, output=0, cost_usd=0.0))
    by_project = collections.defaultdict(lambda: dict(calls=0, cost_usd=0.0, streams=set()))
    unpriced = collections.Counter()
    total_cost = 0.0
    total_tokens = 0
    side_cost = 0.0
    ctx_hist = collections.Counter()
    ctx_sum = cr_sum = 0
    for c in calls:
        cost = pricing.cost(c.model, c.inp, c.out, c.cw1h, c.cw5m, c.cr)
        toks = c.ctx + c.out
        total_tokens += toks
        row = by_day[(day_of(c.ts, tz), c.model)]
        row["calls"] += 1
        row["input"] += c.inp
        row["cache_write_1h"] += c.cw1h
        row["cache_write_5m"] += c.cw5m
        row["cache_read"] += c.cr
        row["output"] += c.out
        pr = by_project[c.project]
        pr["calls"] += 1
        pr["streams"].add(c.stream)
        if cost is None:
            unpriced[c.model] += toks
        else:
            row["cost_usd"] += cost
            pr["cost_usd"] += cost
            total_cost += cost
            if c.side:
                side_cost += cost
        ctx_hist[bucket(c.ctx, CTX_BUCKETS)] += 1
        ctx_sum += c.ctx
        cr_sum += c.cr

    # ---- M1 compaction -------------------------------------------------------
    m1 = dict(events=0, auto=0, manual_or_other=0, streams_with_loop=0, summarise_tokens_est=0,
              rewrite_tokens=0, cost_usd=0.0, saving_usd=0.0, unpriced_events=0)
    for stream, comps in comp_by_stream.items():
        sc = by_stream.get(stream, [])
        ts_list = [c.ts for c in sc]
        dominant = collections.Counter(c.model for c in sc).most_common(1)
        dom_p = pricing.get(dominant[0][0]) if dominant else None
        autos = sorted(k.ts for k in comps if k.trigger == "auto")
        for i in range(len(autos)):
            j = bisect.bisect_right(autos, autos[i] + LOOP_SPAN_S)
            if j - i >= LOOP_MIN_AUTO:
                m1["streams_with_loop"] += 1
                break
        for n, k in enumerate(sorted(comps, key=lambda x: x.ts)):
            m1["events"] += 1
            if k.trigger == "auto":
                m1["auto"] += 1
            else:
                m1["manual_or_other"] += 1
            m1["summarise_tokens_est"] += k.pre_tokens
            cost = 0.0
            if dom_p is not None:
                cost += k.pre_tokens * dom_p["input"] / 1e6
            else:
                m1["unpriced_events"] += 1
            idx = bisect.bisect_right(ts_list, k.ts)
            if idx < len(sc):
                nxt = sc[idx]
                m1["rewrite_tokens"] += nxt.cw
                p = pricing.get(nxt.model)
                if p is not None:
                    cost += _write_premium(p, nxt.cw1h, nxt.cw5m)
            m1["cost_usd"] += cost
            if n >= FREE_COMPACTIONS_PER_STREAM:
                m1["saving_usd"] += cost

    # ---- M2 cold rebuild after idle / M3 floor / M4 bloat --------------------
    m2 = dict(events=0, rewrite_tokens=0, premium_usd=0.0, saving_usd=0.0)
    m3 = dict(streams=0, streams_over_reference=0, excess_tokens_total=0, saving_usd=0.0,
              floor_histogram=collections.Counter(), reference_tokens=FLOOR_REFERENCE)
    m4 = dict(events=0, bloat_tokens=0, cost_usd=0.0, saving_usd=0.0, line_tokens=BLOAT_LINE)
    for stream, sc in by_stream.items():
        comp_ts = sorted(k.ts for k in comp_by_stream.get(stream, []))
        first = sc[0]
        floor = first.ctx
        m3["streams"] += 1
        m3["floor_histogram"][bucket(floor, FLOOR_BUCKETS)] += 1
        excess = max(0, floor - FLOOR_REFERENCE)
        if excess:
            m3["streams_over_reference"] += 1
            m3["excess_tokens_total"] += excess
            p0 = pricing.get(first.model)
            if p0 is not None:
                wp = p0["cache_write_1h"] if first.cw1h >= first.cw5m else p0["cache_write_5m"]
                m3["saving_usd"] += excess * wp / 1e6
            for c in sc[1:]:
                p = pricing.get(c.model)
                if p is not None:
                    m3["saving_usd"] += excess * p["cache_read"] / 1e6
        prev = None
        for c in sc:
            p = pricing.get(c.model)
            if c.ctx > BLOAT_LINE:
                m4["events"] += 1
                bt = c.ctx - BLOAT_LINE
                m4["bloat_tokens"] += bt
                if p is not None:
                    m4["cost_usd"] += bt * p["cache_read"] / 1e6
            if prev is not None:
                gap = c.ts - prev.ts
                ttl = TTL_5M_S if (prev.cw5m > 0 and prev.cw1h == 0) else TTL_1H_S
                after_compaction = any(prev.ts <= t < c.ts for t in comp_ts[bisect.bisect_left(comp_ts, prev.ts):][:1])
                if (gap > ttl and not after_compaction and c.ctx >= COLD_MIN_CTX
                        and c.cw >= COLD_MIN_WRITE_SHARE * c.ctx):
                    m2["events"] += 1
                    m2["rewrite_tokens"] += c.cw
                    if p is not None:
                        m2["premium_usd"] += _write_premium(p, c.cw1h, c.cw5m)
            prev = c
    m2["saving_usd"] = m2["premium_usd"] * COLD_AVOIDABLE
    m4["saving_usd"] = m4["cost_usd"] * BLOAT_AVOIDABLE

    # ---- M5 expensive model on subagent work ---------------------------------
    seen_priced = {c.model: pricing.get(c.model) for c in calls if pricing.get(c.model) is not None}
    m5 = dict(events=0, cost_usd=0.0, cost_at_cheapest_usd=0.0, saving_usd=0.0, cheapest_model=None,
              expensive_models=[])
    positive = {m: p for m, p in seen_priced.items() if p["output"] > 0}
    if positive:
        cheapest = min(positive, key=lambda m: positive[m]["output"])
        cp = positive[cheapest]
        expensive = sorted(m for m, p in positive.items() if p["output"] >= EXPENSIVE_RATIO * cp["output"])
        m5["cheapest_model"] = cheapest
        m5["expensive_models"] = expensive
        for c in calls:
            if c.side and c.model in expensive:
                m5["events"] += 1
                m5["cost_usd"] += pricing.cost(c.model, c.inp, c.out, c.cw1h, c.cw5m, c.cr)
                m5["cost_at_cheapest_usd"] += pricing.cost(cheapest, c.inp, c.out, c.cw1h, c.cw5m, c.cr)
        m5["saving_usd"] = max(0.0, m5["cost_usd"] - m5["cost_at_cheapest_usd"]) * ROUTING_ADOPTION

    mechanisms = dict(compaction=m1, cold_rebuild=m2, session_floor=m3, context_bloat=m4, expensive_subagents=m5)
    projection = project(mechanisms, total_cost, total_tokens, sum(unpriced.values()))

    for m in mechanisms.values():
        for k, v in list(m.items()):
            if isinstance(v, float):
                m[k] = _round(v)
            elif isinstance(v, collections.Counter):
                m[k] = dict(v)

    floors = sorted(sc[0].ctx for sc in by_stream.values())
    ctxs = sorted(c.ctx for c in calls)
    return dict(
        by_day=[dict(day=k[0], model=k[1], priced=pricing.get(k[1]) is not None,
                     **{kk: (_round(vv) if isinstance(vv, float) else vv) for kk, vv in v.items()})
                for k, v in sorted(by_day.items())],
        by_project={slug: dict(calls=v["calls"], streams=len(v["streams"]), cost_usd=_round(v["cost_usd"]))
                    for slug, v in by_project.items()},
        totals=dict(calls=len(calls), streams=len(by_stream), tokens=total_tokens, cost_usd=_round(total_cost),
                    subagent_cost_usd=_round(side_cost), unpriced_tokens_by_model=dict(unpriced),
                    cache_read_share=_round(cr_sum / ctx_sum, 4) if ctx_sum else None,
                    median_ctx=_pct(ctxs, 50), p90_ctx=_pct(ctxs, 90),
                    median_floor=_pct(floors, 50), p90_floor=_pct(floors, 90),
                    ctx_histogram=dict(ctx_hist), compactions=len(scan.compactions)),
        mechanisms=mechanisms,
        projection=projection,
    )


def _pct(sorted_values, q):
    if not sorted_values:
        return None
    i = min(len(sorted_values) - 1, int(round(q / 100 * (len(sorted_values) - 1))))
    return sorted_values[i]


def project(mechanisms, total_cost, total_tokens, unpriced_tokens):
    shares = {}
    remaining = 1.0
    for name, m in mechanisms.items():
        s = min(m.get("saving_usd", 0.0) / total_cost, 1.0) if total_cost > 0 else 0.0
        shares[name] = _round(100 * s, 2)
        remaining *= (1 - s)
    pct = _round(100 * (1 - remaining), 2) if total_cost > 0 else 0.0
    unpriced_share = unpriced_tokens / total_tokens if total_tokens else 0.0
    return dict(
        projected_savings_pct=pct,
        projected_savings_usd=_round(total_cost * pct / 100),
        per_mechanism_pct=shares,
        gate_pct=GATE_PCT,
        meets_gate=pct >= GATE_PCT,
        low_confidence=unpriced_share > LOW_CONFIDENCE_UNPRICED_SHARE or total_cost == 0,
        unpriced_token_share=_round(unpriced_share, 4),
    )
