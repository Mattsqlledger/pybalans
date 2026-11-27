"""SEPA-betaalbestand (BEHEER_EXTRA_ANALYSE.md IM9 A1, keuzes SP1–SP13; ``sqlledger.sepa``)."""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal as D

import pytest

from bankfiles import Entry, camt, make_iban, nl_reference, rf_reference
from sqlledger.bankimport import BankImport
from sqlledger.errors import ValidationError
from sqlledger.sepa import (Pick, SepaChangedError, SepaExport, next_workday, remittance, sepa_id, sepa_text,
                            validate)

OWN = make_iban("NL", "INGB0001234567")
LEV_B = make_iban("NL", "TRIO0198765432")        # leverancier 901
MOLEN = make_iban("NL", "INGB0007777777")        # leverancier 905
REF_4002 = nl_reference("000000000004002")
RF_4001 = rf_reference("M4001")
EXEC = date(2026, 7, 1)
TODAY = date(2026, 6, 30)


@pytest.fixture
def sx(conn, config):
    conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
    conn.execute("UPDATE ap SET paid = 0 WHERE paid IS NULL")
    conn.execute("UPDATE tax SET validto = '2012-09-30' WHERE rate IN (0.19, 0.06)")
    conn.execute("INSERT INTO bank (id, iban, bic) VALUES (10044, %s, 'INGBNL2A')", (OWN,))
    conn.execute("INSERT INTO bank (id, iban, bic) VALUES (901, %s, 'TRIONL2U')", (LEV_B,))
    conn.execute("INSERT INTO bank (id, iban) VALUES (905, %s)", (MOLEN,))
    conn.execute("UPDATE ap SET dcn = %s WHERE id = 4002", (REF_4002,))
    return SepaExport(conn, config)


def q(conn, sql, *params):
    return conn.execute(sql, params).fetchall()


def ns(xml: bytes) -> str:
    return ET.fromstring(xml).tag.split("}")[0] + "}"


def texts(xml: bytes, tag: str):
    n = ns(xml)
    return [e.text for e in ET.fromstring(xml).iter(f"{n}{tag}")]


# ------------------------------------------------------------------ hulpjes

def test_text_and_ids():
    assert sepa_text("Café & Zoon — Größe   (Ø)", 70) == "Cafe + Zoon Grosse (O)"
    assert sepa_text("x" * 80, 70) == "x" * 70
    assert sepa_id("//F 2026/001//") == "F2026/001"
    assert sepa_id("A" * 40) == "A" * 35
    assert next_workday(date(2026, 10, 9)) == date(2026, 10, 12)     # vrijdag -> maandag
    assert next_workday(date(2026, 10, 12)) == date(2026, 10, 13)


def test_remittance():
    assert remittance("I-1", REF_4002) == ("nl", REF_4002)
    assert remittance("I-1", "RF18 5390 0754 7034") == ("rf", "RF18539007547034")
    assert remittance("I-1", None) == ("", "I-1")
    assert remittance("I-1", "klant 77") == ("", "I-1 klant 77")
    assert remittance("I-1", "i-1") == ("", "I-1")
    assert remittance("I-1", "1234567890123456") == ("", "I-1 1234567890123456")   # geen geldig kenmerk


# ------------------------------------------------------------------ selectie

def test_accounts(sx, conn):
    accs = {a.accno: a for a in sx.accounts()}
    assert accs["1100"].iban == OWN and accs["1100"].bic == "INGBNL2A"
    conn.execute("UPDATE bank SET iban = 'NL00FOUT' WHERE id = 10044")
    assert {a.accno: a for a in sx.accounts()}["1100"].iban == ""


