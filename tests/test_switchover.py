"""Overstap uit SQL-Ledger (sqlledger.switchover en Dashboard.switch_list, FASE6_ANALYSE.md 6d-2, I4–I6, I8)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger import switchover
from sqlledger.dashboard import SWITCH_ITEMS, Dashboard
from sqlledger.errors import ValidationError
from sqlledger.extensions import ensure_schema

ALL = ["ar", "ap", "cash", "oe", "quotations", "ic", "gl", "reports", "recurring", "projects"]
INFO = {"kind": "sl", "dataset": "proef", "version": "2.8.37", "date": "Thu Apr 16 11:35:05 2026",
        "version_from": "2.8.10", "version_to": "3.2.4", "skipped": 0}


def by_key(findings):
    return {f.key: f for f in findings}


def test_integrity_clean_template(conn):
    f = by_key(switchover.integrity(conn, INFO))
    assert list(f) == [k for k, _ in switchover.CHECKS]
    assert all(x.level == "ok" and x.count == 0 for x in f.values()), {k: x.count for k, x in f.items()}


def test_integrity_finds_each_kind(conn):
    gl = [r[0] for r in conn.execute("SELECT id FROM gl ORDER BY id LIMIT 2")]
    chart = conn.execute("SELECT id FROM chart WHERE charttype = 'A' ORDER BY id LIMIT 1").fetchone()[0]
    ar = conn.execute("SELECT id, amount FROM ar WHERE amount > 0 ORDER BY id LIMIT 1").fetchone()
    day = date(2026, 3, 1)
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate) VALUES (%s, %s, 5, %s)",
                 (gl[0], chart, day))                                                  # niet in evenwicht
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, fx_transaction) "
                 "VALUES (%s, %s, 0.01, %s, true)", (gl[1], chart, day))               # 1 cent koersregel
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate) VALUES (999991, %s, 3, %s), "
                 "(999991, 987654, -3, %s)", (chart, day, day))                        # rekening bestaat niet + wees
    conn.execute("UPDATE ar SET paid = amount + 10 WHERE id = %s", (ar[0],))           # meer betaald, sluit niet aan
    f = by_key(switchover.integrity(conn, dict(INFO, skipped=2)))
    assert (f["unbalanced"].count, f["unbalanced"].level, f["unbalanced"].rows[0]["diff"]) == (1, "warn", D("5.00"))
    assert f["unbalanced"].rows[0]["module"] == "gl"
    assert (f["fx_rounding"].count, f["fx_rounding"].level) == (1, "info")
    assert f["missing_account"].count == 1 and f["missing_account"].rows[0]["chart_id"] == 987654
    assert f["orphan"].count == 1 and f["orphan"].rows[0]["trans_id"] == 999991
    assert f["overpaid"].count == 1 and f["overpaid"].rows[0]["trans_id"] == ar[0]
    mm = f["arap_mismatch"]
    assert mm.count == 1 and mm.rows[0]["trans_id"] == ar[0] and mm.rows[0]["diff"] == D("-10.00")
    assert (f["upgrade_skipped"].count, f["upgrade_skipped"].level) == (2, "warn")


def test_counts_and_balances(conn, config):
    years = switchover.counts_per_year(conn)
    for table, key in (("gl", "gl"), ("ar", "ar"), ("ap", "ap"), ("acc_trans", "lines")):
        assert sum(y[key] for y in years) == conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    asof = date(2026, 6, 30)
    have = {b["accno"]: b["balance"] for b in switchover.payment_balances(conn, asof)}
    want = {b.accno: b.balance for b in Dashboard(conn, config).bank(asof)}        # = balans (DB13)
    assert want and {k: have[k] for k in want} == want


def test_record_and_switch_list(conn, config):
    dash = Dashboard(conn, config)
    assert switchover.restore_info(conn) is None and dash.switch_list(ALL) is None
    conn.execute("UPDATE defaults SET fldvalue = '20251231' WHERE fldname = 'closedto'")
    if not conn.execute("SELECT 1 FROM defaults WHERE fldname = 'closedto'").fetchone():
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', '20251231')")
    info = switchover.record_restore(conn, INFO, "anna")
    assert info["closedto"] == "20251231" and info["last_transdate"] and info["version_to"] == "3.2.4"
    assert switchover.restore_info(conn) == info
    ext = {"users": True, "backup": False, "rgs": None}
    items = {i.key: i for i in dash.switch_list(ALL, ext)}
    assert list(items) == [k for k in SWITCH_ITEMS if k not in ("rgs", "recurring", "rma")]   # geen RGS-lijst, geen schema, geen RMA
    assert items["restored"].done and items["users"].done and not items["backup"].done
    assert not items["check"].done and not items["numbering"].done and not items["closedto"].done
    assert "vat" not in {i.key for i in dash.switch_list(["gl"], ext)}            # zonder verkoop/inkoop
    # zelf afvinken, niet nodig, afsluitdatum, btw "geen aangifte"
    dash.switch_mark("check", done=True, login="anna")
    dash.switch_mark("style", login="anna")
    with pytest.raises(ValidationError):
        dash.switch_mark("company", done=True)                                    # automatisch punt
    with pytest.raises(ValidationError):
        dash.switch_mark("bestaat_niet")
    conn.execute("UPDATE defaults SET fldvalue = '20260331' WHERE fldname = 'closedto'")
    dash.save_vat_period("none", "anna")
    items = {i.key: i for i in dash.switch_list(ALL, ext)}
    assert items["check"].done and items["style"].skipped and items["closedto"].done and items["vat"].done
    dash.switch_mark("hidden")
    assert dash.switch_list(ALL, ext) is None


def test_vat_item_needs_every_tax_account(conn, config):
    from sqlledger.vatreturn import VatReturns
    ensure_schema(conn)
    switchover.record_restore(conn, INFO)
    dash = Dashboard(conn, config)
    dash.save_vat_period("quarter")
    vr = VatReturns(conn, config)
    accounts = vr.tax_accounts()
    settle = conn.execute("SELECT accno FROM chart WHERE charttype = 'A' AND category = 'L' "
                          "ORDER BY accno LIMIT 1").fetchone()[0]
    vr.save_settings({a["id"]: a["suggested"] or "none" for a in accounts[:-1]}, settle, "")
    item = lambda: next(i for i in dash.switch_list(ALL, {}) if i.key == "vat")        # noqa: E731
    assert not item().done                                                            # één rekening zonder rubriek
    vr.save_settings({a["id"]: a["suggested"] or "none" for a in accounts}, settle, "")
    assert item().done


def test_enable_for_linked_database(conn, config):
    info = switchover.enable(conn, "anna")
    assert info["kind"] == "linked" and switchover.restore_info(conn)["kind"] == "linked"
    assert Dashboard(conn, config).switch_list(ALL, {})


def test_bank_item_needs_iban_and_matching_statement(conn, config):
    ensure_schema(conn)
    switchover.record_restore(conn, INFO)
    dash = Dashboard(conn, config)
    item = lambda: next(i for i in dash.switch_list(ALL, {}) if i.key == "bank")        # noqa: E731
    chart = conn.execute("SELECT id FROM chart WHERE link LIKE '%AR_paid%' AND category = 'A' "
                         "ORDER BY accno LIMIT 1").fetchone()[0]
    conn.execute("DELETE FROM bank WHERE id = %s", (chart,))
    conn.execute("INSERT INTO bank (id, iban) VALUES (%s, 'NL91ABNA0417164300')", (chart,))
    assert not item().done                                                            # nog geen afschrift
    conn.execute("INSERT INTO x_bank_statement (chart_id, format, warnings) VALUES (%s, 'camt053', "
                 "'Grootboeksaldo rekening 1000 vóór 01-01-2027: 10,00; beginsaldo afschrift: 12,00')", (chart,))
    assert not item().done                                                            # sluit niet aan
    conn.execute("UPDATE x_bank_statement SET warnings = NULL")
    assert item().done


@pytest.mark.parametrize("arap", ["ar", "ap"])
def test_open_per_relation_equals_dashboard(conn, config, arap):
    asof = date(2026, 6, 30)
    rows = switchover.open_per_relation(conn, arap, asof)
    oi = Dashboard(conn, config).open_items(arap, asof, rows=True)
    assert rows and sum(r["open"] for r in rows) == oi.total and sum(r["count"] for r in rows) == oi.count
    assert sum(r["overdue"] for r in rows) == oi.overdue
    assert all(r["name"] for r in rows)


# ------------------------------------------------------------------ deelstappen (6d-2)

def _consistent(dash):
    """Klaar volgens ``switch_list`` ⇔ alle deelstappen klaar (company, vat, bank, closedto)."""
    items = {i.key: i for i in dash.switch_list(ALL, {"users": False})}
    steps = dash.switch_steps()
    for key in ("company", "vat", "bank", "closedto"):
        assert items[key].done == all(s.done for s in steps[key]), (key, steps[key])
    return {k: {s.key: s for s in v} for k, v in steps.items()}


def test_switch_steps_company_vat_closedto(conn, config):
    from sqlledger.vatreturn import VatReturns
    ensure_schema(conn)
    conn.execute("DELETE FROM defaults WHERE fldname = 'closedto'")
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', '20173112')")
    switchover.record_restore(conn, INFO)
    dash = Dashboard(conn, config)
    conn.execute("UPDATE defaults SET fldvalue = '' WHERE fldname = 'address'")
    s = _consistent(dash)
    assert s["company"]["name"].done and not s["company"]["address"].done
    assert not s["vat"]["period"].done and not s["vat"]["settlement"].done
    vr = VatReturns(conn, config)
    accounts = vr.tax_accounts()
    assert not s["vat"]["boxes"].done and len(s["vat"]["boxes"].rows) == len(accounts)
    c = s["closedto"]["later"]
    assert not c.done and c.detail["then"] == 20173112 and c.detail["then_date"] is None    # geen geldige datum
    assert c.detail["now"] == 20173112
    # adres, tijdvak, rubrieken op één na, afdrachtrekening
    conn.execute("UPDATE defaults SET fldvalue = 'Straat 1' WHERE fldname = 'address'")
    if not conn.execute("SELECT 1 FROM defaults WHERE fldname = 'address'").fetchone():
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('address', 'Straat 1')")
    dash.save_vat_period("quarter")
    settle = conn.execute("SELECT accno FROM chart WHERE charttype = 'A' AND category = 'L' "
                          "ORDER BY accno LIMIT 1").fetchone()[0]
    vr.save_settings({a["id"]: a["suggested"] or "none" for a in accounts[:-1]}, settle, "")
    s = _consistent(dash)
    assert all(x.done for x in s["company"].values())
    assert s["vat"]["period"].done and s["vat"]["settlement"].done and not s["vat"]["boxes"].done
    assert [r["id"] for r in s["vat"]["boxes"].rows] == [accounts[-1]["id"]]
    vr.save_settings({a["id"]: a["suggested"] or "none" for a in accounts}, settle, "")
    conn.execute("UPDATE defaults SET fldvalue = '20180331' WHERE fldname = 'closedto'")
    s = _consistent(dash)
    assert all(x.done for x in s["vat"].values()) and s["closedto"]["later"].done
    assert s["closedto"]["later"].detail["now_date"] == date(2018, 3, 31)
    dash.save_vat_period("none")                                        # geen herinnering: alleen het tijdvak
    assert list(_consistent(dash)["vat"]) == ["period"]


def test_switch_steps_bank(conn, config):
    ensure_schema(conn)
    switchover.record_restore(conn, INFO)
    dash = Dashboard(conn, config)
    conn.execute("UPDATE bank SET iban = ''")
    pay = conn.execute("SELECT id, accno FROM chart WHERE (link LIKE '%AR_paid%' OR link LIKE '%AP_paid%') "
                       "AND NOT coalesce(closed, false) ORDER BY accno").fetchall()
    s = _consistent(dash)
    assert not s["bank"]["iban"].done and [r["id"] for r in s["bank"]["iban"].rows] == [p[0] for p in pay]
    assert not s["bank"]["statement"].done and not s["bank"]["match"].done and not s["bank"]["match"].rows
    chart, accno = pay[0]
    conn.execute("DELETE FROM bank WHERE id = %s", (chart,))
    conn.execute("INSERT INTO bank (id, iban) VALUES (%s, 'NL91ABNA0417164300')", (chart,))
    s = _consistent(dash)
    assert s["bank"]["iban"].done and not s["bank"]["iban"].rows and not s["bank"]["statement"].done
    warning = f"Grootboeksaldo rekening {accno} vóór 01-01-2027: 10,00; beginsaldo afschrift: 12,00 (verschil 2,00)."
    sid = conn.execute("INSERT INTO x_bank_statement (chart_id, format, warnings) VALUES (%s, 'camt053', %s) "
                       "RETURNING id", (chart, "Andere waarschuwing.\n" + warning)).fetchone()[0]
    conn.execute("INSERT INTO x_bank_statement (chart_id, format) VALUES (%s, 'camt053')", (chart,))   # tweede
    s = _consistent(dash)
    assert s["bank"]["statement"].done and not s["bank"]["match"].done
    (row,) = s["bank"]["match"].rows
    assert row["statement_id"] == sid and row["accno"] == accno and row["warning"] == warning
    conn.execute("UPDATE x_bank_statement SET warnings = NULL")
    s = _consistent(dash)
    assert all(x.done for x in s["bank"].values())


def test_rgs_missing(conn):
    missing, total = switchover.rgs_missing(conn)
    assert total and missing == total and not switchover.rgs_complete(conn)
    ids = [r[0] for r in conn.execute("SELECT id FROM chart WHERE charttype = 'A' AND NOT coalesce(closed, false)")]
    for i in ids[:3]:
        conn.execute("INSERT INTO x_account_rgs (chart_id, version, code) VALUES (%s, '3.7', 'BLim')", (i,))
    assert switchover.rgs_missing(conn) == (total - 3, total)


def test_rma_item_vat_unavailable_and_origin(conn, config):
    """§12 VP1/VP3/VP5: herkomst met bedrijfsnaam en familie; bij een RMA-administratie het punt "rma" (zelf
    afvinken); geen btw-formulier voor het land (CH) = punt btw automatisch "niet nodig" (unavailable)."""
    dash = Dashboard(conn, config)
    info = switchover.record_restore(conn, {**INFO, "family": "rma", "dataset": "", "date": ""}, "anna")
    company = conn.execute("SELECT fldvalue FROM defaults WHERE fldname = 'company'").fetchone()
    assert info["family"] == "rma" and info["company"] == ((company[0] or "").strip() if company else "")
    ext = {"users": True, "backup": True, "rgs": None}
    before = {i.key: i for i in dash.switch_list(ALL, ext)}
    assert "rma" not in before and not before["vat"].unavailable                 # EUR → NL-voorstel
    conn.execute("CREATE SCHEMA rma")
    conn.execute("CREATE TABLE rma.import_report (id serial, report jsonb)")
    items = {i.key: i for i in dash.switch_list(ALL, ext)}
    assert list(items)[:2] == ["restored", "rma"] and not items["rma"].done
    dash.switch_mark("rma", done=True, login="anna")
    assert {i.key: i for i in dash.switch_list(ALL, ext)}["rma"].done
    # RMA + EUR: geen Nederlands voorstel meer; land onbekend → btw niet beschikbaar
    vat = {i.key: i for i in dash.switch_list(ALL, ext)}["vat"]
    assert vat.unavailable and vat.skipped and not vat.done
