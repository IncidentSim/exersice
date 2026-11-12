import math

import pytest

from ansible_collections.incidentsim.verify.plugins.module_utils import observe


def series(values, step=10, start=1000.0, name="m", labels=None):
    return [{"name": name, "labels": labels or {}, "value": v, "ts": start + i * step} for i, v in enumerate(values)]


def test_prometheus_parse_preserves_labels_and_invalid_values():
    text = '# HELP x\nhttp_requests{code="200",path="/a\\"b"} 12 1700000000000\nup 1\nbad{x="y"} NaN\ninf_metric +Inf\n'
    samples = observe.parse_prometheus(text, ts=5.0)
    assert samples[0] == {"name": "http_requests", "labels": {"code": "200", "path": '/a"b'}, "value": 12.0, "ts": 1700000000.0}
    assert samples[1]["ts"] == 5.0 and samples[1]["labels"] == {}
    assert samples[2]["value"] is None and samples[3]["value"] is None


def test_aggregates_and_spread():
    s = series([3, 5, 4, 9])
    for how, want in (("min", 3), ("max", 9), ("avg", 5.25), ("last", 9), ("first", 3), ("spread", 6), ("count", 4), ("sum", 21)):
        r = observe.aggregate_window(s, window_seconds=30, aggregate=how)
        assert r["available"] and r["value"] == want, how


def test_warmup_discards_early_samples():
    s = series([100, 100, 1, 2, 1, 2, 1])  # ts 1000..1060
    r = observe.aggregate_window(s, window_seconds=60, warmup_seconds=20, aggregate="spread")
    assert r["available"] and r["value"] == 1 and r["used"] == 5


@pytest.mark.parametrize("samples", [[], series([float("nan"), None, "x"])])
def test_no_data_is_unavailable_not_success(samples):
    r = observe.aggregate_window(samples, window_seconds=60, aggregate="max")
    assert not r["available"] and r["value"] is None and r["reason"]


def test_selector_with_no_match_is_unavailable():
    r = observe.aggregate_window(series([1, 2]), window_seconds=60, name="other")
    assert not r["available"]


def test_min_samples_and_coverage():
    s = series([1, 2], step=5)
    assert not observe.aggregate_window(s, 60, min_samples=3)["available"]
    assert not observe.aggregate_window(s, 60, min_coverage=0.5)["available"]
    assert observe.aggregate_window(series(range(7)), 60, min_coverage=0.9)["available"]


def test_nan_samples_are_dropped_but_counted():
    s = series([1, float("nan"), 3])
    r = observe.aggregate_window(s, 60, aggregate="avg")
    assert r["value"] == 2 and r["invalid"] == 1


def test_multiple_label_sets_are_not_silently_mixed():
    s = series([1, 2], labels={"db": "a"}) + series([10, 20], labels={"db": "b"})
    r = observe.aggregate_window(s, 60)
    assert not r["available"] and "2 series" in r["reason"]
    r = observe.aggregate_window(s, 60, labels={"db": "b"}, aggregate="max")
    assert r["value"] == 20
    r = observe.aggregate_window(s, 60, merge=True, aggregate="max")
    assert r["value"] == 20 and set(r["series"]) == {"db=a", "db=b"}


@pytest.mark.parametrize("window,warm", [(0, 0), (10, 10), (10, -1)])
def test_invalid_window_rejected(window, warm):
    with pytest.raises(ValueError):
        observe.aggregate_window(series([1]), window, warmup_seconds=warm)


LOG = """2026-11-12T10:00:00+0000 vm app[1]: started
2026-11-12T10:00:05+00:00 vm app[1]: ERROR connection refused
continuation line without timestamp
2026-11-12T10:00:09Z vm app[1]: ok
2026-11-12T10:01:00+0000 vm app[1]: later
"""


def test_parse_log_text_counts_unparsed():
    entries, unparsed = observe.parse_log_text(LOG)
    assert len(entries) == 4 and unparsed == 1


def test_log_scope_is_half_open_and_explicit():
    entries, _ = observe.parse_log_text(LOG)
    r = observe.scan_logs(entries, "2026-11-12T10:00:00Z", "2026-11-12T10:01:00Z", "ERROR")
    assert r["passed"] and r["scanned"] == 3 and r["matched"] == 1
    # the late entry lies outside the scope
    r = observe.scan_logs(entries, "2026-11-12T10:00:06Z", "2026-11-12T10:01:00Z", "ERROR", expect="absent")
    assert r["passed"] and r["scanned"] == 1


def test_absent_never_passes_on_empty_or_unavailable_source():
    r = observe.scan_logs([], 0, 100, "ERROR", expect="absent")
    assert not r["available"] and not r["passed"]
    entries, _ = observe.parse_log_text(LOG)
    r = observe.scan_logs(entries, "2026-11-12T10:00:00Z", "2026-11-12T10:01:00Z", "ERROR", expect="absent", available=False)
    assert not r["available"] and not r["passed"]
    r = observe.scan_logs(entries, "2026-11-12T11:00:00Z", "2026-11-12T12:00:00Z", "ERROR", expect="absent")
    assert not r["available"]  # scope holds no entries: not evidence of absence


def test_present_failure_is_explicit_not_unavailable():
    entries, _ = observe.parse_log_text(LOG)
    r = observe.scan_logs(entries, "2026-11-12T10:00:00Z", "2026-11-12T10:01:00Z", "NEVER")
    assert r["available"] and not r["passed"]


def test_cursor_resumes_numerically():
    entries, _ = observe.parse_log_text(LOG)
    first = observe.scan_logs(entries, "2026-11-12T10:00:00Z", "2026-11-12T10:02:00Z", ".")
    again = observe.scan_logs(entries, "2026-11-12T10:00:00Z", "2026-11-12T10:02:00Z", ".", min_entries=0, cursor=first["cursor"])
    assert not again["available"] and again["scanned"] == 0
    mid = entries[1]["cursor"]
    rest = observe.scan_logs(entries, "2026-11-12T10:00:00Z", "2026-11-12T10:02:00Z", ".", cursor=mid)
    assert rest["scanned"] == 2
    with pytest.raises(ValueError):
        observe.scan_logs(entries, 0, 1e12, ".", cursor="garbage")


def test_bad_scope_rejected():
    with pytest.raises(ValueError):
        observe.scan_logs([], 10, 10, ".")
    assert not math.isnan(observe.parse_time("2026-11-12T10:00:00"))
