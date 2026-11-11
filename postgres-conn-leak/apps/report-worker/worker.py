#!/usr/bin/env python3
"""report-worker: periodically builds a sales summary from the orders table."""
import os
import time

import psycopg2

DSN = os.getenv("DATABASE_DSN", "dbname=incidentsim")
INTERVAL = float(os.getenv("REPORT_INTERVAL_SECONDS", "4"))

REPORT_SQL = """
    SELECT count(*), coalesce(sum(length(note)), 0), max(created_at)
    FROM orders
"""


def build_report(conn) -> tuple:
    with conn.cursor() as cur:
        cur.execute(REPORT_SQL)
        row = cur.fetchone()
    conn.commit()
    return row


def main() -> None:
    sessions = []
    print(f"report-worker started, interval {INTERVAL}s", flush=True)
    while True:
        try:
            conn = psycopg2.connect(DSN, connect_timeout=3, application_name="report-worker")
            orders, note_chars, last_order = build_report(conn)
            # Keep the session for the next report run.
            sessions.append(conn)
            print(
                f"report: orders={orders} note_chars={note_chars} last={last_order} "
                f"sessions={len(sessions)}",
                flush=True,
            )
        except psycopg2.Error as exc:
            print(f"report failed: {str(exc).strip()}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
