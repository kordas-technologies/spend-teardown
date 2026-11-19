# Methodology (pre-registered)

Version: methodology v3 (v1 frozen 2026-11-17, **before any third-party data was analysed**; v2 changes how one record is priced, v3 changes how M5 picks its baseline model; see the changelog. No third-party data had been analysed before v2 or v3 either).
Any change to a formula or constant below bumps the methodology version and is
recorded in the changelog at the bottom. Reports always print the methodology
version they were computed with.

The point of freezing this up front: our go/no-go gate ("at least 3 of 5 teams
show a projected saving of 20% or more") and the pre-purchase advice ("if the CLI
shows under 20%, don't buy") both rest on numbers this tool computes. If we tuned
the formulas after seeing a team's data, the gate would measure our formulas,
not their waste.

## Inputs

- Claude Code session transcripts (`~/.claude/projects/**/*.jsonl`, including
  `subagents/` files). Only these fields are read: `type`, `subtype`,
  `timestamp`, `isSidechain`, `isCompactSummary`, `compactMetadata.trigger`,
  `compactMetadata.preTokens`, `message.id`, `message.model`, `message.usage`.
  Message contents are never read.
- A pricing table (USD per million tokens: input, output, 1h cache write,
  5m cache write, cache read). The bundled table has a `source` and `as_of`
  field. Models missing from the table are counted in tokens and reported as
  **unpriced**; they are never silently priced as another model.
  A model row may carry a `long_context` block with `over_prompt_tokens`
  (Claude Haiku 5.5: 100,000). A record whose prompt (input + cache writes +
  cache reads) is over that number is priced entirely at the `long_context`
  rates, as the vendor bills it. M5 picks the expensive tier and the baseline
  model from the base rates; its saving re-prices each call with this rule,
  tier included.

## Record selection

1. Window: a usage record is in scope when its line `timestamp` falls in
   `[since, until)` in the chosen timezone. File modification time is only used
   to skip files last modified before the window start (such a file cannot hold
   records inside the window).
2. Global de-duplication: records are keyed by `message.id` (fallback
   `requestId`, then `uuid`). Within one file the last streamed line for a key
   wins (it carries the final `output_tokens`). A key already seen in an
   earlier-processed file is skipped, because resumed and forked sessions copy
   earlier records into new files. Files are processed in ascending mtime order.
3. `model == "<synthetic>"` records are excluded (no API call).
4. Schema check by **field shape**, not by Claude Code version string: a usage
   record must have `timestamp`, `message.model` and integer
   `usage.input_tokens` and `usage.output_tokens`. If more than 1% of records
   carrying `usage` fail the shape check, or zero records pass, the run stops
   with exit code 3 ("unknown transcript schema").

Definitions per API call: `ctx = input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`; `fresh = input_tokens + cache_creation_input_tokens`.
A *session stream* is one transcript file (a subagent file is its own stream).

## Mechanisms (detectors)

Each detector reports: events, tokens involved, cost involved (USD), and a
projected saving (USD) under the stated assumption. Constants are fixed here.

### M1 — Compaction (auto-compact loops)

- Event: a `system` line with `subtype == "compact_boundary"`.
  (`isCompactSummary` user lines mark the same event and are not counted again.)
- Cost of one compaction =
  (a) the summarisation call, which is **not** logged in the transcript:
  estimated as `compactMetadata.preTokens` × input price of the stream's
  dominant model (label: *estimated*), plus
  (b) the cold rewrite on the first API call after the boundary:
  `cache_creation_input_tokens` × (cache-write price − cache-read price).
- Loop: a stream with 3 or more `auto` compactions within any 60-minute span.
- **Projected saving:** for each stream, compactions beyond the 2nd are treated
  as avoidable (lever: raise the auto-compact threshold above the session floor,
  shrink the floor, or `/clear` with a handoff). Saving = cost of those excess
  compactions.

### M2 — Cold cache rebuild after idle

- Event: an API call with `ctx >= 20,000`, `cache_creation_input_tokens >=
  0.5 × ctx`, where the gap since the previous API call in the same stream is
  more than 3,600 s (1h cache) — or more than 300 s when that call wrote only
  5-minute cache. First calls of a stream and first calls after a compaction
  are excluded (they belong to M3 and M1).
- Premium = `cache_creation_input_tokens` × (write price − read price).
- **Projected saving:** 50% of the premium (assumption: half of long breaks can
  be preceded by `/compact` or `/clear` + handoff, which shrinks the rewrite).

### M3 — Session floor (MCP servers, CLAUDE.md, memory, hooks)

- Floor of a stream = `ctx` of its first API call.
- Reference floor: **40,000 tokens** (bare Claude Code system prompt plus a
  modest project configuration).
- Excess per stream = max(0, floor − 40,000).
- **Projected saving:** for every stream, excess × cache-write price (paid once
  when the stream starts) + excess × cache-read price × (number of later calls
  in the stream). Lever: disconnect unused MCP servers and connectors, trim
  `CLAUDE.md` and memory, scope tools per project.

### M4 — Context bloat

- Event: an API call with `ctx > 200,000`.
- Bloat tokens = `ctx − 200,000` for those calls, priced at cache-read.
- **Projected saving:** 50% of the bloat cost (assumption: earlier compaction or
  task-scoped sessions keep half of these calls under the line).

### M5 — Expensive model on routine work

- Baseline model = the cheapest priced model (output price > 0) that carries
  **at least 1% of all API calls in the window** (v3). If no priced model
  reaches 1%, the cheapest priced model seen in the window is used.
- Expensive tier = priced models seen in the window whose output price is at
  least 3× the baseline model's (computed from the pricing table, not from
  model names). The 1% rule applies only to the baseline, not to this tier.
