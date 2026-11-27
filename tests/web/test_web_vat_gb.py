"""VAT Return (VK) in de webapp (routes_vat, BTW_LANDEN_ANALYSE.md BL2/BL7/BL10, UK1–UK4): land kiezen, boxes per
btw-rekening, overzicht, formulier, CSV voor bridging software, btw-boeking, klantscherm, taal."""

import re

from test_web_vat import KEY, cdb, data, form_fields, text, user  # noqa: F401  (fixtures)
from webhelpers import csrf, post


def setup_gb(client, offset="0"):
    r = post(client, "/btw/rubrieken", {"switch": "1", "country": "GB"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/btw/rubrieken").text
    assert "Britse VAT Return" in page and "Kwartaal eindigt in" in page
    fields = dict(re.findall(r'<select name="(box_\d+)"[^>]*>.*?<option value="(\w+)" selected', page, re.S))
    r = post(client, "/btw/rubrieken", {**fields, "country": "GB", "offset": offset, "settlement": "1850",
                                        "rounding": "8100"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/btw/rubrieken?ok=vat_boxes_saved"
    return page


def test_country_switch_and_boxes(client, db, company, cdb):  # noqa: F811
    user(client, db, company, "admin")
    page = client.get("/btw/rubrieken").text
    assert 'name="country"' in page and "Voorstel: Nederland" in page
    page = setup_gb(client)
    assert "Box 1 btw op verkopen (grondslag in box 6)" in page and "Box 4 voorbelasting" in page
    boxes = {r["accno"]: r["box"] for r in cdb.execute(
        "SELECT c.accno, b.box FROM x_vat_box b JOIN chart c ON (c.id = b.chart_id) WHERE b.country = 'GB'")}
    assert boxes == {"1800": "4", "1801": "4", "1810": "1", "1811": "1"}
    assert cdb.execute("SELECT value FROM x_setting WHERE key = 'vat_country'").fetchone()["value"] == "GB"
    assert db.execute("SELECT vat_return, icp FROM company WHERE id = %s", (company["id"],)).fetchone() == {
        "vat_return": True, "icp": False}
    # terug naar NL: de Nederlandse koppelingen (nog leeg) en de Britse blijven bewaard
    post(client, "/btw/rubrieken", {"switch": "1", "country": "NL"})
    assert "Nederlandse btw-aangifte" in client.get("/btw/rubrieken").text
    assert cdb.execute("SELECT count(*) AS n FROM x_vat_box WHERE country = 'GB'").fetchone()["n"] == 4


def test_overview_form_and_csv(client, db, company, data):  # noqa: F811
    user(client, db, company, "admin")
    setup_gb(client)
    t = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert "VAT Return (Verenigd Koninkrijk): 2031 1e kwartaal" in t
    assert re.search(r"1 Btw verschuldigd over verkopen en andere prestaties 21", t)
    assert re.search(r"5 Netto te betalen of terug te vragen btw \(box 3 min 4\) 12", t)
    assert re.search(r"6 Totale waarde verkopen en andere prestaties, zonder btw 300", t)   # ook zonder btw
    assert re.search(r"7 Totale waarde inkopen .* 40", t)
    assert "CSV voor bridging software" in t and "Btw-boeking maken" in t
    csv = client.get(f"/btw/aangifte?tijdvak={KEY}&export=csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.splitlines()
    assert lines[1:3] == ["Period start,2031-01-01", "Period end,2031-03-31"]
    assert "1,VAT due in the period on sales and other outputs,21.00" in lines
    assert "5,Net VAT to pay to HMRC or reclaim (the difference between boxes 3 and 4),12.00" in lines
    assert "6,Total value of sales and all other outputs excluding any VAT,300.00" in lines
    assert lines[-1] == "5 direction,pay,"
    form = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}").text
    ft = text(form)
    assert "VAT Return" in ft and "VAT due in the period on sales and other outputs" in ft and "Proefafdruk" in ft
    assert "overtypen of kopiëren naar HMRC is niet toegestaan" in ft
    assert 'class="vatform gb"' in form
    p = client.get(f"/btw/aangifte/formulier?tijdvak={KEY}&export=pdf")
    assert p.status_code == 200 and p.content[:4] == b"%PDF"
    x = client.get(f"/btw/aangifte?tijdvak={KEY}&export=xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    detail = text(client.get(f"/btw/aangifte?tijdvak={KEY}&rubriek=6").text)
    assert "200,00" in detail and "100,60" in detail


def test_booking_description_and_register(client, db, company, data, cdb):  # noqa: F811
    user(client, db, company, "admin")
    setup_gb(client)
    page = client.get(f"/memoriaal/nieuw?btw={KEY}").text
    f = form_fields(page)
    assert f["description"] == "VAT Return 2031 1e kwartaal"
    rows = {(f.get(f"accno_{i}"), f.get(f"debit_{i}"), f.get(f"credit_{i}")) for i in range(1, 8) if f.get(f"accno_{i}")}
    assert rows == {("1810", "21,13", ""), ("1800", "", "8,48"), ("1850", "", "12,00"), ("8100", "", "0,65")}
    f.pop("csrf")
    r = client.post("/memoriaal", data={"csrf": csrf(client, page), **f, "action": "post"}, follow_redirects=False)
    assert r.status_code == 303
    ret = cdb.execute("SELECT * FROM x_vat_return").fetchone()
    assert ret["country"] == "GB" and ret["snapshot"]["totals"]["5"] == "12"
    assert "Definitief: btw-boeking" in text(client.get(f"/btw/aangifte/formulier?tijdvak={KEY}").text)


def test_stagger_quarter_periods(client, db, company, data):  # noqa: F811
    user(client, db, company, "admin")
    setup_gb(client, offset="1")
    page = client.get("/btw/aangifte?tijdvak=2031-02-01_2031-04-30").text
    assert "februari – april 2031" in text(page)
    assert re.search(r'<option value="\d{4}-\d\d-01_\d{4}-(01|04|07|10)-3[01]"', page)


def test_customer_screen_gb_choices(client, db, company, data, cdb):  # noqa: F811
    user(client, db, company, "admin")
    setup_gb(client)
    page = client.get("/klanten/903").text
    assert "Box 6 (standaard)" in page and "Box 8 goederen Noord-Ierland naar de EU" in page
    assert '<option value="3B"' not in page and '<select name="icp_kind"' not in page
    f = form_fields(page)
    f.pop("csrf")
    f["vat_box"] = "8"
    r = client.post("/klanten", data={"csrf": csrf(client, page), **f, "action": "save"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    row = cdb.execute("SELECT box, country FROM x_vat_customer_box WHERE customer_id = 903").fetchone()
    assert row == {"box": "8", "country": "GB"}
    t = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert re.search(r"8 Waarde leveringen van goederen naar de EU, zonder btw \(Noord-Ierland\) 200", t)


def test_english(client, db, company, data):  # noqa: F811
    user(client, db, company, "admin", "beheer")
    setup_gb(client)
    client.cookies.clear()
    user(client, db, company, "readonly", "kijker", lang="en")
    t = text(client.get(f"/btw/aangifte?tijdvak={KEY}").text)
    assert "VAT Return (United Kingdom): Q1 2031" in t and "VAT due on sales and other outputs" in t
    assert "CSV for bridging software" in t
