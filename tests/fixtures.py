"""Synthetic transcripts only. No real data ever goes into this repository."""

import datetime as dt
import json
import os
import uuid

T0 = dt.datetime(2026, 11, 3, 9, 0, tzinfo=dt.timezone.utc)
SLUG = "-Users-alice-work-acmecorp-billing"


def ts(minutes):
    return (T0 + dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def assistant(minutes, msg_id, model="claude-sonnet-5-5", inp=10, cw1h=0, cw5m=0, cr=0, out=100,
              sidechain=False, extra_usage=None):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": cw1h + cw5m, "cache_read_input_tokens": cr,
             "output_tokens": out, "cache_creation": {"ephemeral_1h_input_tokens": cw1h,
                                                      "ephemeral_5m_input_tokens": cw5m}}
    if extra_usage:
        usage.update(extra_usage)
    return {"type": "assistant", "timestamp": ts(minutes), "uuid": str(uuid.uuid4()), "isSidechain": sidechain,
            "sessionId": str(uuid.uuid4()), "cwd": "/Users/alice/work/acmecorp/billing", "version": "9.9.9",
            "message": {"id": msg_id, "model": model, "role": "assistant", "usage": usage,
                        "content": [{"type": "text", "text": "SECRET CONTENT acmecorp"}]}}


def compact(minutes, trigger="auto", pre=180_000):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": ts(minutes), "uuid": str(uuid.uuid4()),
            "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": 30_000}}


def user(minutes, text="hello acmecorp"):
    return {"type": "user", "timestamp": ts(minutes), "uuid": str(uuid.uuid4()),
            "message": {"role": "user", "content": text}}


def write(root, rel, lines, mtime=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for d in lines:
            fh.write((d if isinstance(d, str) else json.dumps(d)) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def session_file(name="3f2a9c1e-0000-4000-8000-000000000001"):
    return os.path.join(SLUG, name + ".jsonl")