- Event: a **subagent / sidechain** call on an expensive-tier model.
- **Projected saving:** 50% adoption × (cost of those calls − the same tokens
  priced at the baseline model).

## Combined projection

Savings overlap (floor reads also show up as bloat, etc.). Combined share:

    combined = 1 − Π_i (1 − min(saving_i / total_cost, 1))

`projected_savings_pct = 100 × combined`. The gate threshold is **20%**.
Unpriced tokens are excluded from `total_cost` and listed separately; if
unpriced tokens exceed 10% of all tokens the projection is marked
`low_confidence`.

## What this tool does not measure

- Subscription rate limits (Pro/Max). Transcripts contain no meter for them, and
  vendors don't publish how they are metered. No claim is made about limits.
- Advisor / side calls that are not written to transcripts.
- Cursor, Copilot and other agents (no local usage data).

## Changelog

- v1 (2026-11-17): initial frozen version.
- v2 (2026-11-20, released in 0.2.0 together with v3): prompt-length pricing tier (`long_context`)
  in the cost of one record. No detector formula or constant changed. Bundled
  table refreshed the same day (adds Claude Fable 5.2, Claude Haiku 5.5 and
  older rows; Claude Sonnet 5.5 cache read 0.2 → 0.10 USD/MTok). Note for M5:
  once Claude Haiku 5.5 is priced, any window that contains even a few Haiku 5.5
  calls makes it the cheapest model, so M5 re-prices subagent calls at Haiku
  rates. On our own 3–17.11 data that moves M5 from 1.2% to 8.7% of spend.
  The v0.1.0 cache-read row for Claude Sonnet 5.5 (0.2) was already out of
  date when the table was dated 2026-10-08: the vendor page shows 0.20 in the
  Wayback Machine snapshot 20261007211533 and 0.10 in 20261008043247.
- v3 (2026-11-20, released in 0.2.0 together with v2): M5 baseline model must
  carry at least 1% of the window's API calls (`BASELINE_MIN_CALL_SHARE`).
  Reason: under v2 a model with 0.79% of calls (Claude Haiku 5.5, 224 of
  28,464 calls in our own 3–17.11 window) became the price yardstick for all
  subagent work, which turned a model nobody uses for that work into the
  recommendation. On the same data M5 is 1.38% of spend under v3 (baseline
  Claude Sonnet 5.5, expensive tier Claude Fable 5.1). Exports from v1 or v2
  cannot be merged with v3.
