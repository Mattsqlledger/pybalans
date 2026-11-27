"""Samengestelde artikelen: stuklijst, prijzen, assembleren/demonteren, lijsten (fase 3, SA1–SA12)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import Part, Parts, ValidationError
from sqlledger.assembly import Assemblies, Component, InsufficientStock

from test_parts import SETUP, dump

D = Decimal


@pytest.fixture
def ic(conn, config):
    for sql in SETUP:
        conn.execute(sql)
    return Parts(conn, config)


@pytest.fixture
def asm(conn, config, ic):
    return Assemblies(conn, config)


def mk_part(ic, number, sell, cost, list_=None, weight=0, **kw):
    p = Part(kind="part", partnumber=number, description=f"Onderdeel {number}", unit="st", sellprice=D(sell),
             listprice=D(list_ if list_ is not None else sell), lastcost=D(cost), weight=D(weight),
             inventory_accno="7001", income_accno="8520", expense_accno="8020", taxaccounts=["1810"])
    for k, v in kw.items():
        setattr(p, k, v)
    return ic.save(p)


def mk_service(ic, number, sell):
    return ic.save(Part(kind="service", partnumber=number, description=f"Dienst {number}", sellprice=D(sell),
                        income_accno="8520", expense_accno="8100", taxaccounts=["1810"]))


def mk_asm(ic, number, comps, **kw):
    p = Part(kind="assembly", partnumber=number, description=f"Product {number}", unit="st",
             income_accno="8520", taxaccounts=["1810"],
             components=[Component(parts_id=c, qty=D(q)) for c, q in comps])
    for k, v in kw.items():
        setattr(p, k, v)
    return ic.save(p)


def onhand(conn, pid):
    return D(str(conn.execute("SELECT onhand FROM parts WHERE id = %s", (pid,)).fetchone()[0]))


def set_onhand(conn, pid, n):
    conn.execute("UPDATE parts SET onhand = %s WHERE id = %s", (n, pid))


def test_save_assembly_and_prices(ic, conn):
    a = mk_part(ic, "O-1", "2.50", "1.10", list_="3", weight="0.5")
    b = mk_part(ic, "O-2", "1.25", "0.40", weight="0.1")
    s = mk_service(ic, "D-1", "10")
    pid = mk_asm(ic, "P-1", [(a, 2), (b, "1.5"), (s, 1)])
    p = ic.get(pid)
    assert p.kind == "assembly" and not p.own_sellprice
    assert [(c.partnumber, c.qty, c.kind) for c in p.components] == [("O-1", 2, "part"), ("O-2", D("1.5"), "part"),
                                                                     ("D-1", 1, "service")]
    # per regel afgerond (ic.pl): verkoop 5 + 1.88 + 10, lijst 6 + 1.88 + 0, kostprijs 2.2 + 0.6 + 0
    assert (p.sellprice, p.listprice, p.lastcost, p.weight) == (D("16.88"), D("7.88"), D("2.8"), D("1.15"))
    row = conn.execute("""SELECT assembly, inventory_accno_id, expense_accno_id, sellprice FROM parts
                          WHERE id = %s""", (pid,)).fetchone()
    assert row == (True, None, None, 16.88)
    rows = conn.execute("SELECT parts_id, qty, bom, adj FROM assembly WHERE aid = %s ORDER BY id", (pid,)).fetchall()
    assert rows == [(a, 2, False, True), (b, 1.5, False, True), (s, 1, False, True)]


def test_assembly_accounts(ic):
    a = mk_part(ic, "O-1", "1", "1")
    with pytest.raises(ValidationError, match="geen voorraadrekening"):
        mk_asm(ic, "P-1", [(a, 1)], inventory_accno="7001")
    with pytest.raises(ValidationError, match="opbrengstrekening"):
        mk_asm(ic, "P-1", [(a, 1)], income_accno="")
    assert ic.links("assembly")["inventory"] == [] and ic.links("assembly")["expense"] == []
    assert ic.new("assembly").taxaccounts == ["1800", "1801", "1810", "1811"]


def test_component_price_change_flows_up(ic, conn):
    a = mk_part(ic, "O-1", "2", "1")
    sub = mk_asm(ic, "S-1", [(a, 3)])                 # 6 / 3
    top = mk_asm(ic, "T-1", [(sub, 2), (a, 1)])       # 2*6 + 2 = 14 / 2*3 + 1 = 7
    assert (ic.get(top).sellprice, ic.get(top).lastcost) == (14, 7)
    p = ic.get(a)
    p.sellprice, p.lastcost = D("2.5"), D("1.2")
    ic.save(p)
    assert (ic.get(sub).sellprice, ic.get(sub).lastcost) == (D("7.5"), D("3.6"))
    assert (ic.get(top).sellprice, ic.get(top).lastcost) == (D("17.5"), D("8.4"))
    row = conn.execute("SELECT sellprice, lastcost FROM parts WHERE id = %s", (top,)).fetchone()
    assert row == (17.5, 8.4)                          # ook opgeslagen (Perl-fout IC.pm 281 opgelost)


def test_own_sellprice(ic, conn):
    a = mk_part(ic, "O-1", "2", "1")
    sub = mk_asm(ic, "S-1", [(a, 3)], own_sellprice=True, sellprice=D("5.95"))
    top = mk_asm(ic, "T-1", [(sub, 2)])
    assert ic.get(sub).sellprice == D("5.95") and ic.get(sub).own_sellprice
    assert ic.get(sub).computed["sellprice"] == 6
    assert ic.get(top).sellprice == D("11.9")         # gebruikt de eigen prijs van het sub-product
    p = ic.get(a)
    p.sellprice = D("3")
    ic.save(p)
    assert ic.get(sub).sellprice == D("5.95")         # eigen prijs blijft
    p = ic.get(sub)
    p.own_sellprice = False
    ic.save(p)
    assert ic.get(sub).sellprice == 9 and ic.get(top).sellprice == 18


def test_bom_rules(ic, conn):
    a = mk_part(ic, "O-1", "1", "1")
    b = mk_part(ic, "O-2", "1", "1")
    with pytest.raises(ValidationError, match="minstens één"):
        mk_asm(ic, "P-1", [])
    with pytest.raises(ValidationError, match="twee keer"):
        mk_asm(ic, "P-1", [(a, 1), (a, 2)])
    with pytest.raises(ValidationError, match="groter dan 0"):
        mk_asm(ic, "P-1", [(a, 0)])
    with pytest.raises(ValidationError, match="decimalen"):
        mk_asm(ic, "P-1", [(a, "0.1234567")])
    p = ic.get(b)
    p.obsolete = True
    ic.save(p)
    with pytest.raises(ValidationError, match="vervallen"):
        mk_asm(ic, "P-1", [(b, 1)])
    conn.execute("INSERT INTO parts (id, partnumber, description) VALUES (5000, 'K-1', 'kit')")
    with pytest.raises(ValidationError, match="kit"):
        mk_asm(ic, "P-1", [(5000, 1)])


def test_cycle_and_depth(ic):
    a = mk_part(ic, "O-1", "1", "1")
    p1 = mk_asm(ic, "L1", [(a, 1)])
    p2 = mk_asm(ic, "L2", [(p1, 1)])
    p = ic.get(p1)
    p.components = [Component(parts_id=p2, qty=D(1))]
    with pytest.raises(ValidationError, match="kring"):
        ic.save(p)
    p.components = [Component(parts_id=p1, qty=D(1))]
    with pytest.raises(ValidationError, match="zichzelf"):
        ic.save(p)
    p3 = mk_asm(ic, "L3", [(p2, 1)])
    p4 = mk_asm(ic, "L4", [(p3, 1)])
    p5 = mk_asm(ic, "L5", [(p4, 1)])                  # 5 niveaus: mag
    with pytest.raises(ValidationError, match="5 niveaus"):
        mk_asm(ic, "L6", [(p5, 1)])
    # een niveau onder L1 erbij maakt L5 te diep
    s = mk_asm(ic, "S", [(a, 1)])
    p = ic.get(p1)
    p.components = [Component(parts_id=s, qty=D(1))]
    with pytest.raises(ValidationError, match="5 niveaus"):
        ic.save(p)


def test_unchanged_assembly_writes_nothing(ic, conn):
    a = mk_part(ic, "O-1", "2.5", "1.1")
    pid = mk_asm(ic, "P-1", [(a, "0.333333")], own_sellprice=True, sellprice=D("1"))
    before = (dump(conn), conn.execute("SELECT * FROM assembly ORDER BY id").fetchall())
    ic.save(ic.get(pid))
    assert (dump(conn), conn.execute("SELECT * FROM assembly ORDER BY id").fetchall()) == before


def test_bom_flag_and_change_rules(ic, conn):
    a = mk_part(ic, "O-1", "1", "1")
    b = mk_part(ic, "O-2", "1", "1")
    pid = mk_asm(ic, "P-1", [(a, 1)])
    first = conn.execute("SELECT id FROM assembly WHERE aid = %s", (pid,)).fetchone()[0]
    p = ic.get(pid)
    p.components[0].bom = True
    ic.save(p)
    assert conn.execute("SELECT id, bom FROM assembly WHERE aid = %s", (pid,)).fetchall() == [(first, True)]
    set_onhand(conn, pid, 2)
    p = ic.get(pid)
    p.components.append(Component(parts_id=b, qty=D(1)))
    with pytest.raises(ValidationError, match="voorraad"):
        ic.save(p)
    p = ic.get(pid)                                     # bom-vinkje mag altijd
    p.components[0].bom = False
    ic.save(p)
    set_onhand(conn, pid, 0)
    p = ic.get(pid)
    p.components.append(Component(parts_id=b, qty=D(2), bom=True))
    ic.save(p)
    assert [(c.partnumber, c.qty, c.bom) for c in ic.get(pid).components] == [("O-1", 1, False), ("O-2", 2, True)]


def test_kind_cannot_change_to_or_from_assembly(ic):
    a = mk_part(ic, "O-1", "1", "1")
    pid = mk_asm(ic, "P-1", [(a, 1)])
    p = ic.get(pid)
    p.kind = "part"
    p.inventory_accno, p.expense_accno = "7001", "8020"
    with pytest.raises(ValidationError, match="samengesteld"):
        ic.save(p)
    p = ic.get(a)
    p.kind = "assembly"
    with pytest.raises(ValidationError, match="samengesteld"):
        ic.save(p)


def test_delete(ic, conn, asm):
    a = mk_part(ic, "O-1", "1", "1")
    pid = mk_asm(ic, "P-1", [(a, 1)])
    with pytest.raises(ValidationError, match="onderdeel van een samengesteld"):
        ic.delete(a)
    assert ic.get(a).in_use
    set_onhand(conn, pid, 1)
    with pytest.raises(ValidationError, match="demonteer"):
        ic.delete(pid)
    set_onhand(conn, pid, 0)
    ic.delete(pid)
    assert conn.execute("SELECT count(*) FROM assembly").fetchone()[0] == 0
    ic.delete(a)
    pid = mk_asm(ic, "P-2", [(mk_part(ic, "O-3", "1", "1"), 1)])
    asm.build(pid, 1, transdate=date(2026, 3, 1), allow_short=True)
    asm.build(pid, -1, transdate=date(2026, 3, 2))
    with pytest.raises(ValidationError, match="productiehistorie"):
        ic.delete(pid)


def test_build_and_disassemble(ic, asm, conn):
    a = mk_part(ic, "O-1", "1", "1")
    b = mk_part(ic, "O-2", "1", "1")
    s = mk_service(ic, "D-1", "5")
    sub = mk_asm(ic, "S-1", [(a, 1)])
    pid = mk_asm(ic, "P-1", [(a, 2), (b, "0.5"), (s, 1), (sub, 1)])
    for x, n in ((a, 10), (b, 3), (sub, 4)):
        set_onhand(conn, x, n)
    entries = conn.execute("SELECT count(*) FROM acc_trans").fetchone()[0]
    r = asm.build(pid, 3, transdate=date(2026, 3, 1), reference="W-1", notes="eerste serie")
    assert r["newonhand"] == 3 and r["shortages"] == []
    assert [onhand(conn, x) for x in (a, b, s, sub, pid)] == [4, D("1.5"), 0, 1, 3]
    hist = asm.builds(pid)
    assert [(h["qty"], h["oldonhand"], h["reference"], h["notes"]) for h in hist] == [(3, 0, "W-1", "eerste serie")]
    head, lines = asm.build_detail(hist[0]["id"])
    assert [(l["partnumber"], l["perassembly"], l["qty"], l["oldonhand"]) for l in lines] == [
        ("O-1", 2, -6, 10), ("O-2", D("0.5"), D("-1.5"), 3), ("S-1", 1, -3, 4)]     # dienst beweegt niet
    with pytest.raises(ValidationError, match="verder dan de voorraad"):
        asm.build(pid, -4, transdate=date(2026, 3, 2))
    asm.build(pid, -2, transdate=date(2026, 3, 2))
    assert [onhand(conn, x) for x in (a, b, sub, pid)] == [8, D("2.5"), 3, 1]
    audit = conn.execute("SELECT action FROM audittrail WHERE trans_id = %s AND formname = 'assembly'"
                         " AND action <> 'saved' ORDER BY transdate", (pid,)).fetchall()
    assert [x[0] for x in audit] in ([], ["stock", "unstock"])
    # geen grootboekboeking
    assert conn.execute("SELECT count(*) FROM acc_trans").fetchone()[0] == entries


def test_build_shortage(ic, asm, conn):
    a = mk_part(ic, "O-1", "1", "1")
    b = mk_part(ic, "O-2", "1", "1")
    pid = mk_asm(ic, "P-1", [(a, 2), (b, 1)])
    set_onhand(conn, a, 3)
    set_onhand(conn, b, 5)
    with pytest.raises(InsufficientStock) as e:
        asm.build(pid, 2, transdate=date(2026, 3, 1))
    assert not e.value.blocking and [s["partnumber"] for s in e.value.shortages] == ["O-1"]
    assert onhand(conn, pid) == 0
    asm.build(pid, 2, transdate=date(2026, 3, 1), allow_short=True)     # waarschuwing bevestigd
    assert (onhand(conn, a), onhand(conn, b), onhand(conn, pid)) == (-1, 3, 2)
    p = ic.get(a)
    p.checkinventory = True
    ic.save(p)
    with pytest.raises(InsufficientStock) as e:
        asm.build(pid, 1, transdate=date(2026, 3, 1), allow_short=True)
    assert e.value.blocking


def test_build_validation(ic, asm, conn):
    a = mk_part(ic, "O-1", "1", "1")
    pid = mk_asm(ic, "P-1", [(a, 1)])
    with pytest.raises(ValidationError, match="aantal"):
        asm.build(pid, 0, transdate=date(2026, 3, 1))
    with pytest.raises(ValidationError, match="geen samengesteld"):
        asm.build(a, 1, transdate=date(2026, 3, 1))
    with pytest.raises(ValidationError, match="decimalen"):
        asm.build(pid, "0.0000001", transdate=date(2026, 3, 1))
    p = ic.get(pid)
    p.obsolete = True
    ic.save(p)
    with pytest.raises(ValidationError, match="vervallen"):
        asm.build(pid, 1, transdate=date(2026, 3, 1), allow_short=True)


def test_can_make(ic, asm, conn):
    a = mk_part(ic, "O-1", "1", "1")
    b = mk_part(ic, "O-2", "1", "1")
    s = mk_service(ic, "D-1", "5")
    p1 = mk_asm(ic, "P-1", [(a, 2), (b, "0.5"), (s, 1)])
    p2 = mk_asm(ic, "P-2", [(a, 3)])
    p3 = mk_asm(ic, "P-3", [(s, 1)])
    set_onhand(conn, a, 7)
    set_onhand(conn, b, -1)
    rows = {r["partnumber"]: r for r in asm.can_make()}
    assert (rows["P-1"]["can_make"], rows["P-1"]["limit"]) == (0, "O-2")
    assert (rows["P-2"]["can_make"], rows["P-2"]["limit"]) == (2, "O-1")
    assert rows["P-3"]["can_make"] is None
    set_onhand(conn, b, 10)
    assert {r["partnumber"]: r["can_make"] for r in asm.can_make(partnumber="P-1")} == {"P-1": 3}


def test_explode_where_used_bom_list(ic, asm, conn):
    a = mk_part(ic, "O-1", "2", "1")
    b = mk_part(ic, "O-2", "1", "0.5")
    sub = mk_asm(ic, "S-1", [(a, 3)])
    top = mk_asm(ic, "T-1", [(sub, 2), (b, 4)])
    rows = asm.explode(top, 5)
    assert [(r["level"], r["partnumber"], r["perassembly"], r["qty"], r["lastcost"], r["linecost"]) for r in rows] == [
        (1, "S-1", 2, 10, 3, 30), (2, "O-1", 3, 30, 1, 30), (1, "O-2", 4, 20, D("0.5"), 10)]
    used = asm.where_used(a)
    assert [(r["level"], r["partnumber"], r["perassembly"], r["qty"], r["via"]) for r in used] == [
        (1, "S-1", 3, 3, None), (2, "T-1", 2, 6, "S-1")]
    lst = asm.bom_list()
    assert [(r["level"], r["partnumber"]) for r in lst] == [(0, "S-1"), (1, "O-1"), (0, "T-1"), (1, "S-1"),
                                                            (2, "O-1"), (1, "O-2")]
    assert [r["partnumber"] for r in asm.bom_list(top_only=True) if r["level"] == 0] == ["T-1"]
    assert lst[2]["linecost"] == 8


def test_component_choices(ic, conn):
    a = mk_part(ic, "AB-1", "1", "1")
    mk_part(ic, "XAB-2", "1", "1")
    mk_part(ic, "C-3", "1", "1", obsolete=True)
    pid = mk_asm(ic, "AB", [(a, 1)])
    assert [r["partnumber"] for r in ic.component_choices("ab", exclude_id=pid)] == ["AB-1", "XAB-2"]
    assert [r["partnumber"] for r in ic.component_choices("ab")][:1] == ["AB"]     # exact nummer eerst
    assert ic.component_choices("C-3") == []


def test_line_rounding_like_perl(ic):
    a = mk_part(ic, "O-1", "0.333", "0.0123")
    pid = mk_asm(ic, "P-1", [(a, 1)])
    p = ic.get(pid)
    assert (p.sellprice, p.lastcost) == (D("0.33"), D("0.01"))
    pid = mk_asm(ic, "P-2", [(a, 3)])
    assert (ic.get(pid).sellprice, ic.get(pid).lastcost) == (1, D("0.04"))


def test_build_closed_period(ic, asm, conn):
    """F41: assembleren en demonteren weigeren vóór of op de afsluitdatum."""
    from sqlledger.errors import ClosedPeriodError
    from test_parts import set_closedto
    a = mk_part(ic, "O-1", "1", "1")
    pid = mk_asm(ic, "P-1", [(a, 1)])
    set_onhand(conn, a, 5)
    set_closedto(conn, "2026-03-31")
    with pytest.raises(ClosedPeriodError, match="afgesloten periode"):
        asm.build(pid, 1, transdate=date(2026, 3, 31))
    assert onhand(conn, pid) == 0
    asm.build(pid, 1, transdate=date(2026, 4, 1))
    with pytest.raises(ClosedPeriodError):
        asm.build(pid, -1, transdate=date(2026, 1, 15))
    assert onhand(conn, pid) == 1