def test_candidates_default_selection(sx, conn):
    sel = sx.candidates(EXEC, duedateto=EXEC)
    ids = [c.trans_id for c in sel.candidates]
    assert ids == [4003, 951, 4002]                 # op vervaldatum; 4001 vervalt pas op 20-7
    assert all(c.default_checked(EXEC) for c in sel.candidates)
    c = {c.trans_id: c for c in sel.candidates}
    assert c[4002].due == D("305.00") and c[4002].suggested == D("305.00")
    assert (c[4002].ref_kind, c[4002].ref) == ("nl", REF_4002)
    assert (c[4003].ref_kind, c[4003].ref, c[4003].bic) == ("", "I-4003", "TRIONL2U")
    wide = sx.candidates(EXEC)
    late = [x for x in wide.candidates if x.trans_id == 4001][0]
    assert not late.default_checked(EXEC)
    assert sx.candidates(EXEC, vendor="molen").candidates[0].vc_id == 905
    assert {x.vc_id for x in sx.candidates(EXEC, vendor="molen").candidates} == {905}


def test_problems_credit_notes_and_currency(sx, conn):
    conn.execute("DELETE FROM bank WHERE id = 905")
    sel = sx.candidates(EXEC)
    assert {c.trans_id: c.problem for c in sel.candidates}[4002] == "iban_missing"
    assert not [c for c in sel.candidates if c.trans_id == 4002][0].default_checked(EXEC)
    conn.execute("INSERT INTO bank (id, iban) VALUES (905, %s)", (make_iban("BR", "00360305000010009795493"),))
    assert {c.trans_id: c.problem for c in sx.candidates(EXEC).candidates}[4002] == "iban_not_sepa"
    conn.execute("UPDATE bank SET iban = %s WHERE id = 905", (make_iban("CH", "00762011623852957"),))
    conn.execute("DELETE FROM address WHERE trans_id = 905")
    assert {c.trans_id: c.problem for c in sx.candidates(EXEC).candidates}[4002] == "address_missing"
    conn.execute("INSERT INTO address (trans_id, address1, zipcode, city, country) "
                 "VALUES (905, 'Bahnhofstrasse 1', '8001', 'Zürich', 'Schweiz')")
    c = [c for c in sx.candidates(EXEC).candidates if c.trans_id == 4002][0]
    assert c.problem is None and c.address == ["Bahnhofstrasse 1", "8001 Zurich"]
    # creditnota (debetnota) van leverancier 901 en een post in dollars
    conn.execute("""INSERT INTO ap (id, invnumber, transdate, vendor_id, amount, netamount, paid, curr, approved,
                                    onhold, exchangerate, duedate)
                    VALUES (4101, 'CN-1', '2026-06-01', 901, -50, -50, 0, 'EUR', '1', '0', 1, '2026-06-01'),
                           (4102, 'USD-1', '2026-06-01', 901, 80, 80, 0, 'USD', '1', '0', 1, '2026-06-01')""")
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                    SELECT 4101, id, 50, '2026-06-01', '1' FROM chart WHERE accno = '1600'""")
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                    SELECT 4102, id, -80, '2026-06-01', '1' FROM chart WHERE accno = '1600'""")
    sel = sx.candidates(EXEC)
    assert [(n.trans_id, n.due) for n in sel.credit_notes] == [(4101, D("-50.00"))]
    assert 4101 not in [c.trans_id for c in sel.candidates]
    assert sel.other_currency == 1


def test_discount_on_execution_date(sx, conn):
    conn.execute("UPDATE ap SET cashdiscount = 0.02, discountterms = 14, netamount = 1000 WHERE id = 4001")
    c = [c for c in sx.candidates(date(2026, 7, 3)).candidates if c.trans_id == 4001][0]
    assert (c.due, c.discount, c.suggested) == (D("1210.00"), D("20.00"), D("1190.00"))
    c = [c for c in sx.candidates(date(2026, 7, 6)).candidates if c.trans_id == 4001][0]
    assert (c.discount, c.suggested) == (D("0.00"), D("1210.00"))     # termijn voorbij


# ------------------------------------------------------------------ bestand

