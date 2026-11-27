"""Bankproef 2026-11 (BT2–BT10, VZ1–VZ6): aansluiting, ABN-rekeningnummer, samen afletteren, zeker afletteren,
IBAN leren/opslaan, voorstel zonder IBAN (naam en bedrag, historie), voorgestelde vaste regels."""

from datetime import date
from decimal import Decimal as D

import pytest

from bankfiles import Entry, make_iban
from sqlledger import GeneralLedger, GLTransaction, JournalLine
from sqlledger.bankimport import BankImportError, GLAction, MatchAction
from sqlledger.bankstatement import card_merchant, nl_iban, parse
from sqlledger.payments import Payment, PaymentLine, Payments
from test_bankimport import (CEES, D1, KPN, LEV_B, MOLEN, OWN, bi, by_amount, q1, statement)  # noqa: F401

NEW_CEES = make_iban("NL", "BUNQ2000000002")


def gl_on_bank(conn, config, day, amount, description, contra="1001", source=None):
    """Memoriaal op de bank (1100): + = bij."""
    lines = [JournalLine("1100", debit=amount, source=source), JournalLine(contra, credit=amount)] if amount > 0 else \
        [JournalLine("1100", credit=-amount, source=source), JournalLine(contra, debit=-amount)]
    return GeneralLedger(conn, config).post_transaction(GLTransaction(transdate=day, description=description,
                                                                       lines=lines))


def cleared(conn, tid):
    return conn.execute("""SELECT ac.cleared FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                           WHERE ac.trans_id = %s AND c.accno = '1100'""", (tid,)).fetchall()


# ------------------------------------------------------------------ inlezen (B5/B6/B8/BT9)

def test_same_day_statements_chain(bi):
    """B6: twee afschriften op dezelfde dag sluiten op elkaar aan: geen onterechte waarschuwing."""
    bi.import_file(statement(Entry(D("5"), D1, description="x"), stmt_id="S1"))
    res = bi.import_file(statement(Entry(D("6"), D1, description="y"), opening="1005", stmt_id="S2"))
    assert not any("vorige afschrift" in w for w in res.statements[0].warnings)
    res = bi.import_file(statement(Entry(D("7"), D1, description="z"), opening="999", stmt_id="S3"))
    assert any("vorige afschrift" in w for w in res.statements[0].warnings)


def test_ledger_warning_summarized(bi, conn):
    """BT6: grootboekverschil alleen bij het eerste afschrift, met het aantal volgende."""
    from bankfiles import camt
    res = bi.import_file(statement(Entry(D("5"), D1, description="a"), stmt_id="S1"))
    assert any("Grootboeksaldo" in w for w in res.statements[0].warnings)
    # één bestand met drie afschriften
    import re
    parts = [camt(OWN, [Entry(D("1"), date(2026, 7, d), description=f"r{d}")], D("1005") + d - 2,
                  stmt_id=f"T{d}") for d in (2, 3, 4)]
    stmts = [re.search(rb"<Stmt>.*</Stmt>", p, re.S).group(0) for p in parts]
    data = parts[0].replace(stmts[0], b"".join(stmts))
    res = bi.import_file(data)
    first, *rest = res.statements
    assert any("Grootboeksaldo" in w for w in first.warnings)
    assert any("Ook bij 2 volgende afschrift(en)" in w for w in first.warnings)
    assert all(not any("Grootboeksaldo" in w for w in r.warnings) for r in rest)
    stored = q1(conn, "SELECT warnings FROM x_bank_statement WHERE id = %s", rest[0].id)[0]
    assert not stored or "Grootboeksaldo" not in stored


ABN = """ABNANL2A
940
ABNANL2A
:20:ABN AMRO BANK NV
:25:417164300
:28:30801/1
:60F:C260701EUR100,00
:61:2607010701D25,50N200NONREF
:86:BEA, BETAALPAS                   BCK*GAMMA GOUDA
NR:BS164945, 01.07.26/16:22      ALPHEN AAN DE
KAARTNUMMER: **6570
:61:2607010701D4,25N526NONREF
:86:ABN AMRO BANK N.V.               DE SPECIFICATIE VINDT U IN UW
NOTA PRODUCTEN EN DIENSTEN.
:62F:C260701EUR70,25
-
""".encode()


