# Sample teardown (our own team's data)

> This is a real teardown, built from Kordas Technologies' own Claude Code usage over two weeks in November 2026. It is exactly what you receive after you run `spend-teardown` and send us the export. Our own numbers are n=1: your mechanisms and shares will differ.

> **Corrected 2026-11-20 (tool 0.2.0).** The first version of this sample (tool 0.1.0, still at tag `v0.1.0`) priced Claude Sonnet 5.5 cache reads at $0.20/MTok. The vendor's list price was already $0.10 for the whole window ([Wayback Machine snapshot of the pricing page, 2026-10-08 04:32 UTC](https://web.archive.org/web/20261008043247/https://platform.claude.com/docs/en/about-claude/pricing)). That version also left Claude Haiku 5.5 unpriced. With the corrected table and methodology v3, spend in the window is $2,497 instead of $2,787, and the projected addressable share is 25.2% instead of 25.4%.

## Claude Code spend teardown: Kordas Technologies

Window 2026-11-03 to 2026-11-17 (14 days, UTC+0900), 636 sessions and subagents, 28,464 model calls. Prices: list API prices as of 2026-11-20. Tool 0.2.0, methodology v3 ([how it is computed](https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md)).

## Summary

- Spend in the window: **$2,497**, about **$5,350 per 30 days** at the same pace.
- Projected addressable share: **25.2%** (about $1,351 per 30 days). This is a projection from your counters, not a guarantee.
- Above our 20% threshold. The changes below are ranked by expected effect; the first two account for 19.4 of the 25.2 points.

Source for every figure below: your export, computed as in https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md.

| Mechanism | Share of spend addressable |
|---|---|
| Session floor: what every session and subagent loads before any work | 13.4% |
| Auto-compaction | 5.9% |
| Cold cache after a break | 4.1% |
| Large context per call | 2.9% |
| Subagents on the top-priced model | 1.4% |

## Changes, largest effect first

### 1. Session floor: what every session and subagent loads before any work

**Share of spend addressable:** 13.4% (from your export, https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md).

**What we measured.** 577 of 636 sessions/subagents (91%) start above 40k tokens. Median start 68k, 90th percentile 98k.

**Why it costs.** Tool definitions, MCP servers, CLAUDE.md files and memory are sent with every request of every session and subagent. Cache reads make each copy cheap, but the floor is multiplied by every call.

**Change.**

- Run `/context` in a typical session and list what loads ([docs](https://code.claude.com/docs/en/debug-your-config)).
- Turn off project MCP servers nobody uses in this repo with `disabledMcpjsonServers` ([docs](https://code.claude.com/docs/en/settings-reference#disabledmcpjsonservers)).
- Move rarely needed sections of CLAUDE.md into skills or linked docs, so they load on demand.
- For custom subagents that do narrow work, add `omitClaudeMd: true` to their frontmatter ([docs](https://code.claude.com/docs/en/sub-agents#supported-frontmatter-fields)).
- Keep MCP tool search on (the default) so tool definitions are deferred ([docs](https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search)).

**How to check it worked.** Median session start drops toward the 40k reference in the next scan; `streams_over_reference` falls.

**Trade-off.** Removing instructions can change behaviour; move them, don't delete them, and re-run a typical task.

### 2. Auto-compaction

**Share of spend addressable:** 5.9% (from your export, https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md).

**What we measured.** 185 compactions (126 automatic), 0 sessions with a loop (3+ automatic within an hour); estimated cost $332.

**Why it costs.** Each compaction is a full model call over the whole context, and the next turn rewrites the cache. Loops happen when the session floor sits close to the auto-compact threshold.

**Change.**

- No compaction loops found, so leave the window alone.
- End long tasks with `/clear` and a short handoff note instead of letting them compact repeatedly.

**How to check it worked.** `compaction.auto` and `streams_with_loop` drop in the next scan.

**Trade-off.** None for the window itself; `/clear` loses conversation state, so write the handoff first.

### 3. Cold cache after a break

**Share of spend addressable:** 4.1% (from your export, https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md).

**What we measured.** 221 times a session resumed after its cache expired and rewrote 41,309k tokens; premium $205. Cache writes are mostly 1-hour.

**Why it costs.** When a session sits idle past the cache lifetime, the next turn pays the write price for the whole context again.

**Change.**

- You already use the 1-hour cache. Before a longer break, run `/compact` or `/clear` with a handoff, so the cold restart rewrites a small context instead of a large one.

**How to check it worked.** `cold_rebuild.events` and `premium_usd` fall per active day.

**Trade-off.** Compacting before a break loses some detail of the conversation; keep the handoff note short but specific.

### 4. Large context per call

**Share of spend addressable:** 2.9% (from your export, https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md).

**What we measured.** 12,770 of 28,464 calls (45%) ran with more than 200k tokens of context. Median context 187k, 90th percentile 320k.

**Why it costs.** Every call re-reads the whole context. Long sessions that drift across tasks pay for old, irrelevant turns on every call.

**Change.**

- Start a new session per task; use `/clear` between unrelated tasks.
- Send wide searches to a subagent so only its summary returns to the main context.
- If compaction loops are absent (above), a lower `autoCompactWindow` caps context size at the cost of more compactions.

**How to check it worked.** Share of calls above 200k falls; median context falls.

**Trade-off.** More compactions if the window is lowered; check the compaction counter in the same scan.

### 5. Subagents on the top-priced model

**Share of spend addressable:** 1.4% (from your export, https://github.com/kordas-technologies/spend-teardown/blob/v0.2.0/METHODOLOGY.md).

**What we measured.** 248 subagent calls ran on claude-fable-5-1, costing $87.93; the same tokens on claude-sonnet-5-5 would cost $19.19.

**Why it costs.** Subagents inherit the main model unless told otherwise. Search, summarising and test runs rarely need the top tier.

**Change.**

- Set `model: sonnet` (or `haiku` for pure search) in the frontmatter of routine subagents ([docs](https://code.claude.com/docs/en/sub-agents#choose-a-model)).
- Or set the default for all unassigned subagents: `"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}`.
- Keep reviewers and planners on the top model if their output decides what ships.

**How to check it worked.** `expensive_subagents.cost_usd` falls; total subagent cost falls.

**Trade-off.** A cheaper model can miss things; keep one high-stakes reviewer on the top tier.

## Settings sketch (review before applying)

Project `.claude/settings.json`. Replace the placeholder and drop any line you don't want; apply one change at a time and re-run the scan after a week.

```json
{
  "disabledMcpjsonServers": [
    "<server you don't use here>"
  ],
  "env": {
    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"
  }
}
```

## Next step (optional)

In a client report this section offers the fix pack: ready-to-review changes for your repositories (`settings.json`, subagent frontmatter, a trimmed CLAUDE.md proposal and a short rollout note) in 3 business days, for $490, with a full refund if you are unhappy within 14 days. The teardown itself is free.

## Not measured

- Subscription (Pro/Max) rate limits: vendors don't publish how they are metered, so we make no claim about them.
- Output quality. Re-run a typical task after each change.
- Anything outside Claude Code's local usage metadata. We never saw your prompts, code or file paths.

---
AI-assisted, human-reviewed. Kordas Technologies. Settings names verified against the Claude Code docs on 2026-11-18.
