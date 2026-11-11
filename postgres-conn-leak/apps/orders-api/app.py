#!/usr/bin/env python3
"""orders-api: a minimal HTTP service that stores orders in PostgreSQL."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2

DSN = os.getenv("DATABASE_DSN", "dbname=incidentsim")
MAX_BODY_BYTES = 16384
MAX_ITEM_LENGTH = 200
MAX_NOTE_LENGTH = 4000
ORDER_PATH = re.compile(r"^/orders/(\d+)$")


def connect():
    return psycopg2.connect(DSN, connect_timeout=3)


def run_query(sql: str, params: tuple = ()) -> tuple | None:
    """Run one statement in its own short transaction and return the first row."""
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def error_payload(exc: psycopg2.Error) -> tuple[int, dict[str, str]]:
    message = str(exc).strip() or exc.__class__.__name__
    if exc.pgcode is None:
        return 503, {"error": "database unavailable", "detail": message}
    return 500, {"error": "database error", "sqlstate": exc.pgcode, "detail": message}


class OrdersHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            try:
                run_query("SELECT 1")
            except psycopg2.Error as exc:
                self._write_json(*error_payload(exc))
                return
            self._write_json(200, {"status": "ok"})
            return

        match = ORDER_PATH.match(self.path)
        if not match:
            self._write_json(404, {"error": "not found"})
            return

        try:
            row = run_query(
                "SELECT id, item, note, created_at FROM orders WHERE id = %s",
                (int(match.group(1)),),
            )
        except psycopg2.Error as exc:
            self._write_json(*error_payload(exc))
            return
        if row is None:
            self._write_json(404, {"error": "order not found"})
            return
        self._write_json(
            200,
            {"id": row[0], "item": row[1], "note": row[2], "created_at": row[3].isoformat()},
        )

    def do_POST(self) -> None:
        if self.path != "/orders":
            self._write_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY_BYTES:
            self._write_json(413 if length > 0 else 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_json(400, {"error": "body must be JSON"})
            return

        item = payload.get("item") if isinstance(payload, dict) else None
        note = payload.get("note", "") if isinstance(payload, dict) else None
        if not isinstance(item, str) or not 0 < len(item) <= MAX_ITEM_LENGTH:
            self._write_json(400, {"error": f"item must be a string of 1..{MAX_ITEM_LENGTH} characters"})
            return
        if not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH:
            self._write_json(400, {"error": f"note must be a string of at most {MAX_NOTE_LENGTH} characters"})
            return

        try:
            row = run_query(
                "INSERT INTO orders (item, note) VALUES (%s, %s) RETURNING id",
                (item, note),
            )
        except psycopg2.Error as exc:
            self._write_json(*error_payload(exc))
            return
        self._write_json(201, {"id": row[0]})

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: object) -> None:
        print(f"{self.address_string()} - {message % args}", flush=True)


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), OrdersHandler)
    print(f"orders-api listening on {host}:{port}", flush=True)
    server.serve_forever()
