# -*- coding: utf-8 -*-
"""Metric windows and log scopes.

Both helpers are built so that missing data can never look like success:
no samples, only NaN, too little coverage or an unreadable log source yield an
``unavailable`` result with a reason instead of an empty aggregate.
"""
from __future__ import absolute_import, division, print_function

__metaclass__ = type

import datetime
import math
import re

AGGREGATES = ("min", "max", "avg", "last", "first", "spread", "count", "sum")

_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)(?:\s+(?P<ts>-?\d+))?\s*$"
)
_LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:\\.|[^"\\])*)"\s*(?:,|$)')


def _unescape(value):
    return re.sub(r"\\(.)", lambda m: {"n": "\n"}.get(m.group(1), m.group(1)), value)


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_prometheus(text, ts=None):
    """Parse Prometheus text exposition, preserving labels.

    ``ts`` (epoch seconds) stamps samples that carry no timestamp. Values that
    are NaN/Inf or unparsable become ``None`` and are dropped later as invalid.
    """
    samples = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if not match:
            continue
        labels = {}
        raw = match.group("labels") or ""
        pos = 0
        while pos < len(raw):
            label = _LABEL.match(raw, pos)
            if not label:
                break
            labels[label.group(1)] = _unescape(label.group(2))
            pos = label.end()
        stamp = ts
        if match.group("ts"):
            stamp = int(match.group("ts")) / 1000.0
        samples.append({"name": match.group("name"), "labels": labels, "value": _number(match.group("value")), "ts": stamp})
    return samples


def _series_key(labels):
    return ",".join("%s=%s" % (k, labels[k]) for k in sorted(labels))


def _aggregate(values, how):
    if how == "min":
        return min(values)
    if how == "max":
        return max(values)
    if how == "avg":
        return sum(values) / len(values)
    if how == "last":
        return values[-1]
    if how == "first":
        return values[0]
    if how == "spread":
        return max(values) - min(values)
    if how == "count":
        return len(values)
    if how == "sum":
        return sum(values)
    raise ValueError("unknown aggregate %r" % how)


def _unavailable(reason, **extra):
    result = {"available": False, "reason": reason, "value": None}
    result.update(extra)
    return result


def aggregate_window(samples, window_seconds, aggregate="avg", name=None, labels=None, warmup_seconds=0.0,
                     end=None, min_samples=1, min_coverage=0.0, merge=False):
    """Aggregate samples inside the observation window.

    The window is ``[end - window_seconds, end]`` (``end`` defaults to the newest
    matching sample); the first ``warmup_seconds`` of it are discarded. Coverage
    is the span of retained samples relative to the post-warm-up window.
    """
    if aggregate not in AGGREGATES:
        raise ValueError("unknown aggregate %r" % aggregate)
    if window_seconds <= 0 or warmup_seconds < 0 or warmup_seconds >= window_seconds:
        raise ValueError("window_seconds must be positive and larger than warmup_seconds")
    labels = labels or {}
    matched = [
        s for s in samples
        if (name is None or s.get("name") == name)
        and all((s.get("labels") or {}).get(k) == v for k, v in labels.items())
        and s.get("ts") is not None
    ]
    if not matched:
        return _unavailable("no samples match the selector", matched=0)
    valid = [s for s in matched if isinstance(s.get("value"), (int, float)) and math.isfinite(s["value"])]
    invalid = len(matched) - len(valid)
    if not valid:
        return _unavailable("all %d matching samples are NaN or non-numeric" % len(matched), matched=len(matched), invalid=invalid)
    window_end = float(end) if end is not None else max(s["ts"] for s in valid)
    start = window_end - window_seconds + warmup_seconds
    used = sorted((s for s in valid if start <= s["ts"] <= window_end), key=lambda s: s["ts"])
    base = {"matched": len(matched), "invalid": invalid, "used": len(used)}
    if len(used) < max(1, min_samples):
        return _unavailable("only %d usable samples in the window, need %d" % (len(used), max(1, min_samples)), **base)
    coverage = (used[-1]["ts"] - used[0]["ts"]) / float(window_seconds - warmup_seconds)
    base["coverage"] = round(coverage, 4)
    if coverage < min_coverage:
        return _unavailable("samples cover %.0f%% of the window, need %.0f%%" % (coverage * 100, min_coverage * 100), **base)
    series = {}
    for sample in used:
        series.setdefault(_series_key(sample.get("labels") or {}), []).append(sample["value"])
    if len(series) > 1 and not merge:
        return _unavailable("%d series match (%s); narrow the labels or set merge" % (len(series), "; ".join(sorted(series))), **base)
    result = dict(base)
    result["available"] = True
    result["reason"] = ""
    result["series"] = {key: _aggregate(values, aggregate) for key, values in series.items()}
    result["value"] = _aggregate([s["value"] for s in used], aggregate)
    result["aggregate"] = aggregate
    return result


def parse_time(value):
    """Epoch seconds from a number or an ISO 8601 string (naive means UTC)."""
    if isinstance(value, bool):
        raise ValueError("invalid time %r" % (value,))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.match(r"^-?\d+(\.\d+)?$", text):
        return float(text)
    text = re.sub(r"Z$", "+00:00", text)
    text = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", text)
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


_LOG_LINE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?|\d{9,}(?:\.\d+)?)\s+(?P<msg>.*)$")


