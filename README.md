# spend-teardown

**ccusage tells you *how much* your coding agent spent. spend-teardown tells you *why*, and what to change.**

A local, offline command-line tool that reads the usage metadata in your Claude Code
session transcripts and names the mechanisms behind the bill:

| Mechanism | What it looks like |
|---|---|
| M1 Auto-compact loops | the session floor sits near the auto-compact threshold, so sessions compact over and over; every compaction is an unlogged call over the full context plus a cold cache rewrite |
| M2 Cold cache after a break | after the 1-hour (or 5-minute) cache expires, the whole context is written again at cache-write price |
| M3 Session floor | MCP servers, connectors, `CLAUDE.md`, memory and hooks loaded into every session and every subagent, then re-read on every call |
| M4 Context bloat | calls above 200k tokens of context |
| M5 Expensive model on subagent work | lookups and exploration running on the top-priced model |

It prints a report with a projected saving per mechanism and a combined projection.
All formulas and constants are fixed in [METHODOLOGY.md](METHODOLOGY.md), versioned, and
were committed before the tool saw any third-party data.

See [SAMPLE_TEARDOWN.md](SAMPLE_TEARDOWN.md) for a full written teardown built from our own team's usage.

## How it compares

| | ccusage and similar | spend-teardown |
|---|---|---|
| Totals per day / model / session | yes | yes (day buckets) |
| Names the mechanism behind the spend | no | yes (M1–M5) |
| Projected saving with published assumptions | no | yes |
| Merges several developers into one team view | no | yes (`merge`) |
| Shareable export with no paths, names or times of day | n/a | yes |
| Network access | — | none (enforced by a test) |

## Install and run

Requires Python 3.9+. No dependencies.

```bash
git clone https://github.com/kordas-technologies/spend-teardown && cd spend-teardown
git checkout v0.1.0   # pin the release you reviewed
python3 -m spend_teardown scan --days 30 --report teardown.md
```

Options worth knowing:

- `--since 2026-11-01 --until 2026-11-15` — explicit window (until is exclusive).
- `--tz UTC` / `--tz +09:00` / `--tz Europe/Warsaw` — where day boundaries fall (default: your local time).
- `--pricing my-prices.json` — your own rates. Models missing from the table are counted in tokens and
  reported as *unpriced*; they are never priced as some other model.
  Claude Haiku 5.5 is priced by prompt length (over 100,000 prompt tokens = higher rates), as on the
  vendor's pricing page; the bundled table carries both rates.
- `--show-export` — print the exact JSON you would share, before you share it.

Team view: each developer runs `scan`, then one person runs

```bash
python3 -m spend_teardown merge alice.json bob.json carol.json -o team.json
```

## What the export contains (and what it never contains)

The export (`spend-teardown-export.json`) has token counts and costs per day and model, per-project
totals under salted hashes, mechanism counters and the projection. The salt is generated on your
machine (`~/.spend-teardown/salt`) and never leaves it.

Before anything is written, a privacy check blocks the export if it finds a file path, a home
directory name, a project or session identifier, a UUID or any time finer than one day. Message
contents are never read.

## What it does not measure

- **Subscription rate limits (Pro/Max).** Transcripts contain no meter for them and vendors don't
  publish how they are metered. No claim is made about limits.
- Calls that are not written to transcripts (e.g. advisor side calls).
- Other coding agents (Cursor and others keep no comparable local data).

## Exit codes

`0` ok · `2` usage or pricing file error · `3` unknown transcript or export schema · `4` privacy check
blocked the export · `5` no usage data in the window.

## License

MIT. Support is best effort, without an SLA. See [SECURITY.md](SECURITY.md).

Built and maintained by Martin Kordas, [Kordas Technologies](https://kordas.tech) (Kraków, Poland). Free teardowns: biuro@kordas.tech.

Not affiliated with Anthropic.
