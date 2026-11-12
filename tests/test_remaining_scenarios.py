"""bad-config, hba-reject, wrong-port and disk-full verification playbooks.

FIXTURE vs. REAL BEHAVIOUR: real ansible-playbook runs the real playbooks, but
psql, runuser, pg_isready, systemctl and df are fake executables answering from
a JSON state file and the application is a local HTTP server (see conftest.py).
No database, systemd unit, filesystem or VM is touched. These tests prove the
playbooks' decisions (pass / explicit fail / technical error) for given
answers; they do NOT prove what PostgreSQL, pg_hba.conf, systemd or df print.
"""
import os

import pytest

from exercise_support import REPO, needs_ansible, run_playbook

pytestmark = needs_ansible

# An arbitrary secret without any "password=" style key in front of it.
SECRET = "Zq9-sentinel-7f3a-Qx"


def playbook(name):
    return os.path.join(REPO, name, "verification", "playbook.yml")


def by_id(data):
    return {c["id"]: c for c in data["checks"]}


def password_file(tmp_path):
    path = tmp_path / "orders-api.password"
    path.write_text(SECRET + "\n")
    return str(path)


# ------------------------------------------------------------ postgres-bad-config
def test_bad_config_pass(tmp_path, app, db):
    proc, data = run_playbook(tmp_path, playbook("postgres-bad-config"), {"app_url": app.url}, db=db)
    assert proc.returncode == 0 and data["status"] == "pass", proc.stdout
    assert set(by_id(data)) == {"db.accepts-connections", "settings.tuning-retained", "settings.shared-buffers-fit",
                                "app.write-accepted", "app.read-back"}


def test_bad_config_tuning_release_reverted_fails(tmp_path, app, db):
    db.set(settings="4MB|512MB|134217728|128MB")
    proc, data = run_playbook(tmp_path, playbook("postgres-bad-config"), {"app_url": app.url}, db=db)
    check = by_id(data)["settings.tuning-retained"]
    assert data["status"] == "fail" and check["status"] == "fail" and "4MB" in check["observed"] and "16MB" in check["expected"]


def test_bad_config_oversized_shared_buffers_fails(tmp_path, app, db):
    db.set(settings="16MB|512MB|999999999999999|900TB")
    proc, data = run_playbook(tmp_path, playbook("postgres-bad-config"), {"app_url": app.url}, db=db)
    assert by_id(data)["settings.shared-buffers-fit"]["status"] == "fail" and data["status"] == "fail"


def test_bad_config_application_write_failure_fails(tmp_path, app, db):
    app.mode["write_fail"] = True
    proc, data = run_playbook(tmp_path, playbook("postgres-bad-config"), {"app_url": app.url}, db=db)
    assert by_id(data)["app.write-accepted"]["status"] == "fail"


def test_bad_config_missing_psql_is_error_not_fail(tmp_path, app, db):
    db.set(mode="broken")
    proc, data = run_playbook(tmp_path, playbook("postgres-bad-config"), {"app_url": app.url}, db=db)
    assert data["status"] == "error" and by_id(data)["db.accepts-connections"]["status"] == "error"


# ------------------------------------------------------------ postgres-hba-reject
def hba_vars(tmp_path, app):
    return {"app_url": app.url, "app_password_file": password_file(tmp_path)}


def test_hba_pass_and_secret_stays_out_of_every_output(tmp_path, app, db):
    db.set(password=SECRET)
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"), hba_vars(tmp_path, app), db=db)
    assert proc.returncode == 0 and data["status"] == "pass", proc.stdout
    assert SECRET not in proc.stdout + proc.stderr + (tmp_path / "report.json").read_text()


def test_hba_rejecting_the_application_role_fails_correct_login(tmp_path, app, db):
    db.set(password=SECRET, login="reject_all")
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"), hba_vars(tmp_path, app), db=db)
    check = by_id(data)["db.correct-password-accepted"]
    assert data["status"] == "fail" and check["status"] == "fail" and "pg_hba.conf" in check["evidence"]
    assert SECRET not in proc.stdout + proc.stderr + (tmp_path / "report.json").read_text()


def test_hba_wrong_password_accepted_fails_the_negative_test(tmp_path, app, db):
    db.set(password=SECRET, login="trust_all")  # authentication is not enforced
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"), hba_vars(tmp_path, app), db=db)
    assert by_id(data)["db.wrong-password-refused"]["status"] == "fail" and data["status"] == "fail"


@pytest.mark.parametrize("login", ["wrong_unrelated"])
def test_hba_unrelated_failure_is_not_password_denial(tmp_path, app, db, login):
    # the wrong password is refused with rc!=0, but NOT by password authentication
    db.set(password=SECRET, login=login)
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"), hba_vars(tmp_path, app), db=db)
    check = by_id(data)["db.wrong-password-refused"]
    assert check["status"] == "fail" and data["status"] == "fail"
    assert "password authentication failed" in check["expected"] and "unexpectedly" in check["evidence"]


def test_hba_database_down_never_counts_as_a_denial(tmp_path, app, db):
    db.set(password=SECRET, login="unrelated")  # connection refused for everyone
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"), hba_vars(tmp_path, app), db=db)
    assert by_id(data)["db.correct-password-accepted"]["status"] == "fail" and data["status"] == "fail"
    assert "db.wrong-password-refused" not in by_id(data)  # the play stopped at the first failure


