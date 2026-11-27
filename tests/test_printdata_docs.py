"""Documenten fase 4 stap 2: orders/offertes (order_vars), verkoop/inkoop zonder artikelen
(transaction_vars), pak-/picklijst, onderdelen van samengestelde artikelen (DS3–DS7)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapLedger, ArapLine, ArapPayment, ArapTransaction, Part, UserConfig
from sqlledger.assembly import Component
from sqlledger.printdata import PrintData

from test_invoice import CUST, VEND, mk_part, mk_service, sale
from test_order import ic, il, ol, order  # noqa: F401 (fixtures)

D = Decimal


@pytest.fixture
def pd(conn):
    return PrintData(conn, UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy",
                                      countrycode="nl"), username="Jan", useremail="jan@example.com")


def assembly(ic, *, bom=True):
    a = mk_part(ic, "ONDERDEEL-1")
    b = mk_part(ic, "ONDERDEEL-2")
    return ic.save(Part(kind="assembly", partnumber="PAKKET", description="Pakket", income_accno="8520",
                        taxaccounts=["1810"], components=[Component(parts_id=a, qty=D(2), bom=bom),
                                                          Component(parts_id=b, qty=D(1), bom=False)]))


# ------------------------------------------------------------------ orders en offertes

def test_order_vars_sales_order(ol, ic, pd):
    a = mk_part(ic, "A-1", sell="12.345")
    s = mk_service(ic, "S-1", sell="100")
    oid = ol.save_order(order([(a, 3, "12.345", {"reqdate": date(2026, 3, 9)}), (s, 1, "100", {"discount": D(10)})],
                              reqdate=date(2026, 3, 10), notes="Order <%ordnumber%>"))
    v = pd.order_vars(oid)
    assert v["formname"] == "sales_order" and v["label"] == "Sales Order"
    assert v["orddate"] == "01-03-2026" and v["reqdate"] == "10-03-2026"
    assert v["number"] == ["A-1", "S-1"] and v["requiredate"] == ["09-03-2026", ""]
    assert "deliverydate" not in v                       # orders: requiredate (OE::order_details)
    assert v["qty"] == ["3", "1"] and v["ship"] == ["", ""]           # nog niets geleverd
    assert v["linetotal"] == ["37,04", "90,00"] and v["subtotal"] == "127,04"
    assert v["ordtotal"] == v["quototal"] == v["total"] and v["paid"] == ""
    assert v["notes"] == f"Order {v['ordnumber']}"
    assert v["terms"] == "" and v["customer"] == v["name"]
    assert v["shiptoname"] == v["name"]                  # verkooporder: adres van de klant


def test_order_vars_packing_list_after_partial_invoice(ol, il, ic, pd):
    a = mk_part(ic, "B-1", sell="10")
    il.post_invoice(purchase_stock(a))
    oid = ol.save_order(order([(a, 5, "10")]))
    inv = ol.to_invoice(oid)
    inv.lines[0].qty = D(3)
    il.post_invoice(inv)                                 # deellevering: order blijft open met ship 3
    v = pd.order_vars(oid, "packing_list")
    assert v["formname"] == "packing_list" and v["label"] == "Packing List"
    assert v["qty"] == ["5"] and v["ship"] == ["3"] and v["totalship"] == "3"


def purchase_stock(part, qty=20):
    from test_invoice import purchase
    return purchase([(part, qty, "2")])


def test_order_vars_quotation_and_purchase_order(ol, ic, pd):
    a = mk_part(ic, "C-1", sell="5")
    q = ol.save_order(order([(a, 2, "5")], type="sales_quotation", reqdate=date(2026, 3, 31)))
    v = pd.order_vars(q)
    assert v["formname"] == "sales_quotation" and v["label"] == "Quotation"
    assert v["quodate"] == "01-03-2026" and "orddate" not in v and v["quonumber"]
    po = ol.save_order(order([(a, 4, "2")], type="purchase_order"))
    v = pd.order_vars(po)
    assert v["formname"] == "purchase_order" and v["vendor"] == v["name"]
    assert v["shiptoname"] == v["company"]               # inkooporder: eigen adres als afleveradres (io.pl)
    rfq = ol.save_order(order([(a, 4, "2")], type="request_quotation"))
    assert pd.order_vars(rfq)["label"] == "Quotation"    # Perl: ook "Quotation"
    assert pd.order_vars(rfq)["shiptoname"] == v["company"]


# ------------------------------------------------------------------ samengestelde artikelen (DS7)

def test_assembly_components_on_documents(ol, il, ic, pd):
    p = assembly(ic)
    s = mk_service(ic, "S-9", sell="1")
    oid = ol.save_order(order([(p, 3, "20"), (s, 1, "1")]))
    v = pd.order_vars(oid, "pick_list")
    # onderdeel met "op documenten" als extra regel onder het pakket, zonder prijs; het andere niet
    assert v["number"] == ["PAKKET", "ONDERDEEL-1", "S-9"]
    assert v["bom"] == ["", "1", ""]
    assert v["qty"] == ["3", "6", "1"] and v["linetotal"][1] == "" and v["runningnumber"] == [1, "", 2]
    assert all(len(v[k]) == 3 for k in ("sell", "part", "service", "lot", "make", "linetax", "requiredate"))
    tid = il.post_invoice(sale([(p, 1, "20")]))
    w = pd.invoice_vars(tid, "customer", "packing_list")
    assert w["number"] == ["PAKKET", "ONDERDEEL-1"] and w["qty"] == ["1", "2"] and w["bom"] == ["", "1"]
    assert len(w["deliverydate"]) == 2


# ------------------------------------------------------------------ verkoop/inkoop zonder artikelen

def test_transaction_vars(conn, config, pd):
    al = ArapLedger(conn, config)
    t = ArapTransaction(vc="customer", vc_id=CUST, transdate=date(2026, 3, 1), arap_accno="1200",
                        lines=[ArapLine(accno="8520", amount=D("100"), description="Advies maart"),
                               ArapLine(accno="8520", amount=D("50.50"), description="Reiskosten")],
                        description="Maart", notes="Ref <%invnumber%>")
    t.taxes = al.calculate_taxes(t)
    t.payments = [ArapPayment(accno="1100", amount=D("20"), datepaid=date(2026, 3, 5), source="bank")]
    tid = al.post_transaction(t)
    v = pd.transaction_vars(tid, "customer")
    assert v["formname"] == "transaction" and v["label"] == "Transaction"
    assert v["accno"] == ["8520", "8520"] and v["description"] == ["Advies maart", "Reiskosten"]
    assert v["amount"] == ["100,00", "50,50"] and v["subtotal"] == "150,50"
    tax = sum((D(x.replace(".", "").replace(",", ".")) for x in v["tax"]), D(0))
    assert v["invtotal"] == f"{D('150.50') + tax:.2f}".replace(".", ",")
    assert v["payment"] == ["20,00"] and v["paid"] == "20,00"
    assert v["notes"] == f"Ref {v['invnumber']}" and v["invdescription"] == "Maart"
    c = ArapTransaction(vc="vendor", vc_id=VEND, transdate=date(2026, 3, 2), arap_accno="1600", type="debit_note",
                        lines=[ArapLine(accno="8020", amount=D("10"))], cashdiscount=D(2))
    c.taxes = al.calculate_taxes(c)
    w = pd.transaction_vars(al.post_transaction(c), "vendor")
    assert w["formname"] == "debit_note" and w["amount"] == ["10,00"]
    assert w["cd_available"] == "0,00"                   # creditnota/debetnota: geen contantkorting


def test_roundto_from_defaults_and_paymentmethod(conn, config, pd):
    """defaults.roundchange is een getal (is.pl zet er zelf '=' voor); een betaalmethode kan afwijken."""
    al = ArapLedger(conn, config)
    conn.execute("DELETE FROM defaults WHERE fldname = 'roundchange'")
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('roundchange', '0.05')")
    t = ArapTransaction(vc="customer", vc_id=CUST, transdate=date(2026, 3, 1), arap_accno="1200",
                        lines=[ArapLine(accno="8520", amount=D("10.02"))], taxes={})
    v = pd.transaction_vars(al.post_transaction(t), "customer")
    assert v["roundto"] == "0.05" and v["total"] == "10,00" and v["roundingdifference"] == "-0,02"
    pm = conn.execute("INSERT INTO paymentmethod (description, fee, roundchange, rn) VALUES ('Kas', 0, 0.1, 1) "
                      "RETURNING id").fetchone()[0]
    t2 = ArapTransaction(vc="customer", vc_id=CUST, transdate=date(2026, 3, 1), arap_accno="1200",
                         lines=[ArapLine(accno="8520", amount=D("10.02"))], taxes={},
                         payments=[ArapPayment(accno="1100", amount=D("5"), datepaid=date(2026, 3, 1),
                                               paymentmethod_id=pm)])
    v2 = pd.transaction_vars(al.post_transaction(t2), "customer")
    assert v2["roundto"] == "0.1" and v2["total"] == "5,00"


def test_credit_invoice_cash_discount_sign(conn, il, ic, pd):
    """Contantkorting op een creditfactuur: positief zoals op het scherm (was -0,02 → totaal te hoog)."""
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')")
    conn.execute("""INSERT INTO chart (accno, description, charttype, category, link)
                    VALUES ('8900', 'Betalingskorting', 'A', 'E', 'AR_discount:AP_discount')""")
    sv = mk_service(ic, "D-1", "100")
    c = sale([(sv, 1, "100")], type="credit_invoice", cashdiscount=D(2), discountterms=10)
    c.discount = ArapPayment(accno="8900", amount=D("2.42"), datepaid=date(2026, 3, 5))
    c.payments = [ArapPayment(accno="1100", amount=D("118.58"), datepaid=date(2026, 3, 5))]
    v = pd.invoice_vars(il.post_invoice(c), "customer")
    assert v["discount_paid"] == "2,42" and v["cd_amount"] == "2,42"
    assert v["cd_subtotal"] == "97,58"
