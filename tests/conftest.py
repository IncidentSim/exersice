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

from exercise_support import ANSIBLE, ATTEMPT, BIN, COLLECTIONS, REPO, needs_ansible, run_playbook  # noqa: F401


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


# FIXTURE, NOT SERVICE BEHAVIOUR. One fake executable installed under the names
# runuser, psql, pg_isready, systemctl and df. It answers from a JSON state file
# and never touches a database, systemd or the filesystem. What PostgreSQL,
# pg_hba.conf, systemd or df really print is NOT proven by tests using it; only
# the verification playbooks' decisions on such answers are.
SHIM = r'''#!%(python)s
import json, os, sys
tool = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
state_path = os.environ["FAKE_DB_STATE"]
state = json.load(open(state_path))


def nth(key):
    counter = "%%s.%%s" %% (state_path, key)
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    return n


def seq(key, default):
    values = state.get(key, default)
    return values[min(nth(key), len(values) - 1)]


def die(code, message):
    sys.stderr.write(message + "\n")
    sys.exit(code)


if state.get("mode") == "broken" or state.get("missing") == tool:
    die(127, "%%s: No such file or directory" %% tool)
sql = argv[-1] if argv else ""

if tool == "pg_isready":
    if state.get("ready", True):
        print("127.0.0.1:5432 - accepting connections"); sys.exit(0)
    print("127.0.0.1:5432 - no response"); sys.exit(2)

if tool == "systemctl":
    exit_ts, status = seq("backup", [[100, "0"]])
    print("ExecMainStatus=%%s" %% status)
    print("ExecMainExitTimestampMonotonic=%%s" %% exit_ts)
    sys.exit(0)

if tool == "df":
    print("Avail")
    print("%%d" %% seq("free", [500]))
    sys.exit(0)

if tool == "psql":  # direct TCP login: the answer depends on PGPASSWORD
    user = [a.split("=", 1)[1] for a in argv if a.startswith("--username=")][0]
    login, given, expected = state.get("login", "ok"), os.environ.get("PGPASSWORD"), state.get("password")
    refused = 'psql: error: connection to server at "127.0.0.1", port 5432 failed: FATAL:  password authentication failed for user "%%s"' %% user
    if login == "reject_all":
        die(2, 'psql: error: FATAL:  no pg_hba.conf entry for host "127.0.0.1", user "%%s", database "incidentsim"' %% user)
    if login == "unrelated":
        die(2, "psql: error: connection to server at 127.0.0.1 failed: Connection refused")
    if login == "trust_all" or given == expected:
        print(user); sys.exit(0)
    if login == "wrong_unrelated":
        die(2, "psql: error: server closed the connection unexpectedly")
    die(2, refused)

# runuser -u postgres -- psql ... --command <SQL>
if "SHOW max_connections" in sql:
    print(state.get("max_connections", 100)); sys.exit(0)
if "pg_stat_activity" in sql:
    print(seq("sessions", [5])); sys.exit(0)
if "count(*) FROM sales" in sql:
    print(state.get("sales", 3)); sys.exit(0)
if "SHOW data_directory" in sql:
    print(state.get("data_directory", "/")); sys.exit(0)
if "current_setting('work_mem')" in sql:
    print(state.get("settings", "16MB|512MB|134217728|128MB")); sys.exit(0)
die(1, "unexpected SQL: %%s" %% sql)
'''


@pytest.fixture
def db(tmp_path):
    """A runuser shim whose answers are controlled through db.state."""
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    for name in ("runuser", "psql", "pg_isready", "systemctl", "df"):
        shim = bin_dir / name
        shim.write_text(SHIM % {"python": sys.executable})
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    class DB:
        path = str(tmp_path / "db.json")
        shim_dir = str(bin_dir)

        def set(self, **state):
            with open(self.path, "w") as handle:
                json.dump(state, handle)
            for counter in tmp_path.glob("db.json.*"):  # restart every sequence
                counter.unlink()

    handle = DB()
    handle.set()
    return handle
