# -*- coding: utf-8 -*-
"""Report model shared by the check module and the report callback.

Pure Python without Ansible imports so that it can be unit tested directly.
"""
from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
import re
import tempfile

SCHEMA = "incidentsim.verify/v1"
STATUSES = ("pass", "fail", "error")
CHECK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_DESCRIPTION = 512
MAX_DETAIL = 4096
MAX_CHECKS = 500

_SECRET = re.compile(
    r"(?i)((?:pg)?pass(?:word|wd)?|secret|token|api[_-]?key|authorization)"
    r"([\"']?\s*[=:]\s*[\"']?)(bearer\s+)?([^\s\"',;&]+)"
)


class CheckError(ValueError):
    """Raised for an invalid check definition (a bug in the playbook)."""


def redact(text):
    """Mask values that follow secret-looking keys."""
    return _SECRET.sub(lambda m: m.group(1) + m.group(2) + "***", text)


def stringify(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def bound(value, limit):
    text = redact(stringify(value)).replace("\x00", "")
    if len(text.encode("utf-8")) <= limit:
        return text
    return text.encode("utf-8")[: limit - 3].decode("utf-8", "ignore") + "..."


def slug(value, fallback="task"):
    text = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return (text or fallback)[:48]


def make_check(check_id, status, description="", observed=None, expected=None, evidence=None):
    if not isinstance(check_id, str) or not CHECK_ID.match(check_id):
        raise CheckError("invalid check id %r (use a-z, 0-9, '.', '_' or '-')" % (check_id,))
    if status not in STATUSES:
        raise CheckError("invalid check status %r" % (status,))
    check = {"id": check_id, "status": status}
    for key, value, limit in (
        ("description", description, MAX_DESCRIPTION),
        ("observed", observed, MAX_DETAIL),
        ("expected", expected, MAX_DETAIL),
        ("evidence", evidence, MAX_DETAIL),
    ):
        text = bound(value, limit)
        if text:
            check[key] = text
    return check


def evaluate(check_id, passed=None, status=None, error=None, **details):
    """Decide a check's status.

    A non-empty ``error`` means the check could not be evaluated (technical
    problem) and always wins. Otherwise an explicit ``status`` is used, else
    ``passed``. A missing verdict is an error, never a pass.
    """
    if error not in (None, "", False):
        details["evidence"] = details.get("evidence") or error
        return make_check(check_id, "error", **details)
    if status:
        return make_check(check_id, status, **details)
    if passed is None:
        raise CheckError("one of passed or status is required")
    return make_check(check_id, "pass" if passed else "fail", **details)


def derive_status(checks):
    statuses = [c["status"] for c in checks]
    if not statuses or "error" in statuses:
        return "error"
    return "fail" if "fail" in statuses else "pass"


def unique_id(check_id, taken):
    if check_id not in taken:
        return check_id
    n = 2
    while "%s-%d" % (check_id[:58], n) in taken:
        n += 1
    return "%s-%d" % (check_id[:58], n)


def build_report(scenario, attempt_id, checks, started_at=None, finished_at=None, complete=True):
    checks = list(checks)[:MAX_CHECKS]
    report = {
        "schema": SCHEMA,
        "scenario": scenario,
        "attempt_id": attempt_id,
        "complete": bool(complete),
        "status": derive_status(checks),
        "checks": checks,
    }
    if started_at:
        report["started_at"] = started_at
    if finished_at:
        report["finished_at"] = finished_at
    return report


def write_report_atomic(path, report):
    """Write the report so that readers never observe a partial file."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".report-", dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


_CONNECTION_SYMPTOMS = (
    "connection refused",
    "connection reset",
    "timed out",
    "remote end closed",
    "remotedisconnected",
    "connection aborted",
    "bad status line",
)
_TECHNICAL_COMMAND = (
    "no such file or directory", "permission denied", "could not find or access",
    "error executing command",  # command module could not start the executable
)


def technical_error(result):
    """Return a message when a registered task result shows a technical fault.

    A refused or timed-out application connection is a symptom (the check
    should *fail*), while a missing binary, a crashed module or an unusable
    URL means the verifier itself is broken (the check is an *error*).
    Returns an empty string when there is no technical fault.
    """
    if not isinstance(result, dict):
        return "no result was recorded for the observation"
    if result.get("unreachable"):
        return bound(result.get("msg") or "host unreachable", 300)
    if result.get("exception"):
        return bound(result.get("msg") or "module raised an exception", 300)
    message = str(result.get("msg") or "")
    lowered = message.lower()
    if result.get("rc") in (126, 127):
        return bound(message or "command could not be executed (rc=%s)" % result["rc"], 300)
    if "rc" in result and any(s in lowered for s in _TECHNICAL_COMMAND):
        return bound(message, 300)
    if result.get("status") == -1:
        if any(s in lowered for s in _CONNECTION_SYMPTOMS):
            return ""
        return bound(message or "request failed without a status", 300)
    if result.get("failed") and not result.get("status") and "rc" not in result:
        return bound(message or "task failed", 300)
    return ""


def dig(value, path, default=None):
    """Safe nested lookup: dig(result, 'json.items.0.id')."""
    current = value
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit() and -len(current) <= int(part) < len(current):
            current = current[int(part)]
        else:
            return default
    return current
