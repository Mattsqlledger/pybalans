"""Bankafschrift inlezen, koppelen en boeken (sqlledger.bankimport; testdata sl_template)."""

import threading
import time
from datetime import date
from decimal import Decimal as D

import psycopg
import pytest

from bankfiles import Entry, camt, make_iban, mt940, nl_reference, rf_reference
from conftest import DSN
from sqlledger import GeneralLedger, GLTransaction, JournalLine, UserConfig
from sqlledger.bankimport import (BankImport, BankImportError, GLAction, IgnoreAction, MatchAction, PayAction,
                                  Rule, UnknownAccountError, find_references, is_valid_nl_reference,
                                  is_valid_rf_reference)
from sqlledger.payments import Payment, PaymentLine, Payments

OWN = make_iban("NL", "INGB0001234567")          # eigen rekening 1100
OTHER = make_iban("NL", "RABO0300000001")        # nog onbekende eigen rekening
KLANT_A = make_iban("NL", "ABNA0417164300")      # klant 900
CEES = make_iban("NL", "RABO0123456789")         # klant 902
LEV_B = make_iban("NL", "TRIO0198765432")        # leverancier 901
MOLEN = make_iban("NL", "INGB0007777777")        # leverancier 905
KPN = make_iban("NL", "ABNA0600000001")
REF_3002 = nl_reference("000000000003002")
RF_3004 = rf_reference("F3004X")
D1 = date(2026, 7, 1)


@pytest.fixture
def bi(conn, config):
    conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
    conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
    # oude btw-tarieven een einddatum (anders twee geldige tarieven per rekening)
    conn.execute("UPDATE tax SET validto = '2012-09-30' WHERE rate IN (0.19, 0.06)")
    conn.execute("UPDATE chart SET link = 'AR_paid:AP_paid' WHERE accno = '1140'")
    conn.execute("INSERT INTO bank (id, iban) VALUES (10044, %s)", (OWN,))
    for vid, iban in ((900, KLANT_A), (902, CEES), (901, LEV_B), (905, MOLEN)):
        conn.execute("INSERT INTO bank (id, iban) VALUES (%s, %s)", (vid, iban))
    conn.execute("UPDATE ar SET dcn = %s WHERE id = 3002", (REF_3002,))
    conn.execute("UPDATE ar SET dcn = %s WHERE id = 3004", (RF_3004,))
    return BankImport(conn, config)


