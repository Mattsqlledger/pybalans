"""Aanmaningen en rekeningoverzichten (fase 4 stap 4a, SB1–SB6; testdata uit compare/arap_seed.sql).

Op 30-06-2026 zijn vervallen: klant 900 F-3006 (363 − 100 betaald, niveau 1 vastgelegd) en F-3009 (10, verval
29-06); klant 902 F-3002 (272,50 − 50), F-3004 (96,80) en F-3007 (65,40, niveau 2); klant 903 F-3003 (USD 500
tegen 1,10). F-3001 vervalt pas 09-07.
"""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger.dunning import Dunning, bucket_ranges, letter_vars, statement_vars
from sqlledger.printdata import PrintData
from sqlledger.reports import Reports

ASOF = date(2026, 6, 30)


@pytest.fixture
def dn(conn, config):
    return Dunning(conn, config)


def _by_name(letters):
    return {(l.vc_id, l.curr): l for l in letters}


def _credit(conn, tid, vc, amount, d, curr="EUR", rate=None):
    """Creditnota (negatief openstaand) met de regel op de debiteurenrekening."""
    conn.execute("""INSERT INTO ar (id, invnumber, transdate, duedate, customer_id, netamount, amount, paid,
                                    invoice, curr, approved)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 0, false, %s, true)""",
                 (tid, f"C-{tid}", d, d, vc, amount, amount, curr))
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                    VALUES (%s, (SELECT id FROM chart WHERE accno = '1200'), %s, %s, true)""", (tid, -amount, d))
    if rate:
        conn.execute("INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES (%s, %s, %s) "
                     "ON CONFLICT DO NOTHING", (curr, d, rate))


def test_letters_per_customer_and_currency(dn):
    letters = _by_name(dn.letters(ASOF))
    assert set(letters) == {(900, "EUR"), (902, "EUR"), (903, "USD")}
    a = letters[(900, "EUR")]
    assert [i.invnumber for i in a.items] == ["F-3006", "F-3009"]
    assert a.total == D("273.00") and a.level == 2 and a.formname == "reminder2"
    assert a.levels == {3006: 2, 3009: 1}
    b = letters[(902, "EUR")]
    assert [i.invnumber for i in b.items] == ["F-3007", "F-3004", "F-3002"]
    assert b.total == D("384.70") and b.level == 3
    usd = letters[(903, "USD")]
    assert usd.items[0].due == D("454.55")                 # in de valuta van de factuur, zoals rp.pl
    assert usd.items[0].days == 27


def test_min_days_and_asof(dn):
    a = _by_name(dn.letters(ASOF, min_days=10))[(900, "EUR")]
    assert [i.invnumber for i in a.items] == ["F-3006"]
    # vóór de deelbetaling van 20-04: F-3006 staat nog helemaal open (open bedrag per peildatum, SB5)
    early = _by_name(dn.letters(date(2026, 4, 19)))
    assert [(i.invnumber, i.due) for i in early[(900, "EUR")].items] == [("F-3006", D("363.00"))]


def test_credits_are_offset_and_no_letter_when_credit_covers(dn, conn):
    _credit(conn, 3100, 900, D("-23.00"), date(2026, 6, 28))
    a = _by_name(dn.letters(ASOF))[(900, "EUR")]
    assert [c.invnumber for c in a.credits] == ["C-3100"] and a.credits[0].due == D("-23.00")
    assert a.total_due == D("273.00") and a.total == D("250.00")
    # tegoed groter dan het vervallen bedrag: geen aanmaning (SB2)
    _credit(conn, 3101, 900, D("-300.00"), date(2026, 6, 28))
    assert (900, "EUR") not in _by_name(dn.letters(ASOF))
    # alleen een deel aangevinkt: het tegoed blijft op de brief
    _credit(conn, 3102, 902, D("-10.00"), date(2026, 7, 5))          # na de peildatum: telt niet
    b = _by_name(dn.letters(ASOF))[(902, "EUR")]
    assert b.credits == []
    sub = b.only([3007])
    assert [i.invnumber for i in sub.items] == ["F-3007"] and sub.level == 3


def test_record_sets_level_once_and_caps_at_three(dn, conn):
    letters = _by_name(dn.letters(ASOF))
    a, b = letters[(900, "EUR")], letters[(902, "EUR")]
    dn.record(a, printed=True)
    dn.record(a, emailed=True)                    # zelfde brief gemaild: niveau niet nog een keer omhoog
    rows = conn.execute("SELECT trans_id, formname, printed, emailed FROM status WHERE formname LIKE 'reminder_' "
                        "AND trans_id IN (3006, 3009) ORDER BY trans_id").fetchall()
    assert rows == [(3006, "reminder2", True, True), (3009, "reminder1", True, True)]
    dn.record(b, printed=True)
    again = _by_name(dn.letters(ASOF))
    assert {i.invnumber: i.level for i in again[(902, "EUR")].items} == {"F-3007": 3, "F-3004": 1, "F-3002": 1}
    dn.record(again[(902, "EUR")], printed=True)
    final = _by_name(dn.letters(ASOF))[(902, "EUR")]
    assert {i.invnumber: (i.level, i.next_level) for i in final.items}["F-3007"] == (3, 3)
    # de oude lijst (Perl-indeling) leest hetzelfde niveau
    rep = {r["invnumber"]: r["level"] for r in Reports(conn, dn.config).reminders(asof=ASOF).rows}
    assert rep["F-3006"] == 3 and rep["F-3009"] == 2


def test_chosen_levels_from_screen(dn):
    a = _by_name(dn.letters(ASOF))[(900, "EUR")]
    sub = a.only([3006, 3009], {3006: 3, 3009: 9})
    assert sub.levels == {3006: 3, 3009: 1} and sub.level == 3


def test_set_levels_and_audittrail(dn, conn):
    conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'")
    if not conn.execute("SELECT 1 FROM defaults WHERE fldname = 'audittrail'").fetchone():
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('audittrail', '1')")
    assert dn.set_levels({3006: 0, 3002: 2, 3007: 2}) == [3006, 3002]
    levels = dict(conn.execute("SELECT trans_id, formname FROM status WHERE formname LIKE 'reminder_'").fetchall())
    assert levels == {3002: "reminder2", 3007: "reminder2"}
    trail = conn.execute("SELECT trans_id, formname, action FROM audittrail WHERE tablename = 'ar' "
                         "ORDER BY trans_id").fetchall()
    assert trail == [(3002, "reminder2", "saved"), (3006, "reminder1", "deleted")]
    with pytest.raises(ValueError):
        dn.set_levels({3002: 4})


def test_statements_per_currency_and_asof(dn, conn):
    sts = {(s.vc_id, s.curr): s for s in dn.statements(ASOF)}
    a = Reports(conn, dn.config).aging("ar", todate=ASOF, buckets=["c0", "c30", "c60", "c90"])
    assert sum(s.total for s in sts.values()) == sum(t["total"] for t in a.totals.values())
    assert sts[(902, "EUR")].total == D("384.70")
    # klant met posten in twee valuta krijgt twee overzichten (Perl-fout 19)
    conn.execute("INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2026-06-01', 1.25)")
    conn.execute("""INSERT INTO ar (id, invnumber, transdate, duedate, customer_id, netamount, amount, paid,
                                    invoice, curr, approved)
                    VALUES (3200, 'F-3200', '2026-06-01', '2026-06-15', 902, 125, 125, 0, false, 'USD', true)""")
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                    VALUES (3200, (SELECT id FROM chart WHERE accno = '1200'), -125, '2026-06-01', true)""")
    mine = dn.statements(ASOF, customer_id=902)
    assert [(s.curr, s.total) for s in mine] == [("EUR", D("384.70")), ("USD", D("100.00"))]
    # vóór de deelbetaling van 20-06 staat F-3002 nog voor 272,50 open
    early = dn.find_statement(902, "EUR", date(2026, 6, 15))
    assert {r["invnumber"]: r["c0"] + r["c30"] + r["c60"] + r["c90"] for r in early.rows}["F-3002"] == D("272.50")