def parse_log_text(text):
    """Entries from lines that start with an ISO 8601 or epoch timestamp."""
    entries, unparsed = [], 0
    for index, line in enumerate(str(text).splitlines()):
        match = _LOG_LINE.match(line)
        if not match:
            if line.strip():
                unparsed += 1
            continue
        try:
            stamp = parse_time(match.group("ts").replace(" ", "T", 1))
        except ValueError:
            unparsed += 1
            continue
        entries.append({"ts": stamp, "message": match.group("msg"), "cursor": "%d:%d" % (int(stamp * 1000), index)})
    return entries, unparsed


def _cursor_key(cursor):
    """Order cursors of the form '<epoch-ms>:<line index>' numerically."""
    try:
        stamp, index = str(cursor).split(":")
        return (int(stamp), int(index))
    except ValueError:
        raise ValueError("invalid log cursor %r" % (cursor,))


def scan_logs(entries, since, until, pattern, expect="present", min_entries=1, available=True, cursor=None, unparsed=0):
    """Scan log entries in ``[since, until)``.

    ``expect=absent`` passes only when the source was readable and the scope
    contained at least ``min_entries`` entries, so an unreadable or empty source
    can never produce a successful "no errors" result. ``cursor`` skips entries
    up to and including that cursor and the result returns the new cursor.
    """
    if expect not in ("present", "absent"):
        raise ValueError("expect must be present or absent")
    since, until = parse_time(since), parse_time(until)
    if until <= since:
        raise ValueError("until must be later than since")
    regex = re.compile(pattern)
    if not available:
        return _unavailable("log source is unavailable", passed=False, scanned=0, matches=[], cursor=cursor)
    scope = [e for e in entries if since <= e["ts"] < until]
    if cursor:
        scope = [e for e in scope if _cursor_key(e.get("cursor")) > _cursor_key(cursor)]
    if len(scope) < max(1, min_entries):
        return _unavailable(
            "only %d log entries in the time scope, need %d (%d unparsed lines)" % (len(scope), max(1, min_entries), unparsed),
            passed=False, scanned=len(scope), matches=[], cursor=cursor)
    matches = [e["message"] for e in scope if regex.search(e["message"])]
    last = max((e.get("cursor") for e in scope if e.get("cursor")), key=_cursor_key, default=cursor)
    return {
        "available": True, "reason": "", "scanned": len(scope), "matched": len(matches), "matches": matches[:20],
        "passed": bool(matches) if expect == "present" else not matches, "cursor": last, "value": len(matches),
    }
