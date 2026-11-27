"""Back-up maken (sqlledger.dbbackup, FASE6_ANALYSE.md 6d-1, I7) en weer inlezen met sqlledger.slbackup."""

import gzip
import os
import uuid
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

import sqlledger
from sqlledger import dbbackup, slbackup
from sqlledger.dbbackup import DumpError
from sqlledger.extensions import ensure_schema
from sqlledger.slbackup import BackupError

DSN = os.environ.get("SQLLEDGER_TEST_DSN", "host=127.0.0.1 port=55432 user=postgres")
TEMPLATE = os.environ.get("SQLLEDGER_TEST_TEMPLATE", "sl_template")
PARAMS = psycopg.conninfo.conninfo_to_dict(DSN)


def test_restrict_lines_of_newer_pg_dump_are_skipped():
    """pg_dump ≥ 15.14/16.10/17.6 zet ``\\restrict``/``\\unrestrict`` om de dump; andere psql-opdrachten niet."""
    text = ("-- PostgreSQL database dump\n\\restrict AbC123xyz\nCREATE TABLE a (x int);\n"
            "\\unrestrict AbC123xyz\n")
    assert [s.text for s in slbackup.split(text)] == ["CREATE TABLE a (x int)"]
    for bad in ("\\restrict", "\\restrict a b", "\\restrict a;b", "\\! id", "\\copy a to '/tmp/x'"):
        with pytest.raises(BackupError, match="psql-opdracht"):
            list(slbackup.split(f"CREATE TABLE a (x int);\n{bad}\n"))


def test_header_is_recognised():
    made = datetime(2026, 12, 31, 23, 5)
    text = dbbackup.header("adm_a", "3.2.4", "Voorbeeld B.V.", made) + "--\n-- PostgreSQL database dump\n--\n"
    b = slbackup.info(text)
    assert (b.kind, b.dataset, b.version, b.date) == ("app", "adm_a", "3.2.4", "2026-12-31 23:05")
    assert dbbackup.filename("adm_a", made) == "adm_a-20261231-2305.sql.gz"
    # SQL-Ledger (am.pl restore) zoekt ongeankerd naar deze twee; die mogen er niet in staan (keuze 1a)
    assert "-- Version:" not in text and "-- Dataset:" not in text
    # back-ups van vóór keuze 1a (kop met "-- Dataset:"/"-- Version:") blijven leesbaar
    old = text.replace("-- App-Dataset:", "-- Dataset:").replace("-- App-Version:", "-- Version:")
    b = slbackup.info(old)
    assert (b.kind, b.dataset, b.version) == ("app", "adm_a", "3.2.4")


def test_rotate_keeps_newest_and_ignores_other_files(tmp_path):
    names = [f"adm_a-2026120{d}-0100.sql.gz" for d in range(1, 6)] + ["adm_a-20261201-0100-sjablonen.zip",
                                                                     "adm_a_oud-20261201-0100.sql.gz", "notities.txt"]
    for n in names:
        (tmp_path / n).write_text("x")
    gone = dbbackup.rotate(tmp_path, "adm_a", 2)
    assert sorted(p.name for p in gone) == names[:3]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names[3:])
    assert dbbackup.rotate(tmp_path, "adm_a", 2, "-sjablonen.zip") == []
    assert dbbackup.rotate(tmp_path, "adm_a", 0) == []                    # 0 = niets weggooien


@pytest.fixture
def admin():
    try:
        conn = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("geen test-PostgreSQL bereikbaar")
    made = []
    yield conn, made
    for name in made:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    conn.close()


def _connect(db):
    return psycopg.connect(f"{DSN} dbname={db}", autocommit=True)


def _info(db):
    return {**PARAMS, "dbname": db}


PUBLIC = "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace " \
         "WHERE n.nspname = 'public' AND relkind = 'r' ORDER BY 1"
CATALOG = {
    "sequences": "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1",
    "functions": "SELECT proname, md5(prosrc) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                 "WHERE nspname = 'public' ORDER BY 1, 2",
    "triggers": "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1",
    "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1",
    "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
                   "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2",
}


def test_dump_and_restore_round_trip(admin, tmp_path):
    """Back-up van een administratie met de eigen x_-tabellen en een bijlage, weer inlezen: elke tabel, sequence,
    functie, trigger, index en constraint gelijk; geen bijwerkstappen."""
    conn, made = admin
    src, dst = f"bk_{uuid.uuid4().hex[:8]}", f"bk_{uuid.uuid4().hex[:8]}"
    made += [src, dst]
    conn.execute(f'CREATE DATABASE "{src}" TEMPLATE "{TEMPLATE}"')
    with sqlledger.connect(**_info(src)) as c:
        ensure_schema(c)
        c.execute("INSERT INTO x_setting (key, value, login) VALUES ('proef', 'één; \\ twee', 'x')")
        c.execute("INSERT INTO archive (filename) VALUES ('scan.pdf') RETURNING id")
        c.commit()
    res = dbbackup.dump(_info(src), tmp_path / "proef.sql.gz", version="3.2.4", company="Proef")
    assert res.size == (tmp_path / "proef.sql.gz").stat().st_size and res.version == "3.2.4"
    assert [p.name for p in tmp_path.iterdir()] == ["proef.sql.gz"]            # geen tijdelijk bestand over
    text = gzip.decompress(res.path.read_bytes()).decode()
    assert text.startswith(dbbackup.HEADER) and "-- Company: Proef" in text and "OWNER TO" not in text
    restored = slbackup.restore(conn, _connect, dst, res.path.read_bytes())
    assert restored.info.kind == "app" and restored.upgrade.applied == []
    with _connect(src) as a, _connect(dst) as b:
        tables = [r[0] for r in a.execute(PUBLIC)]
        assert tables == [r[0] for r in b.execute(PUBLIC)] and "x_setting" in tables
        for t in tables:
            q = f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM \"{t}\" t"
            assert a.execute(q).fetchone() == b.execute(q).fetchone(), t
        for name, q in CATALOG.items():
            assert a.execute(q).fetchall() == b.execute(q).fetchall(), name


def test_dump_errors_leave_no_file(admin, tmp_path):
    with pytest.raises(DumpError, match="pg_dump mislukt"):
        dbbackup.dump(_info(f"bestaat_niet_{uuid.uuid4().hex[:6]}"), tmp_path / "x.sql.gz")
    with pytest.raises(DumpError, match="niet gevonden"):
        dbbackup.dump(_info(TEMPLATE), tmp_path / "y.sql.gz", pg_dump=str(tmp_path / "geen_pg_dump"))
    assert list(tmp_path.iterdir()) == []