@pytest.mark.parametrize("version", ["03", "09"])
def test_create_valid_xml_and_recorded(sx, conn, version):
    conn.execute("UPDATE ap SET dcn = %s WHERE id = 4001", (RF_4001,))
    res = sx.create("1100", EXEC, version, [Pick(4003, D("121.00"), D("121.00")), Pick(4002, D("305")),
                                             Pick(4001, D("200.00"))], today=TODAY)
    validate(version, res.xml)
    assert (res.count, res.total, res.filename) == (3, D("626.00"), f"betaalbestand-2026-07-01-{res.id}.xml")
    assert f"pain.001.001.{version}" in ns(res.xml)
    assert texts(res.xml, "EndToEndId") == ["I-4003", "I-4002", "I-4001"]
    assert texts(res.xml, "InstdAmt") == ["121.00", "305.00", "200.00"]
    assert texts(res.xml, "BtchBookg") == ["false"] and texts(res.xml, "CtrlSum") == ["626.00", "626.00"]
    assert texts(res.xml, "Ustrd") == ["I-4003"]
    assert texts(res.xml, "Ref") == [REF_4002, RF_4001]
    assert texts(res.xml, "Issr") == ["CUR"]                       # alleen bij het NL-kenmerk
    assert texts(res.xml, "BIC" if version == "03" else "BICFI") == ["INGBNL2A", "TRIONL2U"]
    assert texts(res.xml, "Nm")[:2] == ["Testbedrijf BV", "Testbedrijf BV"]
    assert "2026-07-01" in texts(res.xml, "ReqdExctnDt" if version == "03" else "Dt")
    assert texts(res.xml, "MsgId")[0].startswith("PB-")
    # vastgelegd: export, regels, audittrail; niets geboekt
    assert q(conn, "SELECT count, total FROM x_sepa_export") == [(3, D("626.00"))]
    assert sorted(q(conn, "SELECT trans_id, amount FROM x_sepa_item")) == [(4001, D("200.00")), (4002, D("305.00")),
                                                                         (4003, D("121.00"))]
    assert sorted(q(conn, "SELECT trans_id, formname, action FROM audittrail WHERE formname = 'sepa'")) == [
        (4001, "sepa", "exported"), (4002, "sepa", "exported"), (4003, "sepa", "exported")]
    assert q(conn, "SELECT paid FROM ap WHERE id = 4003") == [(D("0"),)]
    c = {c.trans_id: c for c in sx.candidates(EXEC).candidates}
    assert c[4003].exported and not c[4003].default_checked(EXEC)
    assert sx.export_file(res.id) == (res.filename, res.xml)
    assert [e["id"] for e in sx.exports()] == [res.id]


def test_non_eea_addresses_and_no_bic(sx, conn):
    conn.execute("UPDATE bank SET iban = %s WHERE id = 905", (make_iban("CH", "00762011623852957"),))
    conn.execute("DELETE FROM address WHERE trans_id = 905")
    conn.execute("INSERT INTO address (trans_id, address1, zipcode, city) VALUES (905, 'Bahnhofstrasse 1', '8001', "
                 "'Zürich')")
    conn.execute("UPDATE defaults SET fldvalue = 'Dorpsstraat 1\n1234 AB Ergens' WHERE fldname = 'address'")
    conn.execute("INSERT INTO defaults (fldname, fldvalue) SELECT 'address', 'Dorpsstraat 1\n1234 AB Ergens' "
                 "WHERE NOT EXISTS (SELECT 1 FROM defaults WHERE fldname = 'address')")
    conn.execute("UPDATE bank SET bic = NULL WHERE id = 10044")
    for version in ("03", "09"):
        res = sx.create("1100", EXEC, version, [Pick(4002, D("305"))], today=TODAY)
        validate(version, res.xml)
        assert texts(res.xml, "AdrLine") == ["Dorpsstraat 1", "1234 AB Ergens", "Bahnhofstrasse 1", "8001 Zurich"]
        assert texts(res.xml, "Ctry") == ["NL", "CH"]
        assert texts(res.xml, "Id")[-1] != "NOTPROVIDED" and "NOTPROVIDED" in texts(res.xml, "Id")


