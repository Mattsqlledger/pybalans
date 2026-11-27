"""Run my Accounts-/ledger123-back-up inlezen (sqlledger.rmaimport, RMA_IMPORT_ANALYSE.md §9–§10, RI1–RI7)."""

import gzip
import os
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from sqlledger import rmaimport

DSN = os.environ.get("SQLLEDGER_TEST_DSN", "host=127.0.0.1 port=55432 user=postgres")
DATA = Path(__file__).parent / "data" / "rma"
REAL_L28 = Path.home() / "sqlledger-test" / "rma-data" / "l28" / "ledger28-2026-10-16.sql"
ENV = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": "55432", "PGUSER": "postgres"}


def dump(name):
    if name == "ledger28":
        if not REAL_L28.exists():
            pytest.skip("demo-back-up ledger28 niet aanwezig")
        return REAL_L28.read_bytes()
    return gzip.decompress((DATA / f"{name}.sql.gz").read_bytes())


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


def psql_load(admin, name):
    """De dump zoals RMA hem zelf terugzet (psql), in een nieuwe database; alleen voor de vergelijking (RI1)."""
    conn, made = admin
    db = f"rx_{uuid.uuid4().hex[:8]}"
    made.append(db)
    conn.execute(f'CREATE DATABASE "{db}"')
    text = dump(name).decode("utf-8")
    lines = [ln for ln in text.splitlines(keepends=True)
             if not ln.startswith(("CREATE DATABASE", "ALTER DATABASE", "\\connect", "\\restrict", "\\unrestrict"))]
    subprocess.run(["psql", "-q", "-d", db], input="".join(lines).encode(), env=ENV, capture_output=True, check=True)
    return db


def test_reference_is_original_2810():
    """BK1: 54 tabellen van het originele 2.8.10 (RMA-commit 6d3c94f), zonder RMA- of ledger123-kolommen."""
    ref = rmaimport.reference()
    assert len(ref.tables) == 54 and "acc_trans" in ref.tables and "invoicetax" not in ref.tables
    assert "entry_id" not in ref.tables["acc_trans"] and "reminderstop" not in ref.tables["customer"]
    assert {"iban", "bic", "address1", "role"} <= ref.tables["employee"]
    assert {"buy", "sell"} <= ref.tables["exchangerate"] and "precision" in ref.tables["curr"]
    assert "id" in ref.sequences and "del_customer" in ref.functions


@pytest.mark.parametrize("name, tables, columns", [
    ("rma_test_ch", 42, 55), ("rma_test_de", 42, 55), ("rma_test_oud", 24, 42), ("ledger28", 31, 51)])
def test_extras_of_rma_dumps(admin, name, tables, columns):
    """RI1: alles buiten 2.8.10 is extra, ook de ledger123-tabellen (Ecuador, Revolut, import) in ledger28; geen
    originele kolom ontbreekt."""
    with _connect(psql_load(admin, name)) as c:
        ex = rmaimport.find_extras(c)
    assert (len(ex.tables), sum(map(len, ex.columns.values()))) == (tables, columns)
    assert ex.missing == {}
    assert {"invoicetax", "trf", "build", "fifo"} <= set(ex.tables)
    assert ("reminderstop" in ex.columns["customer"]) == name.startswith(("rma_test_ch", "rma_test_de"))
    assert "customerloginid" in ex.sequences and "entry_id" in ex.sequences
    if name in ("rma_test_ch", "rma_test_de"):
        assert ex.types == ["financial_year_status_enum", "settlement_type_enum"]
        assert ("customer", "trg_set_last_modified_customer") in ex.triggers
        assert ex.functions == ["to_filtered_tsvector(text,text,regconfig)", "update_last_modified_column()"]
    else:
        assert not (ex.types or ex.triggers or ex.functions)
    if name == "ledger28":
        assert {"revolut_accounts", "generic_import", "retenc", "acc_trans2"} <= set(ex.tables)
        assert ex.columns["gl"] == ["onhold", "ticket_id", "transjson", "ts"]


def test_pure_2810_has_no_extras(admin):
    """Een gewone 2.8.10-database (de referentie zelf) heeft niets extra."""
    conn, made = admin
    db = f"rx_{uuid.uuid4().hex[:8]}"
    made.append(db)
    conn.execute(f'CREATE DATABASE "{db}"')
    with _connect(db) as c:
        for table, cols in rmaimport.reference().tables.items():
            c.execute(f'CREATE TABLE "{table}" ({", ".join(f"{col} text" for col in sorted(cols))})')
        for seq in rmaimport.reference().sequences:
            c.execute(f'CREATE SEQUENCE "{seq}"')
        assert not rmaimport.find_extras(c)
        c.execute("ALTER TABLE customer ADD COLUMN reminderstop bool")
        c.execute("CREATE TABLE eigen (id serial)")
        ex = rmaimport.find_extras(c)
    assert ex.tables == ["eigen"] and ex.columns == {"customer": ["reminderstop"]} and ex.sequences == []


