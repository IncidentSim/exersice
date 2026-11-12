"""Black-box runs of migrated verification playbooks against isolated fixtures."""
import glob
import json
import os

import pytest

from conftest import REPO, needs_ansible, run_playbook

pytestmark = needs_ansible
SCENARIOS = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(REPO, "*", "manifest.yaml")))


def playbook(name):
    return os.path.join(REPO, name, "verification", "playbook.yml")


def by_id(data):
    return {c["id"]: c for c in data["checks"]}


# ---------------------------------------------------------------- nginx-wrong-api
def test_nginx_pass(tmp_path, app):
    proc, data = run_playbook(tmp_path, playbook("nginx-wrong-api"), {"verification_url": app.url + "/"})
    assert proc.returncode == 0, proc.stdout
    assert data["status"] == "pass" and set(by_id(data)) == {"app.reachable", "host.header-preserved"}


def test_nginx_wrong_host_is_a_fail_with_observed_and_expected(tmp_path, app):
    app.mode["host"] = "backend_upstream"
    proc, data = run_playbook(tmp_path, playbook("nginx-wrong-api"), {"verification_url": app.url + "/"})
    check = by_id(data)["host.header-preserved"]
    assert data["status"] == "fail" and check["status"] == "fail"
    assert check["observed"] == "backend_upstream" and check["expected"] == "verification.incidentsim.test"


def test_nginx_down_is_a_fail(tmp_path, app):
    app.mode["down"] = True
    proc, data = run_playbook(tmp_path, playbook("nginx-wrong-api"), {"verification_url": app.url + "/"})
    assert data["status"] == "fail" and by_id(data)["app.reachable"]["observed"] == "503"


def test_nginx_bad_url_is_a_technical_error(tmp_path, app):
    proc, data = run_playbook(tmp_path, playbook("nginx-wrong-api"), {"verification_url": "htp://nowhere"})
    assert data["status"] == "error", data


# ---------------------------------------------------------------- postgres-conn-leak
FAST = {"sample_count": 4, "sample_interval": 1}


def leak_vars(app):
    return dict(FAST, app_url=app.url)


def test_conn_leak_pass(tmp_path, app, db):
    db.set(sessions=[7, 5, 5, 5])
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    assert proc.returncode == 0, proc.stdout
    ids = by_id(data)
    assert data["status"] == "pass"
    for required in ("db.accepts-connections", "db.max-connections-bounded", "app.write-accepted", "window.requests-served", "sessions.stable"):
        assert ids[required]["status"] == "pass"


def test_conn_leak_growing_sessions_fail_in_the_settled_half(tmp_path, app, db):
    db.set(sessions=[5, 6, 9, 14])
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    check = by_id(data)["sessions.stable"]
    assert data["status"] == "fail" and check["status"] == "fail"
    assert "spread" in check["observed"] and "<= 3" in check["expected"]


def test_conn_leak_early_spike_is_not_counted(tmp_path, app, db):
    # only the settled second half is judged, as in the original playbook
    db.set(sessions=[40, 5, 5, 5])
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    assert data["status"] == "pass", data


def test_conn_leak_raised_connection_limit_fails(tmp_path, app, db):
    db.set(max_connections=1000)
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    assert by_id(data)["db.max-connections-bounded"]["status"] == "fail" and data["status"] == "fail"


def test_conn_leak_application_failure_fails(tmp_path, app, db):
    app.mode["write_fail"] = True
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    assert by_id(data)["app.write-accepted"]["status"] == "fail"


def test_conn_leak_broken_database_tooling_is_error_not_fail(tmp_path, app, db):
    db.set(mode="broken")
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    assert data["status"] == "error" and by_id(data)["db.accepts-connections"]["status"] == "error"


def test_conn_leak_unreadable_session_counts_never_pass(tmp_path, app, db):
    db.set(sessions=["n/a"])
    proc, data = run_playbook(tmp_path, playbook("postgres-conn-leak"), leak_vars(app), db=db)
    assert data["status"] == "error", data


