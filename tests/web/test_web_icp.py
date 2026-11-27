"""ICP-opgaaf in de webapp (routes_icp, ICP_ANALYSE.md ICP1–ICP11): menu, klantscherm (ICP-soort, btw-nummer
verplicht), overzicht per afnemer, doorklikken, PDF/Excel/CSV, vastleggen met de btw-boeking en apart, verschil,
instelling tijdvak, dashboard, rechten en taal."""

from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from sqlledger import ArapLedger, UserConfig, connect
from sqlledger.arap import ArapLine, ArapTransaction
from test_web_vat import KEY, cdb, form_fields, setup_boxes, text, user  # noqa: F401  (fixture)
from webhelpers import csrf, post

D = Decimal


@pytest.fixture
def eu(settings, company_db, cdb):  # noqa: F811
    """Twee EU-klanten op 3B (DE, FR diensten), een klant zonder nummer, een binnenlandse verkoop."""
    cdb.execute("UPDATE customer SET taxnumber = 'DE123456789', name = 'Kunde GmbH' WHERE id = 902")
    cdb.execute("UPDATE customer SET taxnumber = '', name = 'Zonder Nummer' WHERE id = 903")
    conn = connect(**settings.company_conninfo(company_db))
    al = ArapLedger(conn, UserConfig(login="x"))
    for vc_id, net, taxes, d in ((902, "1000.40", {}, date(2031, 2, 10)), (903, "200", {}, date(2031, 2, 11)),
                                 (902, "-100", {}, date(2031, 3, 1)),
                                 (903, "50", {"1810": "10.50"}, date(2031, 3, 2))):
        al.post_transaction(ArapTransaction(vc="customer", vc_id=vc_id, transdate=d, arap_accno="1200",
                                            lines=[ArapLine(accno="8520", amount=D(net))],
                                            taxes={k: D(v) for k, v in taxes.items()}))
    conn.close()
    cdb.execute("SELECT 1")
    return cdb


def to_3b(client, cid, taxnumber, kind="", expect=303):
    page = client.get(f"/klanten/{cid}").text
    f = form_fields(page)
    f.pop("csrf")
    f.update(vat_box="3B", taxnumber=taxnumber, icp_kind=kind)
    r = client.post("/klanten", data={"csrf": csrf(client, page), **f, "action": "save"}, follow_redirects=False)
    assert r.status_code == expect, r.text[:800]
    return r


def test_menu_customer_and_overview(client, db, company, eu):
    user(client, db, company, "admin")
    setup_boxes(client)
    assert 'href="/btw/icp"' not in client.get("/").text
    page = client.get("/klanten/902").text
    assert '<select name="icp_kind"' in page
    to_3b(client, 902, "DE 123.456.789")
    assert db.execute("SELECT icp FROM company WHERE id = %s", (company["id"],)).fetchone()["icp"]
    assert 'href="/btw/icp"' in client.get("/").text
    # klant op 3B zonder (geldig) nummer: geweigerd
    r = to_3b(client, 903, "", expect=400)
    assert "moet een btw-nummer hebben" in r.text
    r = to_3b(client, 903, "NL123456789B01", expect=400)
    assert "geen Nederlands nummer" in r.text
    r = to_3b(client, 903, "FR12", expect=400)
    assert "geen geldig EU-btw-nummer" in r.text
    to_3b(client, 903, "FRXX123456789", kind="services")
    assert 'value="services" selected' in client.get("/klanten/903").text
    t = text(client.get(f"/btw/icp?tijdvak={KEY}").text)
    assert "ICP-opgaaf: 2031 1e kwartaal" in t
    assert "DE 123456789 900 0 Kunde GmbH" in t
    assert "FR XX123456789 0 200 " in t
    assert "Totaal 900 200" in t
    assert "Rubriek 3B van de btw-aangifte: 1.100" in t or "Rubriek 3B van de btw-aangifte: 1,100" in t
    assert "verschillen" not in t and "Zonder geldig btw-nummer" not in t
    # aangifte linkt naar de ICP-opgaaf
    assert "geef de afnemers op in de ICP-opgaaf" in text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    # doorklikken en exports
    d = text(client.get(f"/btw/icp?tijdvak={KEY}&afnemer=DE123456789").text)
    assert "Totaal (exact) 900,40" in d or "Totaal (exact) 900.40" in d
    assert client.get(f"/btw/icp?tijdvak={KEY}&export=xlsx").content[:2] == b"PK"
    assert client.get(f"/btw/icp?tijdvak={KEY}&export=pdf").content[:4] == b"%PDF"
    c = client.get(f"/btw/icp?tijdvak={KEY}&export=csv")
    assert c.status_code == 200 and "DE;123456789;900;0;Kunde GmbH" in c.text