# ------------------------------------------------------------------ inlezen via slbackup.restore (RI2–RI4, RI6)

from sqlledger import slbackup  # noqa: E402

SOURCE = {"rma_test_ch": "2.8.52", "rma_test_de": "2.8.52", "rma_test_oud": "2.8.26", "ledger28": "2.8.24"}


def restore(admin, name):
    conn, made = admin
    db = f"ri_{uuid.uuid4().hex[:8]}"
    made.append(db)
    res = slbackup.restore(conn, _connect, db, dump(name))
    return db, res


@pytest.mark.parametrize("name", sorted(SOURCE))
def test_restore_rma_backup(admin, name):
    """Acceptatie (§9): inlezen lukt, keten 2.8.10 → 3.2.4 foutloos, proefbalans en openstaand gelijk, elke bronrij
    terug te vinden (public of rma), RMA-extra's in schema rma, gewone inventory leeg (RM11)."""
    db, res = restore(admin, name)
    rep = res.rma
    assert res.info.family == "rma" and rep["source_version"] == SOURCE[name]
    assert res.upgrade.to_version == "3.2.4" and res.upgrade.errors == [] and rep["chain_errors"] == []
    assert rep["balance"]["differences"] == [] and rep["open_items"]["differences"] == []
    assert rep["balance"]["accounts"] > 0 and rep["open_items"]["documents"] > 0
    assert [c["table"] for c in rep["counts"] if not c["ok"]] == []
    assert rep["warehouse_converted"] is False
    with _connect(db) as c:
        assert rmaimport.load_report(c)["source_version"] == SOURCE[name]
        public = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables "
                                          "WHERE table_schema = 'public'")}
        assert not public & {"invoicetax", "trf", "build", "fifo", "acc_trans_log"}
        assert c.execute("SELECT count(*) FROM public.inventory").fetchone()[0] == 0
        src_inv = next(r["source"] for r in rep["counts"] if r["table"] == "inventory")
        assert c.execute("SELECT count(*) FROM rma.inventory").fetchone()[0] == src_inv > 0
        for t in ("invoicetax", "trf", "build", "fifo"):
            assert c.execute(f"SELECT count(*) FROM rma.{t}").fetchone()[0] == \
                next(r["source"] for r in rep["counts"] if r["table"] == t)
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                        "WHERE table_schema = 'public' AND table_name = 'acc_trans'")}
        assert "entry_id" not in cols and "tax_chart_id" not in cols
        assert c.execute("SELECT count(*) FROM rma.acc_trans").fetchone()[0] == \
            c.execute("SELECT count(*) FROM public.acc_trans").fetchone()[0]
        assert c.execute("SELECT fldvalue FROM rma.defaults WHERE fldname = 'version'").fetchone()[0] == SOURCE[name]
        if name in ("rma_test_ch", "rma_test_de"):
            kinds = {(r[0], r[1]) for r in c.execute("SELECT kind, name FROM rma.object")}
            assert {("trigger", "trg_set_last_modified_customer"), ("function", "update_last_modified_column()"),
                    ("constraint", "fk_yearend")} <= kinds
            assert c.execute("SELECT count(*) FROM pg_type WHERE typnamespace = 'rma'::regnamespace "
                             "AND typtype = 'e'").fetchone()[0] == 2
            years = c.execute("SELECT status::text FROM rma.financial_year ORDER BY start_date").fetchall()
            assert years == ([("CLOSED",), ("CURRENT_FINANCIAL_YEAR",)] if name == "rma_test_ch"
                             else [("CURRENT_FINANCIAL_YEAR",)])
        if name == "ledger28":
            assert c.execute("SELECT count(*) FROM rma.revolut_accounts").fetchone() is not None
            assert c.execute("SELECT count(*) FROM rma.generic_import").fetchone() is not None
        # geen RMA-trigger meer actief in public
        assert c.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class k ON k.oid = t.tgrelid "
                         "WHERE NOT t.tgisinternal AND t.tgname LIKE 'trg_set_last_modified%'").fetchone()[0] == 0


