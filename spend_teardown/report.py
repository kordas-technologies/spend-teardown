"""Human-readable report, built from an export only (so it can be rebuilt from a shared file)."""

LEVERS = {
    "compaction": ("Auto-compact loops",
                   "Each compaction is an unlogged model call over the full context, and the next turn "
                   "rewrites the cache from scratch. Loops appear when the session floor sits close to the "
                   "auto-compact threshold.",
                   "Raise the auto-compact threshold well above the session floor, shrink the floor (M3), "
                   "or end long tasks with /clear plus a short handoff."),
    "cold_rebuild": ("Cold cache after a break",
                     "After the cache TTL (1 h, or 5 min) expires, the next turn pays cache-write price for "
                     "the whole context again.",
                     "Before breaks longer than the TTL, run /compact or /clear with a handoff; keep long "
                     "sessions on the 1-hour cache."),
    "session_floor": ("Session floor (MCP servers, CLAUDE.md, memory, hooks)",
                      "Every session and every subagent starts by loading the same tool definitions and "
                      "instructions, then re-reads them on every call.",
                      "Disconnect unused MCP servers and connectors, scope tools per project, trim CLAUDE.md "
                      "and memory files."),
    "context_bloat": ("Context bloat",
                      "Calls above 200k tokens of context re-read everything on every turn.",
                      "Compact earlier, split work into task-scoped sessions, keep large files out of the "
                      "conversation."),
    "expensive_subagents": ("Expensive model on subagent work",
                            "Subagents (search, lookups, exploration) running on the top-priced model.",
                            "Route subagents to a cheaper model by default; keep the top model for the main "
                            "thread and judgement calls."),
}


def _usd(x):
    return "$%s" % format(round(x or 0, 2), ",.2f")


def _tok(x):
    if x is None:
        return "n/a"
    return format(int(x), ",")


def render(export):
    t, p, m = export["totals"], export["projection"], export["mechanisms"]
    w = export["window"]
    lines = []
    lines.append("# Spend teardown")
    lines.append("")
    lines.append("Window: %s to %s (%s). Tool %s, methodology v%s. Prices: %s, as of %s." % (
        w["since"], w["until"], w.get("tz"), export["tool_version"], export["methodology_version"],
        export["pricing"]["source"], export["pricing"]["as_of"]))
    if export.get("members"):
        lines.append("Team export: %d members merged." % export["members"])
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append("| API calls | %s |" % _tok(t["calls"]))
    lines.append("| Sessions + subagent streams | %s |" % _tok(t["streams"]))
    lines.append("| Cost at list prices | %s |" % _usd(t["cost_usd"]))
    lines.append("| of which subagents | %s |" % _usd(t["subagent_cost_usd"]))
    lines.append("| Compactions | %s |" % _tok(t.get("compactions")))
    if t.get("median_floor") is not None:
        lines.append("| Session floor, median / p90 | %s / %s tokens |" % (_tok(t["median_floor"]), _tok(t["p90_floor"])))
    if t.get("median_ctx") is not None:
        lines.append("| Context per call, median / p90 | %s / %s tokens |" % (_tok(t["median_ctx"]), _tok(t["p90_ctx"])))
    lines.append("| **Projected saving** | **%.1f%% (%s)** |" % (p["projected_savings_pct"], _usd(p["projected_savings_usd"])))
    lines.append("")
    if p.get("low_confidence"):
        lines.append("> Low confidence: %.0f%% of tokens are on models missing from the pricing table. "
                     "Pass --pricing with your own rates." % (100 * p["unpriced_token_share"]))
        lines.append("")
    if t.get("unpriced_tokens_by_model"):
        lines.append("Unpriced models (tokens counted, not costed): " + ", ".join(
            "%s (%s tokens)" % (k, _tok(v)) for k, v in sorted(t["unpriced_tokens_by_model"].items())))
        lines.append("")
    if p["meets_gate"]:
        lines.append("The projection is at or above %.0f%%: the levers below are worth applying." % p["gate_pct"])
    else:
        lines.append("The projection is under %.0f%%. Your setup is already reasonably lean; a paid fix pack "
                     "is unlikely to pay for itself." % p["gate_pct"])
    lines.append("")
    lines.append("## Mechanisms, largest first")
    lines.append("")
    order = sorted(LEVERS, key=lambda k: -(p["per_mechanism_pct"].get(k) or 0))
    for k in order:
        title, why, lever = LEVERS[k]
        mk = m[k]
        lines.append("### %s — %.1f%% of spend addressable" % (title, p["per_mechanism_pct"].get(k) or 0))
        lines.append("")
        lines.append(why)
        lines.append("")
        lines.append(_facts(k, mk))
        lines.append("")
        lines.append("Lever: " + lever)
        lines.append("")
    lines.append("## How the projection is computed")
    lines.append("")
    lines.append("Each mechanism's saving uses a fixed, published assumption (METHODOLOGY.md). Savings overlap, "
                 "so they are combined as 1 − Π(1 − share), not added. Costs are list prices from the pricing "
                 "table, not your invoice.")
    lines.append("")
    lines.append("## Not measured")
    lines.append("")
    lines.append("- Subscription rate limits (Pro/Max): transcripts contain no meter for them, and vendors don't "
                 "publish how they are metered. This report makes no claim about limits.")
    lines.append("- Calls that are not written to transcripts (e.g. advisor or other side calls).")
    lines.append("- Other coding agents.")
    lines.append("")
    return "\n".join(lines)


def _facts(k, mk):
    if k == "compaction":
        return ("%s compactions (%s auto); %s streams in a loop (3+ auto within an hour); estimated cost %s." % (
            _tok(mk["events"]), _tok(mk["auto"]), _tok(mk["streams_with_loop"]), _usd(mk["cost_usd"])))
    if k == "cold_rebuild":
        return "%s cold rebuilds after a break, %s tokens rewritten, premium %s." % (
            _tok(mk["events"]), _tok(mk["rewrite_tokens"]), _usd(mk["premium_usd"]))
    if k == "session_floor":
        hist = ", ".join("%s: %s" % (b, n) for b, n in sorted((mk.get("floor_histogram") or {}).items()))
        return "%s of %s streams start above %s tokens. Floor distribution: %s." % (
            _tok(mk["streams_over_reference"]), _tok(mk["streams"]), _tok(mk["reference_tokens"]), hist or "n/a")
    if k == "context_bloat":
        return "%s calls above %s tokens of context; %s tokens above the line, cost %s." % (
            _tok(mk["events"]), _tok(mk["line_tokens"]), _tok(mk["bloat_tokens"]), _usd(mk["cost_usd"]))
    if k == "expensive_subagents":
        return "%s subagent calls on %s; cost %s vs %s on %s." % (
            _tok(mk["events"]), ", ".join(mk.get("expensive_models") or []) or "n/a", _usd(mk["cost_usd"]),
            _usd(mk["cost_at_cheapest_usd"]), mk.get("cheapest_model") or "n/a")
    return ""
