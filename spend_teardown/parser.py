"""Read Claude Code JSONL transcripts into usage records and compaction events.

Only usage metadata is read; message contents are never touched. See
METHODOLOGY.md, "Record selection".
"""

import datetime as dt
import json
import os

SCHEMA_FAIL_SHARE = 0.01


class SchemaError(RuntimeError):
    """Transcript shape not recognised: stop rather than report wrong numbers."""


class Call:
    __slots__ = ("ts", "model", "inp", "cw1h", "cw5m", "cr", "out", "side", "stream", "project")

    def __init__(self, ts, model, inp, cw1h, cw5m, cr, out, side, stream, project):
        self.ts, self.model, self.inp, self.cw1h, self.cw5m = ts, model, inp, cw1h, cw5m
        self.cr, self.out, self.side, self.stream, self.project = cr, out, side, stream, project

    @property
    def cw(self):
        return self.cw1h + self.cw5m

    @property
    def ctx(self):
        return self.inp + self.cw + self.cr


class Compaction:
    __slots__ = ("ts", "trigger", "pre_tokens", "stream", "project")

    def __init__(self, ts, trigger, pre_tokens, stream, project):
        self.ts, self.trigger, self.pre_tokens, self.stream, self.project = ts, trigger, pre_tokens, stream, project


class Scan:
    def __init__(self):
        self.calls = []
        self.compactions = []
        self.streams = {}          # stream id -> {"project": slug, "sub": bool}
        self.files_read = 0
        self.files_skipped_mtime = 0
        self.usage_lines = 0
        self.bad_shape = 0
        self.dup_skipped = 0
        self.synthetic_skipped = 0
        self.out_of_window = 0


def parse_ts(value):
    """ISO-8601 timestamp -> epoch seconds (float). Raises ValueError."""
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _usage_shape(d):
    """Return normalised usage tuple or None if the shape is not recognised."""
    m = d.get("message")
    if not isinstance(m, dict):
        return None
    u = m.get("usage")
    model = m.get("model")
    if not isinstance(u, dict) or not isinstance(model, str) or "timestamp" not in d:
        return None
    inp, out = _int(u.get("input_tokens")), _int(u.get("output_tokens"))
    if inp is None or out is None:
        return None
    cwt = _int(u.get("cache_creation_input_tokens")) or 0
    cr = _int(u.get("cache_read_input_tokens")) or 0
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    cw1h = _int(cc.get("ephemeral_1h_input_tokens")) or 0
    cw5m = _int(cc.get("ephemeral_5m_input_tokens")) or 0
    if cw1h + cw5m == 0 and cwt:
        cw5m = cwt  # older format without the 1h/5m split
    return model, inp, cw1h, cw5m, cr, out


def iter_files(root):
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".jsonl"):
                yield os.path.join(dirpath, fn)


def scan(root, since_epoch=None, until_epoch=None):
    """Parse every transcript under root. Window bounds are epoch seconds."""
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise FileNotFoundError("transcript directory not found: %s" % root)
    out = Scan()
    files = []
    for path in iter_files(root):
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if since_epoch is not None and mt < since_epoch:
            out.files_skipped_mtime += 1
            continue
        files.append((mt, path))
    files.sort()

    seen_keys = set()
    seen_boundaries = set()
    for _mt, path in files:
        rel = os.path.relpath(path, root)
        project = rel.split(os.sep)[0]
        is_sub = (os.sep + "subagents" + os.sep) in (os.sep + rel)
        out.files_read += 1
        local = {}       # key -> Call (last streamed line wins)
        order = []
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                has_usage = '"usage"' in line
                has_cb = '"compact_boundary"' in line
                if not (has_usage or has_cb):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if has_cb and d.get("type") == "system" and d.get("subtype") == "compact_boundary":
                    uid = d.get("uuid") or (rel, d.get("timestamp"))
                    if uid in seen_boundaries:
                        continue
                    seen_boundaries.add(uid)
                    try:
                        ts = parse_ts(d.get("timestamp"))
                    except ValueError:
                        continue
                    if (since_epoch is not None and ts < since_epoch) or (until_epoch is not None and ts >= until_epoch):
                        continue
                    meta = d.get("compactMetadata") if isinstance(d.get("compactMetadata"), dict) else {}
                    out.compactions.append(Compaction(ts, str(meta.get("trigger") or "unknown"),
                                                      _int(meta.get("preTokens")) or 0, rel, project))
                    out.streams.setdefault(rel, {"project": project, "sub": is_sub})
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message")
                if not (isinstance(msg, dict) and "usage" in msg):
                    continue
                out.usage_lines += 1
                shape = _usage_shape(d)
                if shape is None:
                    out.bad_shape += 1
                    continue
                try:
                    ts = parse_ts(d["timestamp"])
                except ValueError:
                    out.bad_shape += 1
                    continue
                model, inp, cw1h, cw5m, cr, o = shape
                key = msg.get("id") or d.get("requestId") or d.get("uuid")
                if key is None:
                    key = (rel, d["timestamp"])
                if key in seen_keys:
                    out.dup_skipped += 1
                    continue
                if key not in local:
                    order.append(key)
                local[key] = Call(ts, model, inp, cw1h, cw5m, cr, o,
                                  bool(d.get("isSidechain")) or is_sub, rel, project)
        for key in order:
            seen_keys.add(key)
            c = local[key]
            if c.model == "<synthetic>":
                out.synthetic_skipped += 1
                continue
            if (since_epoch is not None and c.ts < since_epoch) or (until_epoch is not None and c.ts >= until_epoch):
                out.out_of_window += 1
                continue
            out.calls.append(c)
            out.streams.setdefault(rel, {"project": project, "sub": is_sub})

    good = out.usage_lines - out.bad_shape
    if out.usage_lines and (good == 0 or out.bad_shape > SCHEMA_FAIL_SHARE * out.usage_lines):
        raise SchemaError(
            "unknown transcript schema: %d of %d usage records have an unrecognised shape. "
            "This version of spend-teardown cannot read them; please report the Claude Code "
            "version (claude --version) so the parser can be updated." % (out.bad_shape, out.usage_lines))
    out.calls.sort(key=lambda c: (c.stream, c.ts))
    out.compactions.sort(key=lambda c: (c.stream, c.ts))
    return out