def test_check_difference_aborts_and_removes_database(admin, monkeypatch):
    """RI6: verschil in de proefbalans na de keten → afbreken met rapport, database weg."""
    real = rmaimport.snapshot
    calls = []

    def fake(conn, schema="public"):
        snap = real(conn, schema)
        calls.append(1)
        if len(calls) == 2:                                         # na de keten: één rekening verschuift 0,01
            accno = sorted(snap.balance)[0]
            snap.balance[accno] += Decimal("0.01")
        return snap

    monkeypatch.setattr(rmaimport, "snapshot", fake)
    conn, made = admin
    db = f"ri_{uuid.uuid4().hex[:8]}"
    made.append(db)
    with pytest.raises(rmaimport.RmaCheckError) as exc:
        slbackup.restore(conn, _connect, db, dump("rma_test_oud"))
    assert len(exc.value.report["balance"]["differences"]) == 1
    assert not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone()


def test_restore_values_ch(admin):
    """RI5 (RM3): kostenrekening leverancier → x_vendor_default, eindmaand boekjaar, IBAN/BIC en medewerkeradres via
    de keten; zonder PyBalans-plek geteld en in rma; verliesrekening koersverschillen samengevoegd = waarschuwing."""
    db, res = restore(admin, "rma_test_ch")
    rest = res.rma["restored"]
    done = {i["key"]: i["count"] for i in rest["done"]}
    kept = {i["key"]: i["count"] for i in rest["kept"]}
    assert done == {"vendor_expense_account": 1, "fiscal_year_end": 1, "customer_iban": 2, "vendor_iban": 2,
                    "employee_iban": 1, "employee_address": 1, "currency_precision": 3}
    assert kept == {"financial_years": 2, "employee_role": 1, "exchangerate_sell_differs": 5,
                    "customer_income_account": 1, "vendor_clearing_account": 1, "customer_reminderstop": 1,
                    "vendor_reminderstop": 1, "vendor_early_payment_discount": 1, "bank_qriban": 1,
                    "inventory_rows": 22}
    assert rest["warnings"] == [{"key": "fx_loss_account_merged", "count": 1, "gain": "36005 Kursgewinn",
                                 "loss": "39006 Kursverlust"}]
    with _connect(db) as c:
        assert c.execute("SELECT v.name, c.accno FROM x_vendor_default d JOIN vendor v ON v.id = d.vendor_id "
                         "JOIN chart c ON c.id = d.expense_chart_id").fetchall() == [("Lieferant GmbH", "41001")]
        assert c.execute("SELECT fldvalue FROM defaults WHERE fldname = 'yearend'").fetchone() == ("12",)
        assert c.execute("SELECT qriban FROM rma.bank").fetchone() == ("CH4431999123000889012",)


def test_app_backup_of_rma_company_roundtrip(admin, tmp_path):
    """BK7: een back-up van deze app neemt schema rma mee (ENUM-typen, rapport) en is weer in te lezen."""
    from sqlledger import dbbackup
    db, _res = restore(admin, "rma_test_ch")
    path = tmp_path / "app.sql.gz"
    dbbackup.dump({"host": "127.0.0.1", "port": 55432, "user": "postgres", "dbname": db}, path, version="test",
                  company="RMA AG")
    conn, made = admin
    db2 = f"ri_{uuid.uuid4().hex[:8]}"
    made.append(db2)
    res = slbackup.restore(conn, _connect, db2, path.read_bytes())
    assert res.info.kind == "app" and res.info.family == "" and res.rma is None
    with _connect(db) as a, _connect(db2) as b:
        assert rmaimport.load_report(b) == rmaimport.load_report(a)
        for q in ("SELECT count(*) FROM rma.invoicetax", "SELECT count(*) FROM rma.inventory",
                  "SELECT status::text FROM rma.financial_year ORDER BY 1",
                  "SELECT count(*) FROM pg_type WHERE typnamespace = 'rma'::regnamespace AND typtype = 'e'"):
            assert a.execute(q).fetchall() == b.execute(q).fetchall(), q
    # buiten het RMA-pad en buiten een back-up van deze app blijft CREATE SCHEMA overgeslagen
    assert slbackup.check(slbackup.Statement(1, "CREATE SCHEMA rma")) == "skip"
    with pytest.raises(slbackup.BackupError):
        slbackup.check(slbackup.Statement(1, "CREATE TYPE public.x AS ENUM ('a')"), app=True)


def test_bz2_backup(admin):
    """RI8 S4: RMA's mojo-admin maakt bzip2; herkend, voorbeeld en inlezen; beschadigd = duidelijke melding."""
    import bz2
    data = bz2.compress(dump("rma_test_oud"))
    b = slbackup.preview(data)
    assert (b.family, b.version) == ("rma", "2.8.26")
    conn, made = admin
    db = f"ri_{uuid.uuid4().hex[:8]}"
    made.append(db)
    assert slbackup.restore(conn, _connect, db, data).rma["balance"]["differences"] == []
    with pytest.raises(slbackup.BackupError, match="bz2-bestand is beschadigd"):
        slbackup.preview(data[:200])
