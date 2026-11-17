"""Command line: scan | merge | report. No network access anywhere in this package."""

import argparse
import datetime as dt
import json
import os
import re
import sys

from . import __version__
from .analysis import analyze
from .export import PrivacyError, build_export, check_private, dumps, forbidden_from_scan, load_or_create_salt
from .merge import MergeError, merge, validate
from .parser import SchemaError, scan
from .pricing import Pricing, PricingError
from .report import render

EXIT_USAGE, EXIT_SCHEMA, EXIT_PRIVACY, EXIT_NODATA = 2, 3, 4, 5


def parse_tz(value):
    """Return (tzinfo or None for system local, label without time-of-day notation)."""
    if value in (None, "", "local"):
        off = dt.datetime.now().astimezone().utcoffset()
        mins = int(off.total_seconds() // 60)
        return None, "local(UTC%s%02d%02d)" % ("+" if mins >= 0 else "-", abs(mins) // 60, abs(mins) % 60)
    if value.upper() == "UTC":
        return dt.timezone.utc, "UTC"
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})", value)
    if m:
        mins = int(m.group(2)) * 60 + int(m.group(3))
        mins = -mins if m.group(1) == "-" else mins
        return dt.timezone(dt.timedelta(minutes=mins)), "UTC%s%02d%02d" % (m.group(1), abs(mins) // 60, abs(mins) % 60)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(value), value
    except Exception:
        raise argparse.ArgumentTypeError("unknown timezone %r (use local, UTC, +09:00 or an IANA name)" % value)


def _midnight(day, tz):
    d = dt.datetime.strptime(day, "%Y-%m-%d")
    return d.replace(tzinfo=tz) if tz is not None else d.astimezone()


def window_bounds(args, tz):
    if args.since:
        since = _midnight(args.since, tz)
        until = _midnight(args.until, tz) if args.until else since + dt.timedelta(days=args.days)
    else:
        today = dt.datetime.now(tz) if tz is not None else dt.datetime.now().astimezone()
        until = _midnight(today.strftime("%Y-%m-%d"), tz) + dt.timedelta(days=1)
        if args.until:
            until = _midnight(args.until, tz)
        since = until - dt.timedelta(days=args.days)
    if until <= since:
        raise argparse.ArgumentTypeError("--until must be after --since")
    return since, until


def cmd_scan(args):
    tz, tz_label = parse_tz(args.tz)
    since, until = window_bounds(args, tz)
    pricing = Pricing.load(args.pricing)
    try:
        sc = scan(args.root, since.timestamp(), until.timestamp())
    except SchemaError as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_SCHEMA
    except FileNotFoundError as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_NODATA
    if not sc.calls:
        print("error: no usage records in the window (%d files read)" % sc.files_read, file=sys.stderr)
        return EXIT_NODATA
    result = analyze(sc, pricing, tz)
    salt = load_or_create_salt(args.salt_file)
    window = {"since": since.strftime("%Y-%m-%d"), "until": until.strftime("%Y-%m-%d"), "tz": tz_label}
    export = build_export(result, salt, window, pricing)
    try:
        check_private(export, forbidden_from_scan(sc))
    except PrivacyError as e:
        print("error: %s. Nothing was written." % e, file=sys.stderr)
        return EXIT_PRIVACY
    text = dumps(export)
    if args.show_export:
        print(text)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    report = render(export)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report)
    if not args.show_export:
        print(report)
    print("\nExport written to %s (%d files read, %d duplicate records skipped, %d API calls). "
          "Review it with --show-export before sharing." % (args.out, sc.files_read, sc.dup_skipped, len(sc.calls)),
          file=sys.stderr)
    return 0


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_merge(args):
    try:
        team = merge([_load(p) for p in args.exports])
        check_private(team)
    except (MergeError, PrivacyError) as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_SCHEMA if isinstance(e, MergeError) else EXIT_PRIVACY
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(dumps(team) + "\n")
    print(render(team))
    print("\nTeam export written to %s." % args.out, file=sys.stderr)
    return 0


def cmd_report(args):
    try:
        export = _load(args.export)
        validate(export)
    except MergeError as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_SCHEMA
    print(render(export))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="spend-teardown", description=(
        "Explain why your coding-agent token spend is what it is: local, offline, reads usage metadata only."))
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan local transcripts, write a private export and print a report")
    s.add_argument("--root", default=os.path.join("~", ".claude", "projects"), help="transcript directory")
    s.add_argument("--days", type=int, default=30, help="window length in days (default 30)")
    s.add_argument("--since", help="window start YYYY-MM-DD (inclusive)")
    s.add_argument("--until", help="window end YYYY-MM-DD (exclusive)")
    s.add_argument("--tz", default="local", help="day boundaries: local, UTC, +09:00 or IANA name")
    s.add_argument("--pricing", help="pricing JSON (default: bundled table, see its as_of)")
    s.add_argument("--out", default="spend-teardown-export.json", help="export file")
    s.add_argument("--report", help="also write the markdown report to this file")
    s.add_argument("--show-export", action="store_true", help="print the exact export JSON that you would share")
    s.add_argument("--salt-file", default=None, help="where the local hashing salt lives (never exported)")
    s.set_defaults(func=cmd_scan)

    m = sub.add_parser("merge", help="merge exports from several developers into one team export")
    m.add_argument("exports", nargs="+")
    m.add_argument("-o", "--out", default="spend-teardown-team.json")
    m.set_defaults(func=cmd_merge)

    r = sub.add_parser("report", help="render the report for an existing export")
    r.add_argument("export")
    r.set_defaults(func=cmd_report)
    return p


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.func(args)
    except argparse.ArgumentTypeError as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_USAGE
    except PricingError as e:
        print("error: pricing: %s" % e, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