def test_abn_account_number_and_names(bi, conn):
    """BT9: kaal ABN-rekeningnummer wordt IBAN; een eerder als kaal nummer opgeslagen rekening wordt herkend en
    bijgewerkt. BT8: winkelnaam en bank als tegenpartij."""
    [st] = parse(ABN)
    assert st.iban == nl_iban("ABNA", "417164300") == "NL91ABNA0417164300"
    a, b = st.lines
    assert a.counterparty_name == "GAMMA GOUDA" and b.counterparty_name == "ABN AMRO BANK N.V."
    assert card_merchant("BEA, BETAALPAS HORNBACH NIEUWERKERK,PAS492 NR:CT404723, 05.02.26") == "HORNBACH NIEUWERKERK"
    assert card_merchant("BEA, BETAALPAS   720 HORNBACH NIEUWERKE NR:CT404723, 28.10.26") == "HORNBACH NIEUWERKE"
    assert card_merchant("SEPA OVERBOEKING") is None
    conn.execute("INSERT INTO bank (id, iban) VALUES (10051, '417164300')")       # 1140, kaal nummer
    res = bi.import_file(ABN)
    assert res.statements[0].chart_accno == "1140"
    assert q1(conn, "SELECT iban FROM bank WHERE id = 10051")[0] == "NL91ABNA0417164300"


# ------------------------------------------------------------------ afletteren (BT4/BT5/BT2)

def test_sure_match_only_unique_with_iban(bi, conn, config):
    """BT5/VZ3: afletteren tegen de enige bestaande boeking (zelfde bedrag, ≤ 2 dagen) is zeker; zonder
    rekeningnummer van de tegenpartij (pin) of bij twee kandidaten niet."""
    g1 = gl_on_bank(conn, config, D1, D("-42.10"), "Verzekering")
    gl_on_bank(conn, config, D1, D("-9.99"), "Pin 1")
    gl_on_bank(conn, config, D1, D("-15.00"), "Kosten a")
    gl_on_bank(conn, config, D1, D("-15.00"), "Kosten b")
    bi.import_file(statement(Entry(D("-42.10"), D1, "Verzekeraar", KPN, "premie"),
                             Entry(D("-9.99"), D1, "GAMMA GOUDA", None, "BEA, BETAALPAS GAMMA GOUDA NR:1"),
                             Entry(D("-15.00"), D1, "Bank", KPN, "kosten 1"),
                             Entry(D("-15.00"), D1, "Bank", KPN, "kosten 2")))
    props = bi.proposals("1100")
    p = by_amount(props, "-42.10")
    assert p.kind == "match" and p.match["trans_id"] == g1 and p.unique and p.sure
    p = by_amount(props, "-9.99")
    assert p.kind == "match" and p.card and not p.sure
    assert all(p.kind == "match" and not p.sure for p in props if p.line["amount"] == D("-15.00"))


def test_match_learns_iban_not_for_collective(bi, conn, config):
    """BT2: afletteren van een inkoopbetaling geeft het rekeningnummer aan de leverancier (bestaande velden), niet
    bij een verzamelrelatie en niet als er al een nummer staat."""
    conn.execute("DELETE FROM bank WHERE id IN (901, 905)")
    Payments(conn, config).post_payment(Payment(vc="vendor", vc_id=901, datepaid=D1, payment_accno="1100",
                                                source="P-1", amount=D("121"), lines=[PaymentLine(4003, D("121"))]))
    bi.import_file(statement(Entry(D("-121.00"), D1, "Leverancier B", LEV_B, "Factuur")))
    [p] = bi.proposals("1100")
    assert p.kind == "match" and p.category == "d"
    bi.process(p.line["id"], p.action())
    assert q1(conn, "SELECT iban FROM bank WHERE id = 901")[0] == LEV_B
    line = bi.lines(status=None, line_ids=[p.line["id"]])[0]
    assert (line["vc"], line["vc_id"]) == ("vendor", 901)
    # verzamelrelatie: facturen noemen drie verschillende leveranciers
    for tid, name in ((4001, "Alfa"), (4002, "Beta")):
        conn.execute("UPDATE ap SET description = %s WHERE id = %s", (f"{tid} | {name}", tid))
    conn.execute("""INSERT INTO ap (id, invnumber, vendor_id, transdate, duedate, amount, netamount, paid, curr,
                                    approved, onhold, exchangerate, description)
                    VALUES (4006, 'I-4006', 905, '2026-06-01', '2026-06-01', 5, 5, 5, 'EUR', true, false, 1,
                            '9 | Gamma')""")
    from sqlledger._db import cursor
    with cursor(conn) as cur:
        assert bi._collective(cur, "vendor", 905, MOLEN)
        assert not bi._collective(cur, "vendor", 901, LEV_B)