def q1(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def paid(conn, table, tid):
    return D(str(q1(conn, f"SELECT paid FROM {table} WHERE id = %s", tid)[0]))


def by_amount(props, amount):
    [p] = [p for p in props if p.line["amount"] == D(amount)]
    return p


def statement(*entries, opening="1000.00", **kw):
    return camt(OWN, list(entries), D(opening), **kw)


# ------------------------------------------------------------------ kenmerken

def test_references():
    assert is_valid_nl_reference(REF_3002) and len(REF_3002) == 16
    assert not is_valid_nl_reference(str((int(REF_3002[0]) + 1) % 10) + REF_3002[1:])
    assert is_valid_rf_reference(RF_3004) and not is_valid_rf_reference("RF00F3004X")
    assert is_valid_rf_reference("RF18 5390 0754 7034")          # voorbeeld uit ISO 11649
    text = f"betaling {REF_3002[:4]} {REF_3002[4:8]} {REF_3002[8:12]} {REF_3002[12:]} en {RF_3004} dank"
    assert find_references(text) == [REF_3002, RF_3004]
    assert find_references("1234567890123456 ongeldig") == []


# ------------------------------------------------------------------ inlezen

def test_import_and_duplicates(bi, conn):
    e1 = Entry(D("263.00"), D1, "Klant A", KLANT_A, "Betaling F-3006", bank_ref="R1")
    e2 = Entry(D("-15.00"), D1, "Bank", None, "Kosten", bank_ref="R2")
    res = bi.import_file(statement(e1, e2), "a.xml")
    [st] = res.statements
    assert st.chart_accno == "1100" and st.lines_new == 2 and not st.skipped and st.id
    assert any("Grootboeksaldo" in w for w in st.warnings)    # testdata heeft geen beginsaldo 1000
    # zelfde bestand nog eens: niets nieuws, afschrift niet opnieuw opgeslagen
    res = bi.import_file(statement(e1, e2), "a.xml")
    assert res.lines_new == 0 and res.lines_skipped == 2 and res.statements[0].id is None
    # overlappende export: één bekende, één nieuwe regel
    e3 = Entry(D("10.00"), date(2026, 7, 2), "Klant A", KLANT_A, "F-3009", bank_ref="R3")
    res = bi.import_file(statement(e2, e3, opening="1263.00", stmt_id="S2"), "b.xml")
    assert res.lines_new == 1 and res.lines_skipped == 1
    assert res.statements[0].skipped[0]["amount"] == D("-15.00")
    assert not any("vorige afschrift" in w for w in res.statements[0].warnings)
    assert q1(conn, "SELECT count(*) FROM x_bank_line")[0] == 3
    assert len(bi.statements()) == 2


def test_previous_statement_warning(bi):
    bi.import_file(statement(Entry(D("5"), D1, description="x")))
    res = bi.import_file(statement(Entry(D("6"), date(2026, 7, 3), description="y"), opening="999", stmt_id="S2"))
    assert any("vorige afschrift" in w for w in res.statements[0].warnings)


def test_balance_error(bi, conn):
    data = statement(Entry(D("263.00"), D1, description="x"), closing=D("1300.00"))
    with pytest.raises(BankImportError, match="Saldocontrole"):
        bi.import_file(data)
    assert q1(conn, "SELECT count(*) FROM x_bank_statement")[0] == 0


def test_unknown_iban_then_choose(bi, conn):
    data = camt(OTHER, [Entry(D("1.00"), D1, description="rente")], D("0"))
    with pytest.raises(UnknownAccountError) as exc:
        bi.import_file(data)
    assert exc.value.iban == OTHER
    assert {a["accno"] for a in exc.value.accounts} == {"1100", "1140"}
    with pytest.raises(BankImportError, match="geen betaalrekening"):
        bi.import_file(data, chart_accno="4900")
    with pytest.raises(BankImportError, match="heeft al rekeningnummer"):
        bi.import_file(data, chart_accno="1100")
    res = bi.import_file(data, chart_accno="1140")
    assert res.statements[0].iban_saved and res.statements[0].chart_accno == "1140"
    assert q1(conn, "SELECT iban FROM bank WHERE id = 10051")[0] == OTHER
    # volgende keer vanzelf
    res = bi.import_file(camt(OTHER, [Entry(D("2.00"), date(2026, 7, 2), description="rente")], D("1")))
    assert res.statements[0].chart_accno == "1140" and not res.statements[0].iban_saved
    # IBAN die al bij een andere rekening hoort
    with pytest.raises(BankImportError, match="hoort bij rekening 1100"):
        bi.import_file(statement(Entry(D("3"), D1, description="z")), chart_accno="1140")


def test_currency_mismatch(bi):
    with pytest.raises(BankImportError, match="in USD, maar rekening 1100 is in EUR"):
        bi.import_file(statement(Entry(D("5"), D1, description="x"), ccy="USD"))
    with pytest.raises(BankImportError, match="niet bekend"):
        bi.import_file(statement(Entry(D("5"), D1, description="x"), ccy="GBP"))


def test_mt940(bi):
    data = mt940(OWN, [Entry(D("263.00"), D1, "Klant A", KLANT_A, "F-3006"),
                       Entry(D("222.50"), D1, "Bakkerij Cees", CEES, reference=REF_3002)], D("1000"))
    res = bi.import_file(data, "x.940")
    assert res.statements[0].format == "mt940" and res.lines_new == 2
    props = bi.proposals("1100")
    assert [p.category for p in props] == ["b", "a"] and all(p.sure for p in props)
    assert bi.import_file(data).lines_skipped == 2


# ------------------------------------------------------------------ koppelregels

def test_proposals_each_rule(bi, conn, config):
    # (d) bestaande ongeafletterde boeking: memoriaal -50 op de bank, 3 dagen eerder
    gl_id = GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=date(2026, 6, 28), description="Contante opname",
        lines=[JournalLine("1001", debit=D("50")), JournalLine("1100", credit=D("50"), source="OPN-1")]))
    bi.save_rule(Rule(accno="4900", keyword="kpn", direction="-", tax_accno="1800"))
    bi.import_file(statement(
        Entry(D("222.50"), D1, "Bakkerij Cees", CEES, reference=REF_3002),                     # a NL-kenmerk
        Entry(D("96.80"), D1, "Bakkerij Cees", None, f"Betaling {RF_3004}"),                    # a RF in tekst
        Entry(D("131.00"), D1, "Klant A", KLANT_A, "Facturen F-3001 en F-3009"),                # b twee
        Entry(D("30.00"), D1, "Cees", None, "Deel van F-3007"),                                 # b deel
        Entry(D("-121.00"), D1, "Leverancier B", LEV_B, "Factuur"),                             # c één post
        Entry(D("-1515.00"), D1, "Graanhandel", MOLEN, "Maart en april"),                       # c alle posten
        Entry(D("-50.00"), D1, "Geldautomaat", None, "Opname"),                                 # d
        Entry(D("-60.50"), D1, "KPN BV", KPN, "KPN abonnement juli"),                           # e
        Entry(D("-7.00"), D1, "Onbekend", None, "iets"),                                        # niets
        Entry(D("263.00"), D1, "Klant A", KLANT_A, "Betaling F-3006", reversal=True),           # storno
    ))
    props = bi.proposals("1100")
    p = by_amount(props, "222.50")
    assert (p.category, p.vc, p.vc_id, p.exact, p.sure) == ("a", "customer", 902, True, True)
    assert [(a.trans_id, a.paid) for a in p.allocations] == [(3002, D("222.50"))]
    p = by_amount(props, "96.80")
    assert p.category == "a" and [a.trans_id for a in p.allocations] == [3004] and p.sure
    p = by_amount(props, "131.00")
    assert p.category == "b" and sorted(a.trans_id for a in p.allocations) == [3001, 3009] and p.sure
    p = by_amount(props, "30.00")
    assert p.category == "b" and not p.exact and not p.sure
    assert [(a.trans_id, a.paid) for a in p.allocations] == [(3007, D("30.00"))]
    p = by_amount(props, "-121.00")
    assert (p.category, p.vc, p.vc_id, p.exact) == ("c", "vendor", 901, True) and not p.sure
    assert [(a.trans_id, a.paid) for a in p.allocations] == [(4003, D("121.00"))]
    p = by_amount(props, "-1515.00")
    assert p.category == "c" and sorted(a.trans_id for a in p.allocations) == [4001, 4002] and p.exact
    p = by_amount(props, "-50.00")
    assert p.category == "d" and p.kind == "match" and p.match["trans_id"] == gl_id
    p = by_amount(props, "-60.50")
    assert p.category == "e" and p.kind == "gl" and p.rule["accno"] == "4900"
    p = by_amount(props, "-7.00")
    assert p.category is None and p.kind is None
    p = by_amount(props, "263.00")
    assert p.category is None and p.warnings == ["Storno"]


