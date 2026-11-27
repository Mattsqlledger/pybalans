"""Artikelen en diensten (sqlledger.parts, port van IC.pm)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import NotFoundError, Part, Parts, PartSearch, ValidationError
from sqlledger.parts import kind_of

TABLES = ("parts", "partstax", "partsvendor", "partscustomer", "makemodel", "translation", "defaults",
          "audittrail")

#: testdata heeft geen IC_income-rekening: 8520 ook als opbrengst voor diensten
SETUP = ["UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'",
         "SELECT setval('invoiceid', (SELECT max(id) FROM invoice))"]


def dump(conn):
    return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in TABLES}


@pytest.fixture
def ic(conn, config):
    for sql in SETUP:
        conn.execute(sql)
    return Parts(conn, config)


def part(**kw):
    p = Part(kind="part", partnumber="A-1", description="Appeltaart", unit="st", sellprice=Decimal("12.5"),
             listprice=Decimal("14"), lastcost=Decimal("6.125"), priceupdate=date(2026, 1, 5),
             inventory_accno="7001", income_accno="8520", expense_accno="8020", taxaccounts=["1810"])
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_kind_of():
    assert kind_of({"inventory_accno_id": 1, "income_accno_id": 2, "expense_accno_id": 3}) == "part"
    assert kind_of({"income_accno_id": 2, "expense_accno_id": 3}) == "service"
    assert kind_of({"inventory_accno_id": 1, "expense_accno_id": 3}) == "labor"
    assert kind_of({"inventory_accno_id": 1, "income_accno_id": 2, "assembly": True}) == "assembly"
    assert kind_of({}) == "kit"


def test_links(ic):
    l = ic.links("part")
    assert [a["accno"] for a in l["inventory"]][:3] == ["3010", "7001", "7002"]
    assert "8520" in [a["accno"] for a in l["income"]]
    assert "8020" in [a["accno"] for a in l["expense"]]
    assert [a["accno"] for a in l["tax"]] == ["1800", "1801", "1810", "1811"]
    s = ic.links("service")
    assert s["inventory"] == [] and [a["accno"] for a in s["income"]] == ["8520"]
    assert {"8010", "8100"} <= {a["accno"] for a in s["expense"]}


def test_new_has_all_taxes_checked(ic):
    p = ic.new("service")
    assert p.taxaccounts == ["1800", "1801", "1810", "1811"]
    assert p.priceupdate == date.today()
    with pytest.raises(ValidationError):
        ic.new("kit")


def test_save_and_get(ic, conn):
    pid = ic.save(part())
    p = ic.get(pid)
    assert (p.kind, p.partnumber, p.description, p.unit) == ("part", "A-1", "Appeltaart", "st")
    assert (p.sellprice, p.listprice, p.lastcost) == (Decimal("12.5"), Decimal("14"), Decimal("6.125"))
    assert (p.inventory_accno, p.income_accno, p.expense_accno) == ("7001", "8520", "8020")
    assert p.taxaccounts == ["1810"]
    assert p.onhand == 0 and not p.in_use
    row = conn.execute("SELECT partsgroup_id, onhand FROM parts WHERE id = %s", (pid,)).fetchone()
    assert row == (0, 0)          # zoals Perl: geen groep = 0


def test_empty_number_gets_next(ic, conn):
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('partnumber', 'ART-0009')")
    assert ic.next_number(peek=True) == "ART-0010"
    pid = ic.save(part(partnumber=""))
    assert ic.get(pid).partnumber == "ART-0010"
    assert ic.next_number(peek=True) == "ART-0011"


def test_unchanged_save_writes_nothing(ic, conn):
    pid = ic.save(part())
    conn.execute("UPDATE parts SET image = 'foto.jpg', tariff_hscode = '1905', onhand = 7 WHERE id = %s", (pid,))
    conn.execute("INSERT INTO makemodel (parts_id, make, model) VALUES (%s, 'Merk', 'M1')", (pid,))
    before = dump(conn)
    assert ic.save(ic.get(pid)) == pid
    assert dump(conn) == before


def test_change_keeps_unknown_fields(ic, conn):
    pid = ic.save(part())
    conn.execute("UPDATE parts SET image = 'foto.jpg', onhand = 7 WHERE id = %s", (pid,))
    conn.execute("INSERT INTO partsvendor (vendor_id, parts_id, lastcost) VALUES (1001, %s, 5)", (pid,))
    p = ic.get(pid)
    p.sellprice, p.taxaccounts, p.notes = Decimal("13"), ["1810", "1811"], "lekker"
    ic.save(p)
    row = conn.execute("SELECT image, onhand, sellprice, notes FROM parts WHERE id = %s", (pid,)).fetchone()
    assert row == ("foto.jpg", 7, 13, "lekker")
    assert conn.execute("SELECT count(*) FROM partsvendor WHERE parts_id = %s", (pid,)).fetchone()[0] == 1
    assert ic.get(pid).taxaccounts == ["1810", "1811"]


def test_onhand_not_changed_by_save(ic, conn):
    pid = ic.save(part())
    p = ic.get(pid)
    p.onhand = Decimal(99)
    ic.save(p)
    assert ic.get(pid).onhand == 0


def test_validation(ic):
    with pytest.raises(ValidationError, match="omschrijving"):
        ic.save(part(description=" "))
    with pytest.raises(ValidationError, match="eenheid"):
        ic.save(part(unit="zakjes"))
    with pytest.raises(ValidationError, match="decimalen"):
        ic.save(part(sellprice=Decimal("1.1234567")))
    with pytest.raises(ValidationError, match="negatief"):
        ic.save(part(sellprice=Decimal("-1")))
    with pytest.raises(ValidationError, match="gekoppeld"):
        ic.save(part(income_accno="8020"))
    with pytest.raises(ValidationError, match="voorraadrekening"):
        ic.save(part(inventory_accno=""))
    with pytest.raises(ValidationError, match="geen voorraadrekening"):
        ic.save(part(kind="service", income_accno="8520", expense_accno="8100"))
    with pytest.raises(ValidationError, match="btw-rekening"):
        ic.save(part(taxaccounts=["8520"]))
    with pytest.raises(ValidationError):
        ic.save(part(kind="labor"))


def test_service(ic):
    pid = ic.save(Part(kind="service", partnumber="D-1", description="Advies", income_accno="8520",
                       expense_accno="8100", taxaccounts=["1810"]))
    p = ic.get(pid)
    assert p.kind == "service" and p.inventory_accno == ""


def test_duplicates_allowed_with_warning(ic):
    a = ic.save(part())
    b = ic.save(part(partnumber="a-1"))
    assert a != b
    assert [r["id"] for r in ic.duplicates("A-1", exclude_id=b)] == [a]
    assert ic.duplicates("") == []


def test_used_part_accounts_locked(ic, conn):
    pid = ic.save(part())
    conn.execute("INSERT INTO invoice (trans_id, parts_id, description, qty) VALUES (950, %s, 'x', 1)", (pid,))
    p = ic.get(pid)
    assert p.in_use
    p.income_accno = "8530"
    with pytest.raises(ValidationError, match="gebruikt artikel"):
        ic.save(p)
    p = ic.get(pid)
    p.obsolete, p.sellprice = True, Decimal(20)
    ic.save(p)
    assert ic.get(pid).obsolete


def test_delete(ic, conn):
    pid = ic.save(part())
    conn.execute("INSERT INTO translation (trans_id, language_code, description) VALUES (%s, 'en', 'Apple pie')",
                 (pid,))
    ic.delete(pid)
    with pytest.raises(NotFoundError):
        ic.get(pid)
    assert conn.execute("SELECT count(*) FROM partstax WHERE parts_id = %s", (pid,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM translation WHERE trans_id = %s", (pid,)).fetchone()[0] == 0


def test_delete_used_refused(ic, conn):
    pid = ic.save(part())
    conn.execute("INSERT INTO orderitems (trans_id, parts_id, qty) VALUES (1, %s, 1)", (pid,))
    with pytest.raises(ValidationError, match="vervallen"):
        ic.delete(pid)


def test_existing_kit_read_only(ic, conn):
    pid = ic.save(part())
    conn.execute("""UPDATE parts SET inventory_accno_id = NULL, income_accno_id = NULL, expense_accno_id = NULL
                    WHERE id = %s""", (pid,))
    p = ic.get(pid)
    assert p.kind == "kit" and not p.editable
    with pytest.raises(ValidationError):
        ic.save(p)


def test_groups(ic, conn):
    g = ic.save_group("Gebak", "GB")
    with pytest.raises(ValidationError, match="bestaat al"):
        ic.save_group("gebak")
    pid = ic.save(part(partsgroup_id=g))
    assert ic.get(pid).partsgroup == "Gebak"
    assert [r["used"] for r in ic.groups() if r["id"] == g] == [1]
    with pytest.raises(ValidationError, match="in gebruik"):
        ic.delete_group(g)
    ic.save_group("Taart", group_id=g)
    assert ic.get(pid).partsgroup == "Taart"


def test_stock_adjustment(ic, conn):
    pid = ic.save(part())
    r = ic.adjust_stock(pid, transdate=date(2026, 3, 1), reason="telling", newonhand="10")
    assert (r["oldonhand"], r["qty"], r["newonhand"]) == (0, 10, 10)
    r = ic.adjust_stock(pid, transdate=date(2026, 3, 2), reason="breuk", qty="-2.5")
    assert r["newonhand"] == Decimal("7.5")
    assert ic.get(pid).onhand == Decimal("7.5")
    rows = ic.stock_adjustments(pid)
    assert [(x["reason"], x["qty"]) for x in rows] == [("telling", 10), ("breuk", Decimal("-2.5"))]
    with pytest.raises(ValidationError, match="reden"):
        ic.adjust_stock(pid, transdate=date(2026, 3, 2), reason="", qty=1)
    with pytest.raises(ValidationError, match="niet gewijzigd"):
        ic.adjust_stock(pid, transdate=date(2026, 3, 2), reason="x", newonhand="7.5")
    svc = ic.save(Part(kind="service", description="Advies", income_accno="8520", expense_accno="8100"))
    with pytest.raises(ValidationError, match="voorraad"):
        ic.adjust_stock(svc, transdate=date(2026, 3, 2), reason="x", qty=1)


def test_search(ic, conn):
    a = ic.save(part())
    b = ic.save(part(partnumber="B-1", description="Bolus", obsolete=True))
    s = ic.save(Part(kind="service", partnumber="D-1", description="Advies", income_accno="8520",
                     expense_accno="8100"))
    conn.execute("UPDATE parts SET onhand = 3, rop = 5 WHERE id = %s", (a,))
    ids = lambda **kw: [r["id"] for r in ic.search(PartSearch(**kw)) if r["id"] in (a, b, s)]  # noqa: E731
    assert ids(status="all") == [a, b, s]
    assert ids() == [a, s]                          # standaard alleen actief
    assert ids(status="obsolete") == [b]
    assert ids(status="all", kind="service") == [s]
    assert ids(status="short") == [a]
    assert ids(status="onhand") == [a]
    assert ids(status="orphaned") == [b, s]
    assert ids(status="all", description="bol") == [b]
    assert ids(status="all", sort="description") == [s, a, b]
    r = [x for x in ic.search(PartSearch(status="all")) if x["id"] == a][0]
    assert r["kind"] == "part" and r["tax"] == "1810" and r["onhand"] == 3


def test_search_transactions_and_history(ic, conn):
    pid = ic.save(part())
    conn.execute("""INSERT INTO invoice (trans_id, parts_id, description, qty, sellprice, discount)
                    VALUES (950, %s, 'Appeltaart groot', 2, 12.5, 0)""", (pid,))
    ar = conn.execute("SELECT invnumber, amount = paid FROM ar WHERE id = 950").fetchone()
    mine = lambda **kw: [r for r in ic.search(PartSearch(status="all", partnumber="A-1", **kw))]  # noqa: E731
    assert [(r["id"], r["number"], r["qty"]) for r in mine(transactions=("sold",))] == [(pid, ar[0], 2)]
    assert len(mine(transactions=("sold",), open=False)) == (1 if ar[1] else 0)
    assert mine(transactions=("sold",), open=False, closed=False) == []
    assert mine(transactions=("bought",)) == []
    h = ic.history(pid, "sales")
    assert [(x["number"], x["sellprice"]) for x in h] == [(ar[0], Decimal("12.5"))]
    assert ic.history(pid, "purchases") == []


def set_closedto(conn, value):
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', %s) ON CONFLICT DO NOTHING", (value,))
    conn.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = 'closedto'", (value,))


def test_stock_adjustment_closed_period(ic, conn):
    """F41: geen voorraadcorrectie vóór of op de afsluitdatum (zoals tijdkaarten)."""
    from sqlledger.errors import ClosedPeriodError
    pid = ic.save(part())
    set_closedto(conn, "2026-03-31")
    for d in (date(2026, 3, 31), date(2025, 12, 1)):
        with pytest.raises(ClosedPeriodError, match="afgesloten periode"):
            ic.adjust_stock(pid, transdate=d, reason="telling", newonhand="10")
    assert ic.get(pid).onhand == 0 and ic.stock_adjustments(pid) == []
    assert ic.adjust_stock(pid, transdate=date(2026, 4, 1), reason="telling", newonhand="10")["newonhand"] == 10


def test_history_one_basis(ic, conn):
    """F39: prijshistorie en "Regels uit" tonen de ingevoerde prijs vóór korting in de standaardvaluta: factuur
    ``fxsellprice`` × koers (oude SL-regel zonder fxsellprice: ``sellprice``), order ``sellprice`` × koers."""
    pid = ic.save(part())
    conn.execute("""INSERT INTO ar (id, invnumber, transdate, customer_id, curr, exchangerate, amount, netamount, paid)
                    VALUES (960, 'F-USD', '2026-05-01', 900, 'USD', 0.9, 81, 81, 0)""")
    conn.execute("""INSERT INTO invoice (trans_id, parts_id, description, qty, sellprice, fxsellprice, discount)
                    VALUES (960, %s, 'Taart', 1, 81, 100, 0.1),
                           (950, %s, 'Taart oud', 1, 12.5, NULL, 0)""", (pid, pid))
    conn.execute("""INSERT INTO oe (id, ordnumber, transdate, customer_id, curr, exchangerate, quotation, amount,
                                    netamount, closed)
                    VALUES (970, 'O-EUR', '2026-05-02', 900, 'EUR', 1, false, 90, 90, false),
                           (971, 'O-USD', '2026-05-03', 900, 'USD', 0.9, false, 81, 81, false)""")
    conn.execute("""INSERT INTO orderitems (trans_id, parts_id, description, qty, sellprice, discount)
                    VALUES (970, %s, 'Taart', 1, 100, 0.1), (971, %s, 'Taart', 1, 100, 0.1)""", (pid, pid))
    got = {x["number"]: x["sellprice"] for x in ic.history(pid, "sales")}
    assert got["F-USD"] == Decimal("90.00") and got["O-EUR"] == Decimal("100.00") and got["O-USD"] == Decimal("90.00")
    assert [x["discount"] for x in ic.history(pid, "sales") if x["number"] == "F-USD"] == [Decimal("0.1")]
    lines = ic.search(PartSearch(status="all", partnumber="A-1", transactions=("sold", "ordered")))
    assert {x["number"]: x["sellprice"] for x in lines} == {**got}