def test_together_and_multi(bi, conn, config):
    """BT4: twee bankregels tegen één boeking, en één bankregel tegen twee boekingen."""
    g2 = gl_on_bank(conn, config, D1, D("2.00"), "Mollie test")
    a = gl_on_bank(conn, config, D1, D("-40.00"), "Leverancier X")
    b = gl_on_bank(conn, config, D1, D("10.00"), "Leverancier X")
    bi.import_file(statement(Entry(D("1.00"), D1, "Mollie", KPN, "test 1"),
                             Entry(D("1.00"), D1, "Mollie", KPN, "test 2"),
                             Entry(D("-30.00"), D1, "Leverancier X", LEV_B, "netto")))
    props = bi.proposals("1100")
    ones = [p for p in props if p.line["amount"] == D("1.00")]
    first, second = ones
    assert first.kind == "match" and first.match["trans_id"] == g2 and [l["id"] for l in first.together] == \
        [second.line["id"]] and not first.sure
    assert second.kind is None and "samen afletteren" in second.reason
    p30 = by_amount(props, "-30.00")
    assert p30.kind == "match" and sorted(g["trans_id"] for g in p30.matches) == sorted([a, b]) and not p30.sure
    bi.process(first.line["id"], first.action())
    assert {l["status"] for l in bi.lines(status=None, line_ids=[first.line["id"], second.line["id"]])} == {"matched"}
    assert cleared(conn, g2) == [(D1,)]
    bi.process(p30.line["id"], p30.action())
    assert cleared(conn, a) == [(D1,)] and cleared(conn, b) == [(D1,)]
    # met de hand: bedragen sluiten niet aan
    g3 = gl_on_bank(conn, config, D1, D("-5.00"), "Los")
    bi.import_file(statement(Entry(D("-6.00"), D1, "Los", None, "los"), stmt_id="S9", opening="972"))
    [p] = bi.proposals("1100")
    with pytest.raises(BankImportError, match="niet gelijk"):
        bi.process(p.line["id"], MatchAction(g3, D1))
    assert any(g["trans_id"] == g3 for g in bi.open_bookings(p.line["id"]))


# ------------------------------------------------------------------ zonder IBAN (VZ1/VZ5/VZ6)

def test_name_and_amount(bi, conn):
    """VZ1: geen rekeningnummer bij de relatie: open post met precies het bedrag en een naam die erop lijkt;
    nooit zeker. VZ6: ander bedrag → deelbetaling."""
    conn.execute("DELETE FROM bank WHERE id IN (901, 905)")
    bi.import_file(statement(Entry(D("-121.00"), D1, "LEVERANCIER B BV", make_iban("NL", "INGB0009999999"), "fact"),
                             Entry(D("-100.00"), D1, "De Molen", None, "deel")))
    props = bi.proposals("1100")
    p = by_amount(props, "-121.00")
    assert (p.kind, p.category, p.vc_id) == ("payment", "f", 901) and p.exact and not p.sure
    assert [a.trans_id for a in p.allocations] == [4003]
    p = by_amount(props, "-100.00")
    # eerdere betalingen aan De Molen (historie, g) of alleen de naam (f): deelbetaling, nooit zeker
    assert (p.kind, p.vc_id) == ("payment", 905) and p.category in ("f", "g") and not p.exact and not p.sure
    assert sum(a.paid for a in p.allocations) == D("100.00")