def test_invoice_number_boundaries(bi):
    bi.import_file(statement(Entry(D("263.00"), D1, None, None, "XF-3006 en F-30061 en F-3006,50"),
                             Entry(D("10.00"), D1, None, None, "nr F-3009.")))
    props = bi.proposals("1100")
    assert by_amount(props, "263.00").category is None
    assert by_amount(props, "10.00").category == "b"


def test_invoice_with_credit_note(bi, conn):
    """Factuur en creditnota van dezelfde leverancier in één overboeking (netto)."""
    conn.execute("""INSERT INTO ap (id, invnumber, vendor_id, transdate, duedate, amount, netamount, paid, curr,
                                    approved, onhold, exchangerate)
                    VALUES (4005, 'C-4005', 905, '2026-06-25', '2026-06-25', -105, -105, 0, 'EUR', true, false, 1)""")
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                    VALUES (4005, 10065, -105, '2026-06-25', true), (4005, 10166, 105, '2026-06-25', true)""")
    bi.import_file(statement(Entry(D("-200.00"), D1, "Graanhandel", MOLEN, "I-4002 / C-4005")))
    [p] = bi.proposals("1100")
    assert p.category == "b" and p.exact and p.sure
    assert [(a.trans_id, a.paid) for a in p.allocations] == [(4005, D("-105")), (4002, D("305"))]
    bi.process(p.line["id"], p.action())
    assert paid(conn, "ap", 4002) == D("605") and paid(conn, "ap", 4005) == D("-105")


def test_doubt_when_bank_booking_exists(bi, conn, config):
    """Factuur staat open, maar het geld is al (als memoriaal) op de bank geboekt: niet 'zeker'."""
    gl_id = GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=D1, description="Ontvangst F-3009",
        lines=[JournalLine("1100", debit=D("10")), JournalLine("8520", credit=D("10"))]))
    bi.import_file(statement(Entry(D("10.00"), D1, None, None, "F-3009")))
    [p] = bi.proposals("1100")
    assert p.category == "b" and p.exact and p.doubt and not p.sure
    assert p.alternative["trans_id"] == gl_id and "mogelijk is dit al geboekt" in p.warnings[-1]


def test_iban_relation_partial(bi):
    bi.import_file(statement(Entry(D("50.00"), D1, "Cees", CEES, "aanbetaling")))
    [p] = bi.proposals("1100")
    assert p.category == "c" and not p.exact and p.score == 40
    assert sum(a.paid for a in p.allocations) == D("50.00")


# ------------------------------------------------------------------ verwerken

def _bank_rows(conn, tid, accno="1100"):
    return conn.execute("""SELECT ac.amount, ac.source, ac.cleared, ac.transdate FROM acc_trans ac
                           JOIN chart c ON c.id = ac.chart_id WHERE ac.trans_id = %s AND c.accno = %s""",
                        (tid, accno)).fetchall()


def test_process_payment_and_cleared(bi, conn):
    bi.import_file(statement(Entry(D("222.50"), D1, "Bakkerij Cees", CEES, reference=REF_3002,
                                   bank_ref="BR-1")))
    [p] = bi.proposals("1100")
    before = q1(conn, "SELECT fldvalue FROM defaults WHERE fldname = 'receipt_1100'")
    res = bi.process(p.line["id"], p.action())
    assert res.status == "booked" and res.trans_ids == [3002] and res.source == "BR-1"
    assert paid(conn, "ar", 3002) == D("272.50")
    assert _bank_rows(conn, 3002)[-1] == (-222.5, "BR-1", D1, D1)       # afgeletterd op boekdatum
    assert q1(conn, "SELECT fldvalue FROM defaults WHERE fldname = 'receipt_1100'") == before
    line = bi.lines(status=None)[0]
    assert (line["status"], line["kind"], line["vc_id"], line["trans_ids"], line["category"]) == \
        ("booked", "payment", 902, [3002], "a")
    with pytest.raises(BankImportError, match="al verwerkt"):
        bi.process(p.line["id"], p.action())
    assert bi.proposals("1100") == []


def test_payment_identical_to_payment_screen(bi, conn, config):
    """De betaling via het afschrift is precies wat Payments.post_payment boekt (op afletterdatum na)."""
    bi.import_file(statement(Entry(D("131.00"), D1, "Klant A", KLANT_A, "F-3001 F-3009", bank_ref="BR-9")))
    [p] = bi.proposals("1100")

    def snapshot():
        rows = conn.execute("""SELECT trans_id, chart_id, transdate, amount, source, memo, fx_transaction, approved,
                                      id FROM acc_trans WHERE trans_id IN (3001, 3009) ORDER BY 1, 2, 4, 5""")
        ar = conn.execute("SELECT id, amount, paid, datepaid, bank_id FROM ar WHERE id IN (3001, 3009) ORDER BY id")
        pay = conn.execute("SELECT * FROM payment WHERE trans_id IN (3001, 3009) ORDER BY trans_id, id")
        return rows.fetchall(), ar.fetchall(), pay.fetchall()

    class Rollback(Exception):
        pass
    try:
        with conn.transaction():
            Payments(conn, config).post_payment(Payment(
                vc="customer", vc_id=900, datepaid=D1, payment_accno="1100", source="BR-9", memo="Klant A",
                amount=D("131"), lines=[PaymentLine(3001, D("121")), PaymentLine(3009, D("10"))]))
            direct = snapshot()
            raise Rollback
    except Rollback:
        pass
    bi.process(p.line["id"], p.action())
    assert snapshot() == direct
    assert {r[2] for r in _bank_rows(conn, 3001) + _bank_rows(conn, 3009) if r[1] == "BR-9"} == {D1}


def test_partial_and_prepayment(bi, conn):
    bi.import_file(statement(Entry(D("30.00"), D1, "Cees", None, "F-3007"),
                             Entry(D("70.00"), D1, "Cees", None, "F-3007 en meer")))
    p30, p70 = sorted(bi.proposals("1100"), key=lambda p: p.line["amount"])
    bi.process(p30.line["id"], p30.action())
    assert paid(conn, "ar", 3007) == D("30.00")
    # tweede regel: post staat nog 35,40 open; 70 = 35,40 + vooruitontvangst 34,60
    [p] = bi.proposals("1100")
    assert p.allocations[0].paid == D("35.40") and p.prepayment == D("34.60")
    with pytest.raises(BankImportError, match="sluit niet aan"):
        bi.process(p.line["id"], PayAction("customer", 902, [PaymentLine(3007, D("35.40"))]))
    res = bi.process(p.line["id"], p.action())
    assert len(res.trans_ids) == 2 and paid(conn, "ar", 3007) == D("65.40")
    prepay = res.trans_ids[1]
    assert [r[2] for r in _bank_rows(conn, prepay)] == [D1]


def test_multiple_invoices_manual(bi, conn):
    bi.import_file(statement(Entry(D("-200.00"), D1, "Graanhandel", MOLEN, "deel")))
    [p] = bi.proposals("1100")
    res = bi.process(p.line["id"], PayAction("vendor", 905, [PaymentLine(4002, D("150")), PaymentLine(4001, D("50"))]))
    assert sorted(res.trans_ids) == [4001, 4002]
    assert paid(conn, "ap", 4002) == D("450") and paid(conn, "ap", 4001) == D("50")
    assert all(r[2] == D1 for r in _bank_rows(conn, 4001) if r[1] == res.source)


def test_memorial_with_vat_and_rule(bi, conn):
    bi.import_file(statement(Entry(D("-121.00"), D1, "KPN BV", KPN, "Abonnement juli", bank_ref="BR-K"),
                             Entry(D("-60.50"), date(2026, 7, 2), "KPN BV", KPN, "Abonnement aug")))
    p1, p2 = bi.proposals("1100")
    assert p1.category is None
    res = bi.process(p1.line["id"], GLAction("4900", tax_accno="1800", description="Telefoon"))
    gl_id = res.trans_ids[0]
    rows = conn.execute("""SELECT c.accno, ac.amount, ac.cleared FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                           WHERE ac.trans_id = %s ORDER BY c.accno""", (gl_id,)).fetchall()
    assert rows == [("1100", 121.0, D1), ("1800", -21.0, None), ("4900", -100.0, None)]
    assert q1(conn, "SELECT description FROM gl WHERE id = %s", gl_id)[0] == "Telefoon"
    # vaste regel vanuit de regel → de volgende regel krijgt het voorstel
    bi.rule_from_line(p1.line["id"], "4900", tax_accno="1800")
    [p] = bi.proposals("1100")
    assert p.category == "e" and p.rule["iban"] == KPN and p.rule["direction"] == "-"
    res = bi.process(p.line["id"], p.action())
    assert bi.lines(status=None, line_ids=[p.line["id"]])[0]["rule_id"] == p.rule["id"]
    [row] = _bank_rows(conn, res.trans_ids[0])
    assert row[0] == 60.5 and row[2] == date(2026, 7, 2)
    assert len(bi.rules()) == 1
    with pytest.raises(BankImportError):
        bi.save_rule(Rule(accno="4900"))
    bi.delete_rule(p.rule["id"])
    assert bi.rules() == []


def test_match_existing_and_wrong_amount(bi, conn, config):
    gl = GeneralLedger(conn, config)
    a = gl.post_transaction(GLTransaction(transdate=date(2026, 7, 5), description="Storting",
                                          lines=[JournalLine("1100", debit=D("80")), JournalLine("1001", credit=D("80"))]))
    b = gl.post_transaction(GLTransaction(transdate=date(2026, 7, 5), description="Andere",
                                          lines=[JournalLine("1100", debit=D("81")), JournalLine("1001", credit=D("81"))]))
    bi.import_file(statement(Entry(D("80.00"), D1, None, None, "storting")))
    [p] = bi.proposals("1100")
    assert p.category == "d" and p.match["trans_id"] == a
    with pytest.raises(BankImportError, match="niet gelijk"):
        bi.process(p.line["id"], MatchAction(b, date(2026, 7, 5)))
    res = bi.process(p.line["id"], p.action())
    assert res.status == "matched"
    assert _bank_rows(conn, a)[0][2] == D1
    assert _bank_rows(conn, b)[0][2] is None


def test_ignore_and_reopen(bi):
    bi.import_file(statement(Entry(D("1.00"), D1, None, None, "x")))
    [p] = bi.proposals("1100")
    bi.process(p.line["id"], IgnoreAction("dubbel"))
    assert bi.proposals("1100") == []
    bi.reopen(p.line["id"])
    assert len(bi.proposals("1100")) == 1


def test_concurrent_processing_books_once(bi, conn, config):
    bi.import_file(statement(Entry(D("222.50"), D1, "Bakkerij Cees", CEES, reference=REF_3002)))
    [p] = bi.proposals("1100")
    other = psycopg.connect(f"{DSN} dbname={conn.info.dbname}", autocommit=True)
    errors = []

    def second():
        try:
            BankImport(other, UserConfig(login="admin")).process(p.line["id"], p.action())
        except BankImportError as exc:
            errors.append(str(exc))

    try:
        with conn.transaction():
            bi.process(p.line["id"], p.action())
            t = threading.Thread(target=second)
            t.start()
            time.sleep(0.5)
            assert t.is_alive()          # wacht op de rij-lock van de eerste
        t.join(10)
    finally:
        other.close()
    assert errors and "al verwerkt" in errors[0]
    assert paid(conn, "ar", 3002) == D("272.50")
    assert q1(conn, "SELECT count(*) FROM payment WHERE trans_id = 3002")[0] == 1


def test_foreign_currency_needs_rate(bi, conn):
    # rekening 1140 als USD-rekening (eerdere afschriften bepalen de valuta niet; de boekingen wel)
    data = camt(OTHER, [Entry(D("500.00"), D1, "Overseas", None, "F-3003")], D("0"), ccy="USD")
    with pytest.raises(BankImportError, match="is in USD, maar rekening 1140 is in EUR"):
        bi.import_file(data, chart_accno="1140")


def test_concurrent_line_lock_prevents_double_bulk(bi, conn):
    """Twee regels die dezelfde post voorstellen: de post wordt maar één keer voorgesteld."""
    bi.import_file(statement(Entry(D("10.00"), D1, None, None, "F-3009"),
                             Entry(D("10.00"), date(2026, 7, 2), None, None, "F-3009 nogmaals")))
    p1, p2 = bi.proposals("1100")
    assert p1.category == "b" and p2.category is None


def test_suggest_keyword():
    """PB27: kenmerkend stuk van omschrijving of naam als trefwoord (geen cijfers, geen stopwoorden)."""
    from sqlledger.bankimport import suggest_keyword
    assert suggest_keyword("Bank", "Coupon 4% Staatslening 2037") == "coupon"
    assert suggest_keyword(None, "Dividend Royal Dutch Shell 0,47") == "dividend royal"
    assert suggest_keyword("KPN B.V.", "12345 6789") == "kpn"
    assert suggest_keyword("", "") == ""
    text = "Europese incasso: Ziggo Services B.V."
    assert suggest_keyword(None, text) in text.lower()
