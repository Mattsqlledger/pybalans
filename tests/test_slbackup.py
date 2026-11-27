"""SQL-Ledger-back-up inlezen en oudere versies bijwerken (sqlledger.slbackup/slupgrade, FASE6_ANALYSE.md F1–F4)."""

import gzip
import os
import subprocess
import uuid

import psycopg
import pytest

from sqlledger import slbackup, slupgrade
from sqlledger.slbackup import BackupError

DSN = os.environ.get("SQLLEDGER_TEST_DSN", "host=127.0.0.1 port=55432 user=postgres")
HEAD = "-- SQL-Ledger Backup\n-- Dataset: proef\n-- Version: 3.2.4\n--\n"


def stmts(text):
    return [(s.line, s.text, s.copy_data) for s in slbackup.split(text)]


def test_split_quotes_dollar_comments_and_copy():
    text = ("CREATE TABLE a (x text);\n-- commentaar; met puntkomma\n"
            "INSERT INTO a (x) VALUES ('één; twee\nregel ''drie''');\n"
            "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;\n"
            "/* blok; */ COPY a (x) FROM stdin;\nrij 1\nrij;2\n\\.\nSELECT setval('id', 5);\n")
    got = stmts(text)
    assert [g[0] for g in got] == [1, 3, 5, 6, 10]
    assert got[1][1] == "INSERT INTO a (x) VALUES ('één; twee\nregel ''drie''')"
    assert got[2][1].endswith("LANGUAGE plpgsql") and "RETURN 1; END;" in got[2][1]
    assert got[3][1] == "COPY a (x) FROM stdin" and got[3][2] == "rij 1\nrij;2\n"
    with pytest.raises(BackupError, match="aanhalingsteken"):
        list(slbackup.split("INSERT INTO a VALUES ('open);"))


@pytest.mark.parametrize("sql, message", [
    ("COPY a TO '/tmp/x'", "hoort niet"),
    ("COPY a FROM PROGRAM 'rm -rf /'", "hoort niet"),
    ("CREATE EXTENSION plpython3u", "hoort niet"),
    ("CREATE FUNCTION f() RETURNS int AS 'import os' LANGUAGE plpython3u", "plpgsql of sql"),
    ("CREATE ROLE baas SUPERUSER", "hoort niet"),
    ("SET session_authorization = 'postgres'", "SET session_authorization"),
    ("UPDATE pg_authid SET rolsuper = true", "hoort niet"),
    ("DO $$ BEGIN PERFORM 1; END $$", "hoort niet"),
])
def test_dangerous_statements_are_refused(sql, message):
    with pytest.raises(BackupError, match=message):
        slbackup.check(slbackup.Statement(7, sql))


def test_skipped_and_psql_commands():
    for sql in ("DROP TABLE acc_trans", "ALTER TABLE ONLY public.a OWNER TO sl", "GRANT ALL ON a TO PUBLIC",
                "COMMENT ON TABLE a IS 'x'", "SELECT pg_catalog.set_config('search_path', '', false)"):
        assert slbackup.check(slbackup.Statement(1, sql)) == "skip", sql
    with pytest.raises(BackupError, match="psql-opdracht"):
        list(slbackup.split("CREATE TABLE a (x int);\n\\! rm -rf /\n"))
    with pytest.raises(BackupError, match="geen back-up"):
        slbackup.scan(b"CREATE TABLE a (x int);")


def test_versions_and_scripts():
    assert slupgrade.calc_version("2.8.10") == 2008010 and slupgrade.calc_version("3.2.4") > slupgrade.calc_version("2.8.37")
    names = slupgrade.scripts()
    assert len(names) == 70 and names[0] == "Pg-upgrade-1.2.6-1.2.7.sql" and names[-1] == "Pg-upgrade-3.2.3-3.2.4.sql"
    assert names.index("Pg-upgrade-2.6.7-2.6.12.sql") < names.index("Pg-upgrade-2.6.12-2.7.0.sql")


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


