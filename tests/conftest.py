"""Shared fixtures: isolated local HTTP applications and command shims.

Nothing here touches a VM, SSH, systemd or PostgreSQL. The orders-api and the
echo-server are replaced by a tiny local HTTP server, and runuser/psql by a
shim reading its answers from a JSON state file. Real ansible-playbook runs the
real verification playbooks against them.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COLLECTIONS = os.path.join(REPO, "collections")
BIN = os.path.dirname(sys.executable)
ANSIBLE = shutil.which("ansible-playbook", path=BIN + os.pathsep + os.environ.get("PATH", ""))
ATTEMPT = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

needs_ansible = pytest.mark.skipif(ANSIBLE is None, reason="ansible-playbook is not installed")


class App:
    """Mutable behaviour of the fake application."""

    def __init__(self):
        self.mode = {}
        self.orders = {}
        self.latency = 0.0


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            mode = app.mode
            if mode.get("down"):
                return self._send(503, {"error": "down"})
            if self.path == "/":  # echo-server
                host = mode.get("host") or self.headers.get("Host")
                return self._send(200, {"host": host})
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if self.path.startswith("/orders/"):
                oid = self.path.rsplit("/", 1)[1]
                if oid in app.orders:
                    return self._send(200, app.orders[oid])
                return self._send(404, {})
            if self.path.startswith("/customers/"):
                time.sleep(mode.get("latency", 0))
                return self._send(200, {"orders": mode.get("sales", 3)})
            self._send(404, {})

        def do_POST(self):
            if app.mode.get("down") or app.mode.get("write_fail"):
                return self._send(500, {"error": "write failed"})
            time.sleep(app.mode.get("write_latency", 0))
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            oid = str(len(app.orders) + 1)
            app.orders[oid] = dict(body, id=oid)
            self._send(201, {"id": oid})

        def log_message(self, *args):
            pass

    return Handler


@pytest.fixture
def app():
    state = App()
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state.url = "http://127.0.0.1:%d" % httpd.server_address[1]
    yield state
    httpd.shutdown()


SHIM = r'''#!%(python)s
import json, os, sys
state = json.load(open(os.environ["FAKE_DB_STATE"]))
mode = state.get("mode", "ok")
argv = sys.argv[1:]
sql = argv[-1] if argv else ""
if mode == "broken":
    sys.stderr.write("runuser: failed to execute psql: No such file or directory\n"); sys.exit(127)
if "SHOW max_connections" in sql:
    print(state.get("max_connections", 100)); sys.exit(0)
if "pg_stat_activity" in sql:
    counter = os.environ["FAKE_DB_STATE"] + ".n"
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    seq = state.get("sessions", [5])
    value = seq[min(n, len(seq) - 1)]
    print(value); sys.exit(0)
if "count(*) FROM sales" in sql:
    print(state.get("sales", 3)); sys.exit(0)
if "SHOW data_directory" in sql:
    print(state.get("data_directory", "/")); sys.exit(0)
sys.stderr.write("unexpected SQL: %%s\n" %% sql); sys.exit(1)
'''


@pytest.fixture
def db(tmp_path):
    """A runuser shim whose answers are controlled through db.state."""
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    shim = bin_dir / "runuser"
    shim.write_text(SHIM % {"python": sys.executable})
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    class DB:
        path = str(tmp_path / "db.json")
        shim_dir = str(bin_dir)

        def set(self, **state):
            with open(self.path, "w") as handle:
                json.dump(state, handle)
            if os.path.exists(self.path + ".n"):
                os.unlink(self.path + ".n")

    handle = DB()
    handle.set()
    return handle


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