# ---------------------------------------------------------------- postgres-lock
def test_lock_pass_and_slow_writes_fail(tmp_path, app):
    vars_ = {"app_url": app.url, "sample_count": 3, "sample_interval": 0}
    proc, data = run_playbook(tmp_path, playbook("postgres-lock"), vars_)
    assert proc.returncode == 0 and data["status"] == "pass", proc.stdout
    app.mode["write_latency"] = 2  # a write waiting on a lock exceeds the 1 s budget
    (tmp_path / "slow").mkdir()
    proc, data = run_playbook(tmp_path / "slow", playbook("postgres-lock"), vars_)
    failing = [c for c in data["checks"] if c["status"] == "fail"]
    assert data["status"] == "fail" and failing[0]["id"].startswith("writes.saved.")
    assert failing[0]["expected"] == "HTTP 201 within 1 s"


# ---------------------------------------------------------------- postgres-missing-index
def test_missing_index_fast_correct_reports_pass(tmp_path, app, db):
    db.set(sales=3)
    proc, data = run_playbook(tmp_path, playbook("postgres-missing-index"), {"app_url": app.url}, db=db)
    assert proc.returncode == 0 and data["status"] == "pass", proc.stdout
    assert set(by_id(data)) == {"reports.http-ok", "reports.counts-correct", "reports.latency-within-budget"}


def test_missing_index_slow_reports_fail_on_latency_only(tmp_path, app, db):
    app.mode["latency"] = 0.4
    proc, data = run_playbook(tmp_path, playbook("postgres-missing-index"), {"app_url": app.url}, db=db)
    ids = by_id(data)
    assert ids["reports.http-ok"]["status"] == "pass" and ids["reports.counts-correct"]["status"] == "pass"
    assert ids["reports.latency-within-budget"]["status"] == "fail" and data["status"] == "fail"


def test_missing_index_wrong_sql_correctness_fails(tmp_path, app, db):
    db.set(sales=4)  # the application says 3
    proc, data = run_playbook(tmp_path, playbook("postgres-missing-index"), {"app_url": app.url}, db=db)
    assert by_id(data)["reports.counts-correct"]["status"] == "fail"


def test_missing_index_database_tooling_missing_is_error(tmp_path, app, db):
    db.set(mode="broken")
    proc, data = run_playbook(tmp_path, playbook("postgres-missing-index"), {"app_url": app.url}, db=db)
    assert data["status"] == "error"


# ---------------------------------------------------------------- all scenarios
def test_all_eight_scenarios_have_manifests():
    assert SCENARIOS == ["nginx-wrong-api", "postgres-bad-config", "postgres-conn-leak", "postgres-disk-full",
                         "postgres-hba-reject", "postgres-lock", "postgres-missing-index", "postgres-wrong-port"]


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("entry", ["infra", "incident", "verification"])
def test_syntax_check(tmp_path, scenario, entry):
    proc, _ = run_playbook(tmp_path, os.path.join(REPO, scenario, entry, "playbook.yml"), report=False, mode="syntax",
                           cwd=os.path.join(REPO, scenario, entry))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_manifest_matches_vm_json_and_playbook_checks(scenario):
    import yaml

    manifest = yaml.safe_load(open(os.path.join(REPO, scenario, "manifest.yaml")))
    vm = json.load(open(os.path.join(REPO, scenario, manifest["environment"])))
    assert manifest["id"] == vm["name"] == scenario
    assert manifest["actions"]["provision"]["playbook"] == vm["playbooks"]["infra"]
    assert manifest["actions"]["inject"]["playbook"] == vm["playbooks"]["incident"]
    assert manifest["verification"]["playbook"] == vm["playbooks"]["verification"]
    # every required check id is declared by some verification file
    text = "".join(open(p).read() for p in glob.glob(os.path.join(REPO, scenario, "verification", "*.yml")))
    for check_id in manifest["verification"]["report"]["checks"]:
        assert "id: %s" % check_id in text or "id: \"%s" % check_id in text, check_id
    # the migration must not hide failures
    assert "ignore_errors: true" not in text
    assert "incidentsim.verify.check" in text
