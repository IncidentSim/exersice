"""Helpers shared by the exercise tests (kept out of conftest.py so that it
cannot clash with the collection's own conftest when both suites run together)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COLLECTIONS = os.path.join(REPO, "collections")
BIN = os.path.dirname(sys.executable)
ANSIBLE = shutil.which("ansible-playbook", path=BIN + os.pathsep + os.environ.get("PATH", ""))
ATTEMPT = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

needs_ansible = pytest.mark.skipif(ANSIBLE is None, reason="ansible-playbook is not installed")


def run_playbook(tmp_path, playbook, extra_vars=None, db=None, report=True, mode="verify", cwd=None):
    """Run a playbook the way the runner does; return (process, report dict)."""
    report_path = tmp_path / "report.json"
    env = dict(os.environ, ANSIBLE_COLLECTIONS_PATH=COLLECTIONS, ANSIBLE_CALLBACKS_ENABLED="incidentsim.verify.report",
               ANSIBLE_NOCOWS="1", ANSIBLE_HOME=str(tmp_path / "home"), ANSIBLE_LOCALHOST_WARNING="False",
               ANSIBLE_INTERPRETER_PYTHON_DISTRO_MAP="{}", PATH=BIN + os.pathsep + os.environ["PATH"])
    if db is not None:
        env["PATH"] = db.shim_dir + os.pathsep + env["PATH"]
        env["FAKE_DB_STATE"] = db.path
    if report:
        env.update(INCIDENTSIM_SCENARIO=os.path.basename(os.path.dirname(os.path.dirname(playbook))),
                   INCIDENTSIM_ATTEMPT_ID=ATTEMPT, INCIDENTSIM_REPORT_PATH=str(report_path))
    cmd = [ANSIBLE, "-i", "localhost,", "-e", "ansible_python_interpreter=" + sys.executable]
    if extra_vars:
        cmd += ["-e", json.dumps(extra_vars)]  # JSON keeps integers typed
    if mode == "syntax":
        cmd.append("--syntax-check")
    proc = subprocess.run(cmd + [playbook], env=env, capture_output=True, text=True, timeout=300, cwd=cwd)
    data = json.loads(report_path.read_text()) if report_path.exists() else None
    return proc, data