def test_restore_pg_dump_of_template_gzip(admin):
    """Een pg_dump (platte SQL, gzip) van de testadministratie terugzetten: zelfde rijen, versie 3.2.4."""
    conn, made = admin
    env = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": "55432", "PGUSER": "postgres"}
    dump = subprocess.run(["pg_dump", "-O", "-x", "sl_template"], env=env, capture_output=True, check=True).stdout
    name = f"rb_{uuid.uuid4().hex[:8]}"
    made.append(name)
    res = slbackup.restore(conn, _connect, name, gzip.compress(dump))
    assert res.info.kind == "pg_dump" and res.upgrade.applied == [] and res.rows_copied > 0
    with _connect(name) as new, _connect("sl_template") as old:
        for table in ("acc_trans", "ar", "customer", "chart", "defaults"):
            q = f"SELECT count(*) FROM {table}"
            assert new.execute(q).fetchone() == old.execute(q).fetchone(), table


def test_failed_restore_leaves_nothing(admin):
    conn, made = admin
    name = f"rb_{uuid.uuid4().hex[:8]}"
    made.append(name)
    bad = (HEAD + "CREATE TABLE a (x int);\nINSERT INTO bestaat_niet VALUES (1);\n").encode()
    with pytest.raises(BackupError, match="Regel 6"):
        slbackup.restore(conn, _connect, name, bad)
    assert not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()


def test_newer_database_refused(admin):
    conn, made = admin
    name = f"rb_{uuid.uuid4().hex[:8]}"
    made.append(name)
    conn.execute(f'CREATE DATABASE "{name}"')
    with _connect(name) as c:
        c.execute("CREATE TABLE defaults (fldname text, fldvalue text)")
        c.execute("INSERT INTO defaults VALUES ('version', '4.2.0')")
        with pytest.raises(slupgrade.UpgradeError, match="nieuwer"):
            slupgrade.upgrade(c)


def test_plan_and_progress(admin):
    """v1.0.1 PB4: ``plan`` geeft dezelfde scripts als het bijwerken zelf; voortgang per fase."""
    assert slupgrade.plan(slupgrade.calc_version("3.2.4"), slupgrade.calc_version("3.2.4")) == []
    old = slupgrade.plan(slupgrade.calc_version("2.8.37"), slupgrade.calc_version("3.2.4"))
    assert old[0].startswith("Pg-upgrade-2.8.") and old[-1] == "Pg-upgrade-3.2.3-3.2.4.sql"
    conn, made = admin
    env = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": "55432", "PGUSER": "postgres"}
    dump = subprocess.run(["pg_dump", "-O", "-x", "sl_template"], env=env, capture_output=True, check=True).stdout
    name = f"rb_{uuid.uuid4().hex[:8]}"
    made.append(name)
    seen = []
    slbackup.restore(conn, _connect, name, dump, progress=lambda phase, done, total: seen.append((phase, done, total)))
    phases = [p for p, _d, _t in seen]
    assert phases[0] == "check" and phases.index("restore") < phases.index("upgrade") < phases.index("finish")
    assert ("restore", seen[-3][2], seen[-3][2]) in seen and ("upgrade", 0, 0) in seen
    assert all(0 <= d <= t for p, d, t in seen if p in ("check", "restore"))


def test_split_positions_and_speed():
    """Statements weten waar ze eindigen (voortgang terugzetten); grote tekst zonder bijzondere tekens in één keer."""
    text = "INSERT INTO a VALUES ('x');\n" + "INSERT INTO a VALUES (" + "1" * 200000 + ");\n"
    got = list(slbackup.split(text))
    assert got[0].end == text.index(";") + 1 and got[1].end == len(text) - 1