def test_problem_customer_shown(client, db, company, eu):
    user(client, db, company, "admin")
    setup_boxes(client)
    to_3b(client, 902, "DE123456789")
    eu.execute("INSERT INTO x_vat_customer_box (customer_id, box) VALUES (903, '3B')")   # oude gegevens
    t = text(client.get(f"/btw/icp?tijdvak={KEY}").text)
    assert "Zonder geldig btw-nummer kan de opgaaf niet worden ingediend" in t and "Zonder Nummer — geen btw-nummer" in t
    a = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert "Relaties in rubriek 3B of 4B zonder geldig EU-btw-nummer" in a and "3B Zonder Nummer" in a


def test_registered_with_vat_entry_and_difference(client, db, company, eu):
    user(client, db, company, "admin")
    setup_boxes(client)
    to_3b(client, 902, "DE123456789")
    page = client.get(f"/memoriaal/nieuw?btw={KEY}").text
    f = form_fields(page)
    f.pop("csrf")
    r = client.post("/memoriaal", data={"csrf": csrf(client, page), **f, "action": "post"}, follow_redirects=False)
    assert r.status_code == 303
    snap = eu.execute("SELECT * FROM x_icp_return").fetchone()
    assert snap["gl_id"] and snap["snapshot"]["lines"]["DE123456789"]["goods"] == "900"
    t = text(client.get(f"/btw/icp?tijdvak={KEY}").text)
    assert "Vastgelegd met btw-boeking" in t and "ICP-opgaaf vastleggen" not in t
    # later gewijzigd: verschil per afnemer
    eu.execute("UPDATE ar SET netamount = 1100.40, amount = 1100.40 WHERE customer_id = 902 AND transdate = '2031-02-10'")
    eu.execute("""UPDATE acc_trans SET amount = -1100.40 WHERE amount = -1000.40""")
    eu.execute("""UPDATE acc_trans SET amount = 1100.40 WHERE amount = 1000.40""")
    t = text(client.get(f"/btw/icp?tijdvak={KEY}").text)
    assert "De boekhouding wijkt af van de vastgelegde opgaaf" in t and "DE123456789 Kunde GmbH 900 1.000" in t.replace("1,000", "1.000")


def test_separate_period_register_and_dashboard(client, db, company, eu):
    user(client, db, company, "admin")
    setup_boxes(client)
    to_3b(client, 902, "DE123456789")
    page = client.get("/beheer/instellingen").text
    assert 'name="icp_period"' in page
    fields = form_fields(page)
    fields.pop("csrf", None)
    fields["icp_period"] = "month"
    post(client, "/beheer/instellingen", fields)
    assert eu.execute("SELECT value FROM x_setting WHERE key = 'icp_period'").fetchone()["value"] == "month"
    feb = "2031-02-01_2031-02-28"
    t = text(client.get(f"/btw/icp?tijdvak={feb}").text)
    assert "ICP-opgaaf: februari 2031" in t and "ICP-opgaaf vastleggen" in t
    page = client.get(f"/btw/icp?tijdvak={feb}").text
    r = client.post("/btw/icp/vastleggen", data={"csrf": csrf(client, page), "tijdvak": feb}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/btw/icp?tijdvak={feb}&ok=icp_registered"
    assert "De ICP-opgaaf is vastgelegd." in client.get(r.headers["location"]).text
    assert eu.execute("SELECT gl_id FROM x_icp_return").fetchone()["gl_id"] is None


def test_dashboard_line(client, db, company, eu, monkeypatch):
    import sqlledger_web.routes_dashboard as rd
    from sqlledger_web import routes_icp

    class Fixed(date):
        @classmethod
        def today(cls):
            return date(2031, 4, 10)
    user(client, db, company, "admin")
    setup_boxes(client)
    to_3b(client, 902, "DE123456789")
    monkeypatch.setattr(rd, "date", Fixed)
    monkeypatch.setattr(routes_icp, "date", Fixed)
    t = text(client.get("/dashboard/blok/tedoen").text)
    assert "ICP-opgaaf 2031 1e kwartaal: vóór 2031-04-30" in t or "ICP-opgaaf 2031 1e kwartaal: vóór 30-04-2031" in t


def test_rights_and_language(client, db, company, eu):
    user(client, db, company, "admin", "beheer")
    setup_boxes(client)
    to_3b(client, 902, "DE123456789")
    client.cookies.clear()
    user(client, db, company, "readonly", "kijker", lang="en")
    t = text(client.get(f"/btw/icp?tijdvak={KEY}").text)
    assert "EC Sales List: Q1 2031" in t and "Country code" in t and "Record EC Sales List" not in t
    page = client.get(f"/btw/icp?tijdvak={KEY}").text
    r = client.post("/btw/icp/vastleggen", data={"csrf": csrf(client, page), "tijdvak": KEY})
    assert r.status_code == 403
