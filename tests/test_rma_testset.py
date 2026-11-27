"""Synthetische Run my Accounts-testset (RMA_IMPORT_ANALYSE.md RM7(1), RT1–RT8).

De dumps in ``tests/data/rma/`` zijn gemaakt met ``compare/make_rma_testset.sh``: lege RMA-administraties
(RMA's eigen ``User::dbcreate`` + ``dbupdate``), gevuld via RMA's eigen schermcode (``compare/rma_driver.pl``) en
bewaard als ``pg_dump -C | gzip`` zoals RMA's back-up. Synthetisch, geen echte gegevens.

Deze tests lezen alleen de bestanden (geen Perl, geen database): ze leggen vast wat erin staat, zodat de RMA-route
(RM1–RM3, later RM4) erop kan bouwen, en dat ze als Run my Accounts / ledger123 herkend worden (RI7)."""

import gzip
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from sqlledger import slbackup

DATA = Path(__file__).parent / "data" / "rma"
SETS = {"rma_test_ch": "2.8.52", "rma_test_de": "2.8.52", "rma_test_oud": "2.8.26"}


def load(name):
    return gzip.decompress((DATA / f"{name}.sql.gz").read_bytes())


def rows(name, table):
    return list(slbackup.table_rows(load(name), table))


def defaults(name):
    return {r["fldname"]: r["fldvalue"] for r in rows(name, "defaults")}


@pytest.mark.parametrize("name", sorted(SETS))
def test_dump_is_recognised_as_rma_backup(name):
    """Gewone pg_dump -C (geen SQL-Ledger-kop), versie alleen in defaults; herkend als Run my Accounts / ledger123
    (RI7; tot RI1–RI7 weigerde RM6 hem)."""
    data = load(name)
    assert b"PostgreSQL database dump" in data[:4000] and b"-- SQL-Ledger Backup" not in data[:4000]
    assert f"CREATE DATABASE {name} ".encode() in data
    assert defaults(name)["version"] == SETS[name]
    b = slbackup.preview(gzip.compress(data))
    assert (b.kind, b.family, b.version) == ("pg_dump", "rma", SETS[name])
    b, stmts = slbackup.scan(data)
    assert b.family == "rma" and stmts


def test_recent_dump_has_extension_and_enums_old_dump_not():
    """RM5: vanaf 2.8.27/2.8.42 ENUM-typen en fuzzystrmatch; de 2.8.26-set (RMA-code van 2024-02-02) heeft ze niet,
    maar wel de RMA-tabellen uit Pg-custom_tables (dbcreate laadt die altijd)."""
    new, old = load("rma_test_ch"), load("rma_test_oud")
    for marker in (b"CREATE EXTENSION IF NOT EXISTS fuzzystrmatch", b"CREATE TYPE public.financial_year_status_enum",
                   b"CREATE TYPE public.settlement_type_enum"):
        assert marker in new and marker not in old
    for table in (b"trf", b"build", b"fifo", b"invoicetax"):
        assert b"CREATE TABLE public." + table + b" (" in old


def test_ch_relations_carry_rma_columns():
    """RM3: de kolommen die de originele keten 2.8.10→3.2.4 stil weggooit, zijn in de set gevuld."""
    cust = {r["name"]: r for r in rows("rma_test_ch", "customer")}
    assert cust["Muster AG"]["reminderstop"] == "t" and cust["Muster AG"]["income_accno_id"]
    assert cust["Muster AG"]["iban"] == "CH9300762011623852957"
    vend = {r["name"]: r for r in rows("rma_test_ch", "vendor")}
    lev = vend["Lieferant GmbH"]
    assert lev["expense_accno_id"] and lev["payment_clearing_accno_id"]
    assert Decimal(lev["early_payment_discount"]) == Decimal("0.02")    # 2 % ingevoerd, als fractie bewaard
    emp = rows("rma_test_ch", "employee")[0]
    assert (emp["address1"], emp["city"], emp["role"]) == ("Seeweg 2", "Luzern", "manager") and emp["iban"]
    bank = rows("rma_test_ch", "bank")[0]
    assert bank["qriban"] == "CH4431999123000889012"
    fx = rows("rma_test_ch", "exchangerate")
    assert sum(1 for r in fx if r["buy"] != r["sell"]) == 6
    assert any(float(r["buy"]) == 0 and float(r["sell"]) > 0 for r in fx)    # alleen sell (ADM-15)
    years = sorted((r["start_date"], r["status"]) for r in rows("rma_test_ch", "financial_year"))
    assert years == [("2025-01-01", "CLOSED"), ("2026-01-01", "CURRENT_FINANCIAL_YEAR")]


def test_ch_warehouse_documents_and_inventory_meaning():
    """RM11/RM4: RMA's inventory per magazijn telt niet op tot parts.onhand (factuurregels schrijven voorraad, ook
    voor de order waaruit gefactureerd wordt, en voor diensten); trf/build zijn eigen documenten."""
    assert len(rows("rma_test_ch", "trf")) == 1 and len(rows("rma_test_ch", "build")) == 1
    parts = {r["id"]: r for r in rows("rma_test_ch", "parts")}
    per_part = defaultdict(Decimal)
    for r in rows("rma_test_ch", "inventory"):
        per_part[parts[r["parts_id"]]["partnumber"]] += Decimal(r["qty"])
    onhand = {p["partnumber"]: Decimal(p["onhand"]) for p in parts.values()}
    assert onhand["A-100"] == 4 and per_part["A-100"] == 14
    assert per_part["S-1"] == Decimal("-4.5") and onhand["S-1"] == 0    # dienst met voorraadrijen
    linetypes = {r["linetype"] for r in rows("rma_test_ch", "inventory")}
    assert {"1", "2", "4", "5"} <= linetypes    # transfer in/uit, assemblage onderdelen/eindproduct


def test_payments_are_on_the_invoice():
    """Betalingen staan als acc_trans-regels op de factuur zelf (zoals 3.2.4); geen losse betaaldocumenten."""
    ar = rows("rma_test_ch", "ar")
    assert not [r for r in ar if Decimal(r["amount"]) == 0]
    inv = next(r for r in ar if r["invnumber"] == "RE-2025-001")
    assert Decimal(inv["paid"]) == Decimal(inv["amount"])
    lines = [r for r in rows("rma_test_ch", "acc_trans") if r["trans_id"] == inv["id"] and r["transdate"] == "2025-03-10"]
    assert sorted(float(r["amount"]) for r in lines) == [-4421.29, 4421.29]


#: echte test-/demoback-up van een ledger123-installatie (2.8.24, RMA-schema + ledger123-extra's), van de gebruiker
#: gekregen op 2026-10-16; staat bewust NIET in de repo (bevat een echt e-mailadres van een ontwikkelaar)
REAL_L28 = Path.home() / "sqlledger-test" / "rma-data" / "l28" / "ledger28-2026-10-16.sql"


@pytest.mark.skipif(not REAL_L28.exists(), reason="demo-back-up ledger28 niet aanwezig")
def test_real_ledger28_demo_backup_recognised():
    """De pg_dump (13.23, met \\restrict) wordt herkend als RMA-familie (versie 2.8.24)."""
    data = REAL_L28.read_bytes()
    assert b"\\restrict " in data[:2000]
    assert slbackup.preview(data).family == "rma"
    assert {r["fldname"]: r["fldvalue"] for r in slbackup.table_rows(data, "defaults")}["version"] == "2.8.24"
