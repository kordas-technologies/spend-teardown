"""Private export: the only thing a user ever shares with us.

Rules (enforced by check_private before anything is written):
- project slugs are replaced by salted hashes; the salt stays on the machine;
- no paths, no usernames, no session/file ids, no UUIDs;
- time resolution is one day; no time of day anywhere.
"""

import datetime as dt
import getpass
import hashlib
import json
import os
import re
import secrets

from . import EXPORT_SCHEMA, METHODOLOGY_VERSION, __version__

DEFAULT_SALT_FILE = os.path.join("~", ".spend-teardown", "salt")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TIME_OF_DAY = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}")
_PATH_MARKERS = ("/Users/", "-Users-", "/home/", "-home-", "C:\\", "C--Users", "\\Users\\", ".jsonl", "~/")


class PrivacyError(RuntimeError):
    pass


def load_or_create_salt(path=None):
    path = os.path.expanduser(path or DEFAULT_SALT_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            salt = fh.read().strip()
        if len(salt) >= 32:
            return salt
    os.makedirs(os.path.dirname(path), exist_ok=True)
    salt = secrets.token_hex(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(salt + "\n")
    return salt


def hash_project(slug, salt):
    return "p_" + hashlib.sha256((salt + "\x00" + slug).encode("utf-8")).hexdigest()[:12]


def build_export(result, salt, window, pricing):
    """result = analysis.analyze(...); window = dict(since=day, until=day, tz=label)."""
    by_project = sorted(
        ({"project": hash_project(slug, salt), **v} for slug, v in result["by_project"].items()),
        key=lambda r: -r["cost_usd"])
    return {
        "schema": EXPORT_SCHEMA,
        "tool_version": __version__,
        "methodology_version": METHODOLOGY_VERSION,
        "generated_on": dt.date.today().isoformat(),
        "window": window,
        "pricing": {"source": pricing.source, "as_of": pricing.as_of},
        "totals": result["totals"],
        "by_day": result["by_day"],
        "by_project": by_project,
        "mechanisms": result["mechanisms"],
        "projection": result["projection"],
    }


def _walk_strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def check_private(export, forbidden=()):
    """Raise PrivacyError if the export could identify paths, people, sessions or times of day."""
    forbidden = [f for f in forbidden if f and len(f) >= 3]
    try:
        user = getpass.getuser()
    except Exception:
        user = None
    if user and len(user) >= 3:
        forbidden.append(user)
    benign = _tool_strings(export)
    problems = []
    for s in _walk_strings(export):
        for marker in _PATH_MARKERS:
            if marker in s:
                problems.append("path marker %r" % marker)
        if _UUID.search(s):
            problems.append("UUID-like id")
        if _ISO_DATETIME.search(s) or _TIME_OF_DAY.search(s):
            problems.append("finer than one day")
        if s in benign:
            continue  # model ids and the pricing label: not user data
        low = s.lower()
        for f in forbidden:
            if f.lower() in low:
                problems.append("identifying string (%d chars)" % len(f))
    if problems:
        raise PrivacyError("export blocked by privacy check: " + "; ".join(sorted(set(problems))))


def _tool_strings(export):
    """Strings that come from the tool or the pricing file, not from user paths."""
    out = set()
    pr = export.get("pricing") or {}
    out.update(str(v) for v in pr.values())
    for row in export.get("by_day") or []:
        out.add(str(row.get("model")))
    totals = export.get("totals") or {}
    out.update((totals.get("unpriced_tokens_by_model") or {}).keys())
    m5 = (export.get("mechanisms") or {}).get("expensive_subagents") or {}
    if m5.get("cheapest_model"):
        out.add(m5["cheapest_model"])
    out.update(m5.get("expensive_models") or [])
    return out


def forbidden_from_scan(scan):
    """Raw identifiers seen while scanning: must never appear in the export."""
    out = set()
    for stream, info in scan.streams.items():
        out.add(info["project"])
        out.add(stream)
        out.add(os.path.basename(stream).rsplit(".", 1)[0])
        # meaningful slug parts, e.g. "-Users-alice-work-acme-api" -> "alice", "acme"
        for part in re.split(r"[-_/\\. ]+", info["project"]):
            if len(part) >= 4 and part.lower() not in _COMMON_PARTS:
                out.add(part)
    return sorted(out)


# Generic path words that would make the check reject harmless strings.
_COMMON_PARTS = {"users", "home", "documents", "desktop", "projects", "project", "work", "code", "repos",
                 "repo", "src", "github", "workspace", "workspaces", "library", "mobile", "documents",
                 "private", "tmp", "var", "folders", "claude", "subagents", "downloads", "cloud",
                 "dev", "main", "temp", "model", "models", "cost", "calls", "input", "output",
                 "cache", "read", "write", "auto", "priced", "streams", "tokens", "share", "events",
                 "saving", "premium", "floor", "line", "rewrite", "manual", "other", "loop", "with",
                 "total", "totals", "day", "days", "window", "since", "until", "schema", "export",
                 "tool", "version", "pricing", "source", "note", "gate", "meets", "projection"}


def dumps(export):
    return json.dumps(export, indent=2, sort_keys=True, ensure_ascii=False)
