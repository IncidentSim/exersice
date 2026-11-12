"""Run real ansible-playbook against an isolated local HTTP fixture.

No VM, SSH or external network is involved: the playbooks target localhost with
a local connection and an HTTP server bound to 127.0.0.1.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from collection_support import COLLECTIONS

ANSIBLE = shutil.which("ansible-playbook", path=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))
pytestmark = pytest.mark.skipif(ANSIBLE is None, reason="ansible-playbook is not installed")
ATTEMPT = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code, body = {"/ok": (200, {"host": "x"}), "/missing": (404, {})}.get(self.path, (500, {}))
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()


PROBE = """
    - ansible.builtin.uri:
        url: "{{ base }}%s"
        return_content: true
        status_code: [200]
        timeout: 5
      register: probe
      failed_when: false
"""
HEALTH_CHECK = """
    - incidentsim.verify.check:
        id: app.health
        description: Application answers
        passed: "{{ probe | incidentsim.verify.dig('status') == 200 }}"
        error: "{{ probe | incidentsim.verify.technical_error }}"
        observed: "{{ probe | incidentsim.verify.dig('status') }}"
        expected: 200
"""
HEAD = """---
- hosts: localhost
  connection: local
  gather_facts: false
  vars:
    base: "%s"
  tasks:
"""


def run(tmp_path, server, tasks, report=True, extra_env=None):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(HEAD % server + tasks)
    path = tmp_path / "report.json"
    env = dict(os.environ, ANSIBLE_COLLECTIONS_PATH=COLLECTIONS, ANSIBLE_CALLBACKS_ENABLED="incidentsim.verify.report",
               ANSIBLE_NOCOWS="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_HOME=str(tmp_path / "home"))
    if report:
        env.update(INCIDENTSIM_SCENARIO="fixture", INCIDENTSIM_ATTEMPT_ID=ATTEMPT, INCIDENTSIM_REPORT_PATH=str(path))
    env.update(extra_env or {})
    proc = subprocess.run([ANSIBLE, "-i", "localhost,", str(playbook)], env=env, capture_output=True, text=True, timeout=120)
    data = json.loads(path.read_text()) if path.exists() else None
    return proc, data


def test_pass(tmp_path, server):
    proc, data = run(tmp_path, server, PROBE % "/ok" + HEALTH_CHECK)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data["status"] == "pass" and data["complete"] is True
    assert data["scenario"] == "fixture" and data["attempt_id"] == ATTEMPT
    assert data["checks"][0]["observed"] == "200" and data["checks"][0]["expected"] == "200"


def test_explicit_failure_is_fail_not_error(tmp_path, server):
    proc, data = run(tmp_path, server, PROBE % "/missing" + HEALTH_CHECK)
    assert proc.returncode != 0
    assert data["status"] == "fail"
    assert data["checks"] == [dict(data["checks"][0], id="app.health", status="fail", observed="404", expected="200")]


def test_refused_connection_is_a_symptom_fail(tmp_path, server):
    tasks = (PROBE % "/ok").replace('{{ base }}', 'http://127.0.0.1:1') + HEALTH_CHECK
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "fail", data


def test_technical_error_in_observation_is_error(tmp_path, server):
    tasks = """
    - ansible.builtin.command:
        argv: [/nonexistent/psql, --version]
      register: probe
      changed_when: false
      failed_when: false
""" + HEALTH_CHECK
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "error", data
    assert data["checks"][0]["status"] == "error" and data["checks"][0]["evidence"]


def test_unchecked_task_failure_is_captured_by_callback(tmp_path, server):
    tasks = """
    - ansible.builtin.command:
        argv: [/nonexistent/tool]
      changed_when: false
"""
    proc, data = run(tmp_path, server, tasks)
    assert proc.returncode != 0
    assert data["status"] == "error"
    assert data["checks"][0]["id"].startswith("task.") and data["checks"][0]["status"] == "error"


def test_templating_failure_does_not_become_a_pass(tmp_path, server):
    tasks = """
    - incidentsim.verify.check:
        id: broken.template
        passed: "{{ undefined_variable.attribute }}"
"""
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "error" and data["checks"][0]["id"] == "broken.template"


def test_ignored_failures_are_the_playbooks_business(tmp_path, server):
    tasks = """
    - ansible.builtin.command: {argv: [/nonexistent/x]}
      ignore_errors: true
""" + PROBE % "/ok" + HEALTH_CHECK
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "pass" and len(data["checks"]) == 1


def test_empty_playbook_never_passes(tmp_path, server):
    proc, data = run(tmp_path, server, "    - ansible.builtin.debug: {msg: nothing checked}\n")
    assert data["status"] == "error" and data["checks"] == []


def test_no_report_without_runner_inputs(tmp_path, server):
    proc, data = run(tmp_path, server, PROBE % "/ok" + HEALTH_CHECK, report=False)
    assert proc.returncode == 0 and data is None


def test_loop_items_are_reported_individually(tmp_path, server):
    tasks = """
    - incidentsim.verify.check:
        id: "item.{{ item }}"
        passed: "{{ item != 'b' }}"
        observed: "{{ item }}"
        expected: not b
      loop: [a, b, c]
      ignore_errors: true
"""
    proc, data = run(tmp_path, server, tasks)
    assert [(c["id"], c["status"]) for c in data["checks"]] == [("item.a", "pass"), ("item.b", "fail"), ("item.c", "pass")]
    assert data["status"] == "fail"


def test_secrets_never_reach_the_report(tmp_path, server):
    tasks = """
    - incidentsim.verify.check:
        id: leak.test
        passed: false
        observed: "PGPASSWORD=hunter2 password: hunter2"
"""
    proc, data = run(tmp_path, server, tasks)
    assert "hunter2" not in json.dumps(data)


def test_metrics_and_logs_unavailable_become_error(tmp_path, server):
    tasks = """
    - incidentsim.verify.metrics:
        samples: []
        window_seconds: 60
        aggregate: max
    - incidentsim.verify.check: {id: never.reached, passed: true}
"""
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "error" and data["checks"][0]["id"].startswith("task.")
    tasks = """
    - incidentsim.verify.logs:
        text: ""
        since: 0
        until: 100
        pattern: ERROR
        expect: absent
"""
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "error"


def test_metrics_spread_in_a_playbook(tmp_path, server):
    tasks = """
    - incidentsim.verify.metrics:
        samples: [{ts: 0, value: 4}, {ts: 10, value: 5}, {ts: 20, value: 6}]
        window_seconds: 20
        aggregate: spread
        min_coverage: 0.9
      register: m
    - incidentsim.verify.check:
        id: sessions.stable
        passed: "{{ m.value <= 3 }}"
        observed: "{{ m.value }}"
"""
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "pass", data


def test_no_log_task_failure_never_leaks_into_the_report(tmp_path, server):
    tasks = """
    - ansible.builtin.command:
        argv: [/nonexistent/tool, "--password=hunter2"]
      environment:
        PGPASSWORD: hunter2
      no_log: true
"""
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "error"
    assert "hunter2" not in json.dumps(data)
    assert "hunter2" not in proc.stdout + proc.stderr


def test_no_log_check_result_is_an_error_without_details(tmp_path, server):
    tasks = """
    - incidentsim.verify.check:
        id: secret.check
        passed: false
        observed: "token: topsecretvalue"
      no_log: true
"""
    proc, data = run(tmp_path, server, tasks)
    assert data["status"] == "error" and "topsecretvalue" not in json.dumps(data)
