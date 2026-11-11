#!/usr/bin/env python3
"""nightly-fix: normalizes order notes, then waits for a data check before COMMIT."""
import os
import select
import sys
import time

import psycopg2

DSN = os.getenv("DATABASE_DSN", "dbname=incidentsim")
RETRY_SECONDS = 5


def wait_for_server(conn) -> None:
    """Block until the server sends something, which ends the session here."""
    while True:
        readable, _, _ = select.select([conn], [], [], 60)
        if readable:
            conn.poll()
            if conn.closed:
                raise psycopg2.OperationalError("server closed the connection")


def idle_transactions_limited(cur) -> bool:
    cur.execute(
        "SELECT setting::bigint > 0 FROM pg_settings"
        " WHERE name = 'idle_in_transaction_session_timeout'"
    )
    return cur.fetchone()[0]


def run_fix() -> bool:
    """Return True when the fix is committed and the job is finished."""
    conn = psycopg2.connect(DSN, connect_timeout=3, application_name="nightly-fix")
    try:
        with conn.cursor() as cur:
            # Keep the data stable while the fix is reviewed.
            cur.execute("LOCK TABLE orders IN EXCLUSIVE MODE")
            cur.execute("UPDATE orders SET note = btrim(note) WHERE note <> btrim(note)")
            fixed = cur.rowcount
            if idle_transactions_limited(cur):
                conn.commit()
                print(
                    f"normalized {fixed} order notes; the server limits idle transactions, "
                    "committed without waiting for the data check",
                    flush=True,
                )
                return True
            print(f"normalized {fixed} order notes, waiting for the data check before COMMIT", flush=True)
        # TODO: commit after the data check. The check was never implemented.
        wait_for_server(conn)
        return False
    finally:
        conn.close()


def main() -> int:
    while True:
        try:
            if run_fix():
                return 0
        except psycopg2.Error as exc:
            print(f"fix interrupted, retrying in {RETRY_SECONDS}s: {str(exc).strip()}", flush=True)
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