def test_hba_missing_psql_is_error(tmp_path, app, db):
    db.set(password=SECRET, missing="psql")
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"), hba_vars(tmp_path, app), db=db)
    assert data["status"] == "error"
    assert SECRET not in proc.stdout + proc.stderr + (tmp_path / "report.json").read_text()


def test_hba_unreadable_credential_is_error(tmp_path, app, db):
    db.set(password=SECRET)
    proc, data = run_playbook(tmp_path, playbook("postgres-hba-reject"),
                              {"app_url": app.url, "app_password_file": str(tmp_path / "absent")}, db=db)
    assert data["status"] == "error"


# ------------------------------------------------------------ postgres-wrong-port
def test_wrong_port_pass(tmp_path, app, db):
    db.set(password=SECRET)
    proc, data = run_playbook(tmp_path, playbook("postgres-wrong-port"), hba_vars(tmp_path, app), db=db)
    assert proc.returncode == 0 and data["status"] == "pass", proc.stdout
    assert SECRET not in proc.stdout + proc.stderr + (tmp_path / "report.json").read_text()


def test_wrong_port_no_listener_fails_before_login(tmp_path, app, db):
    db.set(password=SECRET, ready=False)
    proc, data = run_playbook(tmp_path, playbook("postgres-wrong-port"), hba_vars(tmp_path, app), db=db)
    check = by_id(data)["db.tcp-ready"]
    assert data["status"] == "fail" and check["status"] == "fail" and "no response" in check["observed"]


def test_wrong_port_login_failure_fails(tmp_path, app, db):
    db.set(password=SECRET, login="unrelated")
    proc, data = run_playbook(tmp_path, playbook("postgres-wrong-port"), hba_vars(tmp_path, app), db=db)
    assert by_id(data)["db.correct-password-accepted"]["status"] == "fail"


def test_wrong_port_missing_pg_isready_is_error(tmp_path, app, db):
    db.set(password=SECRET, missing="pg_isready")
    proc, data = run_playbook(tmp_path, playbook("postgres-wrong-port"), hba_vars(tmp_path, app), db=db)
    assert data["status"] == "error" and by_id(data)["db.tcp-ready"]["status"] == "error"


# ------------------------------------------------------------ postgres-disk-full
def disk_vars(app):
    return {"app_url": app.url, "sample_count": 4, "sample_interval": 1, "min_backup_runs": 2}


HEALTHY_BACKUPS = [[100, "0"], [200, "0"], [300, "0"], [400, "0"]]


def test_disk_full_pass(tmp_path, app, db):
    db.set(backup=HEALTHY_BACKUPS, free=[500, 498, 499, 500])
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    assert proc.returncode == 0 and data["status"] == "pass", proc.stdout
    ids = by_id(data)
    for required in ("db.accepts-connections", "window.writes-served", "backups.running", "storage.free-space-stable"):
        assert ids[required]["status"] == "pass"


def test_disk_full_disabled_backups_fail(tmp_path, app, db):
    # the timestamp never advances: the backup timer was disabled or stopped
    db.set(backup=[[100, "0"]], free=[500, 500, 500, 500])
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    check = by_id(data)["backups.running"]
    assert data["status"] == "fail" and check["status"] == "fail" and "0 successful runs" in check["observed"]


def test_disk_full_failing_backups_do_not_count(tmp_path, app, db):
    db.set(backup=[[100, "0"], [200, "1"], [300, "1"], [400, "1"]], free=[500, 500, 500, 500])
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    assert by_id(data)["backups.running"]["status"] == "fail"


def test_disk_full_declining_free_space_fails(tmp_path, app, db):
    db.set(backup=HEALTHY_BACKUPS, free=[500, 300, 100, 20])
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    check = by_id(data)["storage.free-space-stable"]
    assert data["status"] == "fail" and check["status"] == "fail"
    assert "500, 300, 100, 20" in check["observed"] and "decline 480" in check["observed"]


@pytest.mark.parametrize("free", [[500, 480, 450, 440], [30, 30, 30, 30]])
def test_disk_full_slow_decline_or_low_floor_fails(tmp_path, app, db, free):
    db.set(backup=HEALTHY_BACKUPS, free=free)
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    assert by_id(data)["storage.free-space-stable"]["status"] == "fail"


def test_disk_full_write_failure_fails(tmp_path, app, db):
    app.mode["write_fail"] = True
    db.set(backup=HEALTHY_BACKUPS, free=[500] * 4)
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    failing = [c for c in data["checks"] if c["status"] == "fail"]
    assert failing and failing[0]["id"].startswith("window.accepted.")


@pytest.mark.parametrize("missing", ["df", "systemctl"])
def test_disk_full_missing_tools_are_errors_not_passes(tmp_path, app, db, missing):
    db.set(backup=HEALTHY_BACKUPS, free=[500] * 4, missing=missing)
    proc, data = run_playbook(tmp_path, playbook("postgres-disk-full"), disk_vars(app), db=db)
    assert data["status"] == "error"