def test_patch_exchangerate_keeps_sell_only_rates(admin):
    """AD1 (ADM-15): het oorspronkelijke statement staat nog in het script (anders werkt de correctie stil niet) en
    een koers met alleen een verkoopkoers overleeft Pg-upgrade-2.9.1-2.9.2."""
    from importlib import resources
    base = resources.files("sqlledger.slschema").joinpath("upgrade")
    for (script, original), _new in slupgrade.PATCHES.items():
        sqls = [s.strip() for _l, s in slupgrade.statements(base.joinpath(script).read_text(encoding="utf-8"))]
        assert original in sqls, script
    conn, made = admin
    name = f"rb_{uuid.uuid4().hex[:8]}"
    made.append(name)
    conn.execute(f'CREATE DATABASE "{name}"')
    with _connect(name) as c:
        c.execute("CREATE TABLE defaults (fldname text, fldvalue text)")
        c.execute("INSERT INTO defaults VALUES ('version', '2.9.1')")
        c.execute("CREATE TABLE exchangerate (curr char(3), transdate date, buy float, sell float)")
        c.execute("INSERT INTO exchangerate VALUES ('USD', '2015-01-02', 1.2, 1.3), ('GBP', '2015-01-02', 0, 0.8), "
                  "('CHF', '2015-01-02', NULL, 1.1), ('JPY', '2015-01-02', 0, 0)")
        res = slupgrade.upgrade(c, target="2.9.2")
        assert res.applied == ["Pg-upgrade-2.9.1-2.9.2.sql"] and res.errors == []
        rows = c.execute("SELECT curr, exchangerate FROM exchangerate ORDER BY curr").fetchall()
    assert rows == [("CHF", 1.1), ("GBP", 0.8), ("USD", 1.2)]


def test_rma_backup_recognised_and_only_rma_path_relaxed():
    """RI7: een back-up met een RMA-tabel (ook in SL-formaat) of versie 2.8.11 … 2.8.x is Run my Accounts / ledger123.
    Alleen in dat pad mogen ENUM, fuzzystrmatch (overgeslagen) en ``pg_dump -C``; het originele pad blijft streng."""
    for table in slupgrade.RMA_TABLES:
        dump = (HEAD + f"CREATE TABLE public.{table} (\n    trans_id integer\n);\n").encode()
        assert slbackup.preview(gzip.compress(dump)).family == "rma"
    # een gewone tabel met zo'n naam als deel van een langere naam telt niet
    assert slbackup.scan((HEAD + "CREATE TABLE invoicetax_oud (x int);\n").encode())[0].family == ""
    enum = slbackup.Statement(3, "CREATE TYPE public.s AS ENUM ('A', 'B''s')")
    ext = slbackup.Statement(4, "CREATE EXTENSION IF NOT EXISTS fuzzystrmatch WITH SCHEMA public")
    db = slbackup.Statement(5, "CREATE DATABASE x WITH TEMPLATE = template0 ENCODING = 'UTF8'")
    for st in (enum, ext, db):
        with pytest.raises(BackupError, match="hoort niet"):
            slbackup.check(st)
    assert [slbackup.check(st, rma=True) for st in (enum, ext, db)] == ["run", "skip", "skip"]
    for sql in ("CREATE EXTENSION plpython3u", "CREATE EXTENSION fuzzystrmatch_x",
                "CREATE TYPE public.s AS ENUM ('a'); DROP TABLE ar", "CREATE TYPE public.c AS (a int)"):
        with pytest.raises(BackupError):
            slbackup.check(slbackup.Statement(1, sql), rma=True)
    text = "--\n\\connect proef\nCREATE TABLE a (x int);\n"
    with pytest.raises(BackupError, match="connect"):
        list(slbackup.split(text))
    assert [s.text for s in slbackup.split(text, rma=True)] == ["CREATE TABLE a (x int)"]
    with pytest.raises(BackupError, match="connect"):                   # maar één keer
        list(slbackup.split(text + "\\connect ander\n", rma=True))


@pytest.mark.parametrize("version, table", [("2.8.52", None), ("2.8.11", None), ("2.8.10", "acc_trans_log")])
def test_rma_database_refused(admin, version, table):
    """RM6: RMA nummert na 2.8.10 eigen versies; die en een database met een RMA-tabel worden niet bijgewerkt
    (de originele keten zou stil kolommen weghalen)."""
    conn, made = admin
    name = f"rb_{uuid.uuid4().hex[:8]}"
    made.append(name)
    conn.execute(f'CREATE DATABASE "{name}"')
    with _connect(name) as c:
        c.execute("CREATE TABLE defaults (fldname text, fldvalue text)")
        c.execute("INSERT INTO defaults VALUES ('version', %s)", (version,))
        if table:
            c.execute(f"CREATE TABLE {table} (id int)")
        with pytest.raises(slupgrade.UpgradeError, match="Run my Accounts"):
            slupgrade.upgrade(c)
        assert slupgrade.db_version(c) == version