def test_duplicate_open_posts_doubt(bi, conn):
    """BT10/VZ5: twee open posten met hetzelfde bedrag bij dezelfde relatie: de oudste, met twijfel."""
    conn.execute("""INSERT INTO ap (id, invnumber, vendor_id, transdate, duedate, amount, netamount, paid, curr,
                                    approved, onhold, exchangerate)
                    VALUES (4007, 'I-4007', 901, '2026-06-01', '2026-06-01', 121, 121, 0, 'EUR', true, false, 1)""")
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                    VALUES (4007, 10065, 121, '2026-06-01', true), (4007, 10166, -121, '2026-06-01', true)""")
    bi.import_file(statement(Entry(D("-121.00"), D1, "Leverancier B", LEV_B, "Factuur")))
    [p] = bi.proposals("1100")
    assert p.category == "c" and p.doubt and [a.trans_id for a in p.allocations] == [4003]
    assert any("2 open posten met hetzelfde bedrag" in w for w in p.warnings)


def test_history_memorial_and_relation(bi, conn, config):
    """VZ1: zoals eerder: memoriaal met dezelfde tegenrekening; relatie bekend maar geen open post → voorstel om
    eerst de factuur in te voeren."""
    gl_on_bank(conn, config, date(2026, 6, 1), D("-500.00"), "Vastgoed Jansen huur juni", contra="4900")
    conn.execute("DELETE FROM bank WHERE id = 901")
    Payments(conn, config).post_payment(Payment(vc="vendor", vc_id=901, datepaid=date(2026, 6, 2),
                                                payment_accno="1100", source="P-2", amount=D("121"),
                                                lines=[PaymentLine(4003, D("121"))]))
    bi.import_file(statement(Entry(D("-500.00"), D1, "VASTGOED JANSEN", None, "huur juli"),
                             Entry(D("-77.00"), D1, "Leverancier B", None, "nieuwe levering")))
    props = bi.proposals("1100")
    p = by_amount(props, "-500.00")
    assert (p.kind, p.category, p.rule["accno"], p.rule["id"]) == ("gl", "g", "4900", None) and not p.sure
    res = bi.process(p.line["id"], p.action())
    assert res.kind == "gl"
    p = by_amount(props, "-77.00")
    assert p.kind is None and p.category == "g" and p.candidates[0]["vc_id"] == 901
    assert "voer eerst de factuur in" in p.reason


# ------------------------------------------------------------------ IBAN opslaan (VZ4) en vaste regels (BT3)

def test_iban_offer_replace(bi, conn):
    bi.import_file(statement(Entry(D("65.40"), D1, "Cees", NEW_CEES, "F-3007")))
    [p] = bi.proposals("1100")
    bi.process(p.line["id"], p.action())
    offer = bi.vc_iban_offer(p.line["id"])
    assert offer["current"] == CEES and offer["iban"] == NEW_CEES and bi.vc_iban_missing(p.line["id"]) is None
    assert not bi.save_vc_iban(p.line["id"])
    assert bi.save_vc_iban(p.line["id"], replace=True)
    assert q1(conn, "SELECT iban FROM bank WHERE id = 902")[0] == NEW_CEES
    assert bi.vc_iban_offer(p.line["id"]) is None


def test_rule_proposals(bi):
    bi.import_file(statement(Entry(D("-60.50"), D1, "KPN BV", KPN, "juli"),
                             Entry(D("-60.50"), date(2026, 7, 2), "KPN BV", KPN, "aug"),
                             Entry(D("-9.00"), date(2026, 7, 3), "GAMMA", None, "BEA, BETAALPAS GAMMA NR:1"),
                             Entry(D("-9.00"), date(2026, 7, 4), "GAMMA", None, "BEA, BETAALPAS GAMMA NR:2")))
    for p in bi.proposals("1100"):
        bi.process(p.line["id"], GLAction("4900", tax_accno="1800" if p.line["iban"] else None))
    [r] = bi.rule_proposals("1100")
    assert (r["iban"], r["direction"], r["accno"], r["tax_accno"], r["count"], r["varies"]) == \
        (KPN, "-", "4900", "1800", 2, False)
    from sqlledger.bankimport import Rule
    bi.save_rule(Rule(accno="4900", iban=KPN, direction="-", tax_accno="1800"))
    assert bi.rule_proposals("1100") == []