def test_aging_single_bucket_includes_asof_day(conn, config):
    # Perl-fout 20: met alleen "lopend" viel een factuur van de peildatum zelf weg
    one = Reports(conn, config).aging("ar", todate=date(2026, 6, 25), buckets=["c0"])
    assert "F-3001" in [r["invnumber"] for r in one.rows]


def test_bucket_ranges():
    assert bucket_ranges(["c0", "c30", "c60", "c90"]) == ["0–30", "31–60", "61–90", "> 90"]
    assert bucket_ranges(["c0"]) == ["≥ 0"]
    assert bucket_ranges(["c15", "c45"]) == ["15–45", "> 45"]


def test_vars_and_render(dn, conn, config, tmp_path):
    from sqlledger_web.documents import DocumentRenderer
    pd = PrintData(conn, config, doc_language="nl")
    a = _by_name(dn.letters(ASOF))[(900, "EUR")]
    v = letter_vars(pd, a, ASOF)
    assert [i["invnumber"] for i in v["items"]] == ["F-3006", "F-3009"]
    assert v["total"] == "273.00" and v["level"] == 2 and v["invnumber"] == "F-3006, F-3009"
    r = DocumentRenderer(tmp_path, 1, "nl")
    html = r.render_html("reminder2", v)
    assert "Tweede herinnering" in html and "F-3009" in html and "273.00" in html
    r.doc_language = "de"
    assert "Zweite Zahlungserinnerung" in r.render_html("reminder2", v)
    st = dn.find_statement(902, "EUR", ASOF)
    sv = statement_vars(pd, st, ASOF)
    assert [c["days"] for c in sv["columns"]] == ["0–30", "31–60", "61–90", "> 90"]
    html = DocumentRenderer(tmp_path, 1, "en").render_html("statement", sv)
    assert "Statement of account" in html and "384.70" in html
    pdf = r.render_many([("reminder2", v), ("statement", sv)])
    one = r.render_pdf("reminder2", v)
    assert pdf.startswith(b"%PDF") and len(pdf) > len(one)
