#!/usr/bin/env python3
"""sales-api: per-customer sales reports from PostgreSQL."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2

DSN = os.getenv("DATABASE_DSN", "dbname=incidentsim")
CUSTOMER_SALES_PATH = re.compile(r"^/customers/(\d+)/sales$")


def run_query(sql: str, params: tuple = ()) -> tuple | None:
    conn = psycopg2.connect(DSN, connect_timeout=3)
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


class SalesHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            try:
                run_query("SELECT 1")
            except psycopg2.Error as exc:
                self._write_json(*error_payload(exc))
                return
            self._write_json(200, {"status": "ok"})
            return

        match = CUSTOMER_SALES_PATH.match(self.path)
        if not match:
            self._write_json(404, {"error": "not found"})
            return

        customer_id = int(match.group(1))
        try:
            row = run_query(
                "SELECT count(*), coalesce(sum(amount), 0), max(sold_at)"
                " FROM sales WHERE customer_id = %s",
                (customer_id,),
            )
        except psycopg2.Error as exc:
            self._write_json(*error_payload(exc))
            return
        self._write_json(
            200,
            {
                "customer_id": customer_id,
                "orders": row[0],
                "total": str(row[1]),
                "last_sale": row[2].isoformat() if row[2] else None,
            },
        )

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
    server = ThreadingHTTPServer((host, port), SalesHandler)
    print(f"sales-api listening on {host}:{port}", flush=True)
    server.serve_forever()
