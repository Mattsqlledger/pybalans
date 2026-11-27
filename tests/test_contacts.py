"""Klanten en leveranciers (sqlledger.contacts, port van CT.pm)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from sqlledger import Contacts, ContactSearch, NotFoundError, Relation, ValidationError
from sqlledger.contacts import bic_valid, clean_name, iban_valid
from sqlledger.models import Reference

TABLES = ("customer", "vendor", "address", "contact", "bank", "shipto", "customertax", "vendortax",
          "reference", "defaults", "audittrail")


def dump(conn):
    out = {}
    for t in TABLES:
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        out[t] = sorted(rows, key=repr)
    return out


#: de testdata (compare/seed.sql) zet adressen en contactpersonen met vaste id's neer
SEQUENCES = ["SELECT setval('addressid', (SELECT max(id) FROM address))",
             "SELECT setval('contactid', (SELECT max(id) FROM contact))"]


@pytest.fixture
def ct(conn, config):
    for sql in SEQUENCES:
        conn.execute(sql)
    return Contacts(conn, config)


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def test_helpers():
    assert iban_valid("NL91ABNA0417164300") and iban_valid("nl91 abna 0417 1643 00")
    assert not iban_valid("NL91ABNA0417164301") and not iban_valid("54197594")
    assert iban_valid("GB29NWBK60161331926819")
    assert bic_valid("ABNANL2A") and bic_valid("RABONL2UXXX") and not bic_valid("16-57-10")
    assert clean_name("  Jansen  &  Zn-- ") == "Jansen & Zn"
    assert clean_name("A--B-") == "A-B"


def test_get_customer(ct):
    r = ct.get("customer", 902)
    assert (r.number, r.name, r.city, r.address2) == ("K002", "Bakkerij Cees", "Amersfoort", "unit 2")
    assert (r.firstname, r.lastname, r.salutation, r.contactid) == ("Cees", "Bakker", "Dhr.", 10)  # eerste contact
    assert r.taxaccounts == ["1810", "1811"] and r.terms == 30 and r.in_use
    assert r.typeofcontact == "company" and r.gender == "M"
    v = ct.get("vendor", 905)
    assert v.taxaccounts == ["1800"] and v.taxnumber == "NL905B01"
    with pytest.raises(NotFoundError):
        ct.get("customer", 901)


@pytest.mark.parametrize("vc,vc_id", [("customer", 900), ("customer", 902), ("customer", 903),
                                       ("customer", 904), ("vendor", 901), ("vendor", 905)])
def test_unchanged_save_changes_nothing(conn, ct, vc_id, vc):
    before = dump(conn)
    assert ct.save(ct.get(vc, vc_id)) == vc_id
    assert dump(conn) == before


def test_new_customer(conn, ct):
    r = ct.new("customer")
    assert r.startdate == date.today() and r.curr == "EUR" and r.typeofcontact == "company"
    r.name, r.address1, r.city, r.country = "  Nieuwe  Klant ", "Laan 1", "Delft", "NL"
    r.firstname, r.lastname, r.email, r.phone = "Ans", "Smit", "ans@example.test", "015-1234"
    r.taxaccounts, r.terms, r.discount, r.creditlimit = ["1810"], 14, Decimal("2.5"), Decimal("1000")
    r.arap_accno, r.payment_accno = "1200", "1100"
    r.iban, r.bic, r.bankname = "nl91 abna 0417 1643 00", "abnanl2a", "ABN Amro"
    r.references = [Reference(description="Contract", code="C1")]
    vid = ct.save(r)
    row = one(conn, "SELECT * FROM customer WHERE id = %s", vid)
    assert row[1] == "Nieuwe Klant"            # opgeschoond
    c = ct.get("customer", vid)
    assert c.number == "1"                     # geen customernumber in defaults -> 1
    assert (c.city, c.firstname, c.email, c.phone, c.terms) == ("Delft", "Ans", "ans@example.test", "015-1234", 14)
    assert c.discount == Decimal("2.5") and c.creditlimit == 1000 and c.taxaccounts == ["1810"]
    assert (c.arap_accno, c.payment_accno) == ("1200", "1100")
    assert (c.iban, c.bic, c.bankname) == ("NL91ABNA0417164300", "ABNANL2A", "ABN Amro")   # geen hoofdletters
    assert c.references[0].description == "Contract" and not c.in_use
    contact = one(conn, "SELECT contact, email, phone FROM customer WHERE id = %s", vid)
    assert tuple(contact) == ("Ans Smit", "ans@example.test", "015-1234")
    assert one(conn, "SELECT fldvalue FROM defaults WHERE fldname = 'customernumber'")[0] == "1"
    # opnieuw ongewijzigd opslaan: niets verandert
    before = dump(conn)
    ct.save(c)
    assert dump(conn) == before


def test_number_template_and_peek(conn, ct):
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('vendornumber', 'L<%name 1%>-0041')")
    r = ct.new("vendor")
    r.name = "zuivel bv"
    assert ct.next_number("vendor", r, peek=True) == "LZ-0042"
    assert one(conn, "SELECT fldvalue FROM defaults WHERE fldname = 'vendornumber'")[0] == "L<%name 1%>-0041"
    r.number = "LZ-0042"
    vid = ct.save(r, reserved_number="LZ-0042")       # nu pas opgehoogd
    assert ct.get("vendor", vid).number == "LZ-0042"
    assert one(conn, "SELECT fldvalue FROM defaults WHERE fldname = 'vendornumber'")[0] == "L<%name 1%>-0042"
    r2 = ct.new("vendor")
    r2.name = "Ander"
    assert ct.get("vendor", ct.save(r2)).number == "LA-0043"


def test_duplicate_number(conn, ct):
    r = ct.new("customer")
    r.name, r.number = "Dubbel", "K001"
    with pytest.raises(ValidationError, match="K001 bestaat al"):
        ct.save(r)
    # bestaande dubbelen blijven: wijzigen van iets anders mag
    conn.execute("UPDATE customer SET customernumber = 'K001' WHERE id = 903")
    c = ct.get("customer", 903)
    c.notes = "nieuwe notitie"
    ct.save(c)
    c.number = "K002"
    with pytest.raises(ValidationError):
        ct.save(c)


def test_lock_number(conn, ct):
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('lock_customernumber', '1')")
    c = ct.get("customer", 900)
    c.number = "K900"
    with pytest.raises(ValidationError, match="vergrendeld"):
        ct.save(c)


def test_validation(conn, ct):
    c = ct.get("customer", 900)
    c.name = "x" * 65
    with pytest.raises(ValidationError, match="Naam is te lang"):
        ct.save(c)
    c = ct.get("customer", 900)
    c.iban = "NL91ABNA0417164301"
    with pytest.raises(ValidationError, match="IBAN"):
        ct.save(c)
    c = ct.get("customer", 900)
    c.bic = "16-57-10"
    with pytest.raises(ValidationError, match="BIC"):
        ct.save(c)
    c = ct.get("customer", 900)
    c.name = "   "
    with pytest.raises(ValidationError, match="naam"):
        ct.save(c)
    c = ct.get("customer", 900)
    c.discount = Decimal(120)
    with pytest.raises(ValidationError):
        ct.save(c)
    # een bestaande ongeldige IBAN (oude data) blokkeert opslaan van iets anders niet
    conn.execute("INSERT INTO bank (id, name, iban, bic, dcn) VALUES (900, 'oud', '54197594', '16-57-10', 'X')")
    c = ct.get("customer", 900)
    c.notes = "bijgewerkt"
    ct.save(c)
    assert one(conn, "SELECT iban, bic FROM bank WHERE id = 900")[:2] == ("54197594", "16-57-10")


def test_bank_keeps_dcn_rvc_and_address(conn, ct):
    conn.execute("INSERT INTO bank (id, name, iban, dcn, rvc) VALUES (905, 'Bank', 'NL91ABNA0417164300', 'D1', 'R1')")
    v = ct.get("vendor", 905)
    v.iban, v.bankcity = "GB29NWBK60161331926819", "London"
    ct.save(v)
    b = one(conn, "SELECT name, iban, dcn, rvc, address_id FROM bank WHERE id = 905")
    assert tuple(b[:4]) == ("Bank", "GB29NWBK60161331926819", "D1", "R1")
    assert one(conn, "SELECT city, trans_id FROM address WHERE id = %s", b[4]) == ("London", b[4])
    # alles leegmaken: rij blijft (dcn/rvc), bankadres weg
    v = ct.get("vendor", 905)
    v.bankname = v.iban = v.bankcity = ""
    ct.save(v)
    assert one(conn, "SELECT name, iban, dcn FROM bank WHERE id = 905") == (None, None, "D1")
    assert one(conn, "SELECT count(*) FROM address WHERE id = %s", b[4])[0] == 0


def test_contact_columns_preserved(conn, ct):
    conn.execute("UPDATE contact SET parent_id = 77 WHERE id = 10")
    c = ct.get("customer", 902)
    c.lastname, c.phone, c.email = "de Bakker", "06-1", "c@b.test"
    ct.save(c)
    assert one(conn, "SELECT lastname, parent_id, phone FROM contact WHERE id = 10") == ("de Bakker", 77, "06-1")
    assert one(conn, "SELECT lastname FROM contact WHERE id = 11")[0] == "Bakker"      # tweede contact blijft
    assert one(conn, "SELECT contact, phone, email FROM customer WHERE id = 902") == ("Cees de Bakker", "06-1",
                                                                                     "c@b.test")


def test_person_name(conn, ct):
    r = ct.new("customer")
    r.typeofcontact, r.firstname, r.lastname = "person", "Jan", "Vries"
    vid = ct.save(r)
    assert ct.get("customer", vid).name == "Vries Jan"
    r = ct.get("customer", vid)
    r.lastname = "Visser"
    ct.save(r)
    assert ct.get("customer", vid).name == "Visser Jan"
    assert one(conn, "SELECT typeofcontact FROM contact WHERE trans_id = %s", vid)[0] == "person"


def test_shipto(conn, ct):
    c = ct.get("customer", 900)
    c.shiptoname, c.shiptoaddress1 = "Klant A", "Dorpsstraat 1"        # gelijk aan factuuradres: niet bewaard
    ct.save(c)
    assert one(conn, "SELECT count(*) FROM shipto WHERE trans_id = 900")[0] == 0
    c.shiptoaddress1, c.shiptocity = "Magazijnweg 5", "Nieuwegein"
    ct.save(c)
    conn.execute("UPDATE shipto SET shiptorecurring = true WHERE trans_id = 900")
    c = ct.get("customer", 900)
    assert c.shiptocity == "Nieuwegein"
    c.shiptozipcode = "3430AA"
    ct.save(c)
    assert one(conn, "SELECT shiptozipcode, shiptorecurring FROM shipto WHERE trans_id = 900") == ("3430AA", True)


def test_delete(conn, ct):
    with pytest.raises(ValidationError, match="inactief"):
        ct.delete("customer", 900)
    r = ct.new("vendor")
    r.name, r.iban, r.bankcity, r.shiptocity = "Weg", "NL91ABNA0417164300", "Utrecht", "Elders"
    r.taxaccounts = ["1800"]
    r.references = [Reference(description="doc")]
    vid = ct.save(r)
    bank_addr = one(conn, "SELECT address_id FROM bank WHERE id = %s", vid)[0]
    ct.delete("vendor", vid)
    for sql in ("SELECT count(*) FROM vendor WHERE id = %s", "SELECT count(*) FROM address WHERE trans_id = %s",
                "SELECT count(*) FROM contact WHERE trans_id = %s", "SELECT count(*) FROM bank WHERE id = %s",
                "SELECT count(*) FROM vendortax WHERE vendor_id = %s", "SELECT count(*) FROM shipto WHERE trans_id = %s",
                "SELECT count(*) FROM reference WHERE trans_id = %s"):
        assert one(conn, sql, vid)[0] == 0, sql
    assert one(conn, "SELECT count(*) FROM address WHERE id = %s", bank_addr)[0] == 0
    a = one(conn, "SELECT tablename, reference, action FROM audittrail WHERE trans_id = %s", vid)
    assert tuple(a) == ("vendor", "Weg", "deleted")


def test_delete_many_all_or_nothing(conn, ct):
    ids = []
    for n in ("A1", "A2"):
        r = ct.new("customer")
        r.name = n
        ids.append(ct.save(r))
    with pytest.raises(ValidationError):
        ct.delete_many("customer", ids + [900])
    assert one(conn, "SELECT count(*) FROM customer WHERE id = ANY(%s)", ids)[0] == 2
    assert ct.delete_many("customer", ids) == ["A1", "A2"]
    assert one(conn, "SELECT count(*) FROM customer WHERE id = ANY(%s)", ids)[0] == 0


def test_inactive_and_search(conn, ct):
    names = [r["name"] for r in ct.search("customer")]
    assert names == ["Bakkerij Cees", "Kassaklant", "Klant A", "Overseas Inc"]   # 902 maar één keer (2 contacten)
    ct.set_inactive("customer", 904, date.today() - timedelta(days=1))
    conn.execute("UPDATE customer SET enddate = %s WHERE id = 903", (date.today() + timedelta(days=30),))
    assert [r["id"] for r in ct.search("customer", ContactSearch(status="inactive"))] == [904]
    assert 903 in [r["id"] for r in ct.search("customer", ContactSearch(status="active"))]
    r = ct.new("customer")
    r.name = "Los"
    vid = ct.save(r)
    conn.execute("DELETE FROM address WHERE trans_id = %s", (vid,))
    conn.execute("DELETE FROM contact WHERE trans_id = %s", (vid,))
    rows = ct.search("customer", ContactSearch(status="orphaned"))
    assert [x["id"] for x in rows] == [vid]                 # zichtbaar zonder adres/contact
    assert [x["id"] for x in ct.search("customer", ContactSearch(city="utrecht"))] == [904, 900]   # op naam
    assert [x["id"] for x in ct.search("customer", ContactSearch(contact="cees"))] == [902]
    assert [x["id"] for x in ct.search("customer", ContactSearch(number="k00", sort="number",
                                                                 direction="DESC"))] == [904, 903, 902, 900]
    assert [x["id"] for x in ct.search("customer", ContactSearch(country="US"))] == [903]
    row = [x for x in ct.search("customer") if x["id"] == 900][0]
    open_ = one(conn, "SELECT sum(amount::numeric - paid::numeric) FROM ar WHERE customer_id = 900 AND amount <> paid")[0]
    assert row["outstanding"] == (open_ or 0) and row["availablecredit"] == -row["outstanding"]
    assert row["taxaccounts"] == "1810 1811"
    assert ct.countries("customer") == ["NL", "US"]
    assert ct.in_use("customer", 900) and not ct.in_use("customer", vid)


def test_choices(conn, ct):
    ch = ct.choices("customer")
    assert [a["accno"] for a in ch["arap"]] == ["1200"] and [a["accno"] for a in ch["payment"]] == ["1100"]
    assert [a["accno"] for a in ch["tax"]] == ["1800", "1801", "1810", "1811"]
    assert ch["currencies"][0] == "EUR" and not ch["lock_number"]
