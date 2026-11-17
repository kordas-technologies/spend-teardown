# Security and data handling

## What the tool reads

Only these fields of each transcript line: `type`, `subtype`, `timestamp`, `isSidechain`,
`compactMetadata.trigger`, `compactMetadata.preTokens`, `message.id`, `message.model`,
`message.usage`. Message contents (your prompts, code, tool output) are never parsed into the
results.

## What the tool sends

Nothing. The package imports no networking modules; `tests/test_privacy_and_cli.py`
(`NoNetworkTests`) fails the build if any module imports `socket`, `http`, `urllib`, `ssl`,
`subprocess`, `asyncio` or similar, or uses a dynamic `__import__`.

## What you may choose to share

Only the export file, and only after reviewing it (`--show-export`). It is built to be
non-identifying: salted project hashes, day buckets, no paths, no usernames, no session ids, no
time of day. A privacy check blocks writing an export that violates these rules (exit code 4).

If you send us an export for a teardown, we keep it for 30 days and then delete it, unless you
opt in to the anonymous benchmark.

## Supply chain

- Zero third-party dependencies; standard library only.
- CI actions will be pinned by commit SHA before the repository is published.
- Planned: tagged, signed releases with provenance. Until then, run from a reviewed checkout.

## Reporting a vulnerability

Email security contact listed on the project page. Please do not open a public issue for a
privacy leak in the export; we will acknowledge within 5 working days.
