"""Eigen ``x_``-tabellen (``sqlledger/extensions.py``)."""

import threading

import psycopg

from sqlledger.extensions import MIGRATIONS, ensure_schema


def test_ensure_schema_concurrently_on_fresh_database(conn):
    """Eerste keer openen van een gekoppelde database: de dashboardblokken roepen ensure_schema tegelijk aan.
    Zonder slot vóór CREATE TABLE botste dat op pg_type (gevonden bij de eerste start met een back-up)."""
    dsn = conn.info.dsn
    errors, versions = [], []
    start = threading.Barrier(6)

    def run():
        try:
            with psycopg.connect(dsn, autocommit=True) as c:
                start.wait()
                versions.append(ensure_schema(c))
        except Exception as exc:                # noqa: BLE001 - de toets telt elke fout
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and versions == [len(MIGRATIONS)] * 6
