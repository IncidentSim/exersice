import json
import os

import pytest

from ansible_collections.incidentsim.verify.plugins.module_utils import report


def test_evaluate_pass_fail_and_error_precedence():
    assert report.evaluate("a.b", passed=True)["status"] == "pass"
    assert report.evaluate("a.b", passed=False, observed=404, expected=200)["status"] == "fail"
    # a technical error always wins over a verdict
    assert report.evaluate("a.b", passed=True, error="psql: not found")["status"] == "error"
    assert report.evaluate("a.b", status="fail")["status"] == "fail"


def test_missing_verdict_is_never_a_pass():
    with pytest.raises(report.CheckError):
        report.evaluate("a.b")


@pytest.mark.parametrize("bad", ["", "A", "../x", "a b", "-a", "x" * 65, None, 5])
def test_invalid_ids_rejected(bad):
    with pytest.raises(report.CheckError):
        report.make_check(bad, "pass")


def test_details_are_stringified_bounded_and_redacted():
    check = report.make_check("a", "fail", observed={"password": "hunter2", "n": 1},
                              evidence="PGPASSWORD=s3cret token: abc123 Authorization: Bearer xyz " + "x" * 10000)
    blob = json.dumps(check)
    for secret in ("hunter2", "s3cret", "abc123", "xyz"):
        assert secret not in blob
    assert len(check["evidence"].encode()) <= report.MAX_DETAIL


def test_derive_status():
    p, f, e = ({"status": s} for s in ("pass", "fail", "error"))
    assert report.derive_status([p]) == "pass"
    assert report.derive_status([p, f]) == "fail"
    assert report.derive_status([f, e]) == "error"
    assert report.derive_status([]) == "error"  # nothing checked is never a pass


def test_unique_ids():
    taken = {"a", "a-2"}
    assert report.unique_id("a", taken) == "a-3"
    assert report.unique_id("b", taken) == "b"


def test_atomic_write(tmp_path):
    path = tmp_path / "report.json"
    data = report.build_report("s", "A" * 26, [report.make_check("a", "pass")])
    report.write_report_atomic(str(path), data)
    assert json.loads(path.read_text())["status"] == "pass"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


def test_technical_error_classification():
    te = report.technical_error
    # application symptoms are explicit failures, not verifier faults
    assert te({"status": -1, "msg": "Request failed: <urlopen error [Errno 111] Connection refused>"}) == ""
    assert te({"status": -1, "msg": "The read operation timed out"}) == ""
    assert te({"status": 200}) == ""
    assert te({"rc": 1, "stdout": "", "stderr": "FATAL: password authentication failed"}) == ""
    # verifier faults
    assert te({"rc": 2, "msg": "[Errno 2] No such file or directory: b'psql'"})
    assert te({"rc": 127})
    assert te({"status": -1, "msg": "unknown url type: 'htp'"})
    assert te({"exception": "Traceback", "msg": "boom"})
    assert te({"unreachable": True})
    assert te(None)
    assert te({"failed": True, "msg": "file not found"})


def test_dig():
    value = {"json": {"items": [{"id": 7}]}, "status": 200}
    assert report.dig(value, "json.items.0.id") == 7
    assert report.dig(value, "json.missing.deep", "d") == "d"
    assert report.dig(None, "a", 1) == 1
    assert report.dig(value, "json.items.5") is None