def test_create_refusals_leave_nothing(sx, conn):
    def nothing():
        return not q(conn, "SELECT 1 FROM x_sepa_export") and not q(conn, "SELECT 1 FROM x_sepa_item")

    with pytest.raises(ValidationError, match="verleden"):
        sx.create("1100", date(2026, 6, 29), "03", [Pick(4003, D("1"))], today=TODAY)
    with pytest.raises(ValidationError, match="minstens één"):
        sx.create("1100", EXEC, "03", [], today=TODAY)
    with pytest.raises(ValidationError, match="hoger dan wat openstaat"):
        sx.create("1100", EXEC, "03", [Pick(4003, D("121.01"))], today=TODAY)
    with pytest.raises(ValidationError, match="groter zijn dan 0"):
        sx.create("1100", EXEC, "03", [Pick(4003, D("0"))], today=TODAY)
    with pytest.raises(SepaChangedError):
        sx.create("1100", EXEC, "03", [Pick(4003, D("100"), D("120.00"))], today=TODAY)
    with pytest.raises(SepaChangedError):
        sx.create("1100", EXEC, "03", [Pick(999999, D("100"))], today=TODAY)
    with pytest.raises(ValidationError, match="betaalrekening"):
        sx.create("9999", EXEC, "03", [Pick(4003, D("100"))], today=TODAY)
    conn.execute("DELETE FROM bank WHERE id = 901")
    with pytest.raises(ValidationError, match="kan niet in het betaalbestand"):
        sx.create("1100", EXEC, "03", [Pick(4003, D("100"))], today=TODAY)
    conn.execute("UPDATE ap SET onhold = '1' WHERE id = 4002")
    with pytest.raises(SepaChangedError):
        sx.create("1100", EXEC, "03", [Pick(4002, D("100"))], today=TODAY)
    conn.execute("DELETE FROM bank WHERE id = 10044")
    with pytest.raises(ValidationError, match="geen geldige IBAN"):
        sx.create("1100", EXEC, "03", [Pick(4001, D("100"))], today=TODAY)
    assert nothing()


def test_duplicate_invoice_numbers_get_unique_end_to_end(sx, conn):
    conn.execute("UPDATE ap SET invnumber = 'F 1' WHERE id IN (4001, 4003)")
    res = sx.create("1100", EXEC, "03", [Pick(4003, D("121")), Pick(4001, D("1210"))], today=TODAY)
    assert texts(res.xml, "EndToEndId") == ["F1", "F1-2"]


# ------------------------------------------------------------------ rondgang: bestand -> afschrift -> afletteren

def test_round_trip_bank_statement_matches(sx, conn, config):
    res = sx.create("1100", EXEC, "03", [Pick(4003, D("121.00")), Pick(4002, D("305.00"))], today=TODAY)
    e2e = texts(res.xml, "EndToEndId")
    bi = BankImport(conn, config)
    bi.import_file(camt(OWN, [
        Entry(D("-121.00"), EXEC, "Leverancier B", LEV_B, "I-4003", e2e=e2e[0]),
        Entry(D("-305.00"), EXEC, "Graanhandel", MOLEN, None, reference=REF_4002, e2e=e2e[1]),
    ], D("1000.00")))
    props = {p.line["amount"]: p for p in bi.proposals("1100")}
    p = props[D("-121.00")]
    assert (p.category, p.vc_id, p.exact) == ("b", 901, True)
    assert [(a.trans_id, a.paid) for a in p.allocations] == [(4003, D("121.00"))]
    p = props[D("-305.00")]
    assert (p.category, p.vc_id, p.exact) == ("a", 905, True)
    assert [(a.trans_id, a.paid) for a in p.allocations] == [(4002, D("305.00"))]
