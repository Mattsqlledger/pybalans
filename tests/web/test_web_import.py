"""Beheer > Importeren en Exporteren (FASE4_ANALYSE.md stap 7, keuzes 1–12)."""

import io
import re

import psycopg
import pytest

from conftest import DSN
from webhelpers import csrf, login, make_user

URL = "/beheer/importeren"


def choose(client, company):
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company["id"]})


@pytest.fixture
def admin(client, db, company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    choose(client, company)
    return client


def cconn(company):
    return psycopg.connect(f"{DSN} dbname={company['dbname']}", autocommit=True)


def upload(client, kind, data: bytes, name="klanten.csv"):
    page = client.get(URL).text
    return client.post(URL, data={"csrf": csrf(client, page), "kind": kind},
                       files={"bestand": (name, io.BytesIO(data), "text/csv")}, follow_redirects=True)


def submit(client, page_html, url, action, rows=None, **extra):
    data = {"csrf": csrf(client, page_html), "action": action, "chosen_set": "1"}
    for m in re.finditer(r'<select name="(col_\d+)"[^>]*>(.*?)</select>', page_html, re.S):
        sel = re.search(r'<option value="([^"]*)" selected', m.group(2))
        data[m.group(1)] = sel.group(1) if sel else ""
    data.update(extra)
    if rows is None:
        rows = re.findall(r'name="row" value="(\d+)"[^>]*checked', page_html)
    data["row"] = rows
    return client.post(url, data=data, follow_redirects=True)


def test_rights_and_menu(client, db, company):
    secret = make_user(db, "boek", totp=True, memberships=[(company["id"], "bookkeeper", "boek")])
    login(client, "boek", secret)
    choose(client, company)
    assert client.get(URL).status_code == 403
    page = client.get("/beheer/exporteren")
    assert page.status_code == 200 and "/beheer/exporteren/customer.xlsx" in page.text
    assert "/beheer/exporteren/coa.csv" not in page.text            # rekeningschema: alleen beheerder
    assert client.get("/beheer/exporteren/coa.csv").status_code == 403


def test_import_customers_flow(admin, company, db):
    data = "Klantnummer;Naam;Plaats;Onbekend\n;Importklant BV;Leiden;x\n;;Zonder naam;y\n".encode()
    r = upload(admin, "customer", data)
    assert r.status_code == 200 and "Importklant BV" in r.text
    url = str(r.url.path)
    assert re.fullmatch(r"/beheer/importeren/[0-9a-f]{32}", url)
    assert '<option value="customernumber" selected' in r.text and '<option value="city" selected' in r.text
    assert "1 nieuw, 0 bijwerken, 0 ongewijzigd, 1 met een fout." in r.text
    with cconn(company) as c:
        assert c.execute("SELECT count(*) FROM customer WHERE name = 'Importklant BV'").fetchone()[0] == 0
    # kolom "Plaats" anders koppelen en het voorbeeld bijwerken
    r2 = submit(admin, r.text, url, "preview", col_2="notes")
    assert '<option value="notes" selected' in r2.text
    r3 = submit(admin, r2.text, url, "import", remember="1")
    assert "Klaar: 1 nieuw, 0 bijgewerkt" in r3.text
    with cconn(company) as c:
        assert c.execute("SELECT notes FROM customer WHERE name = 'Importklant BV'").fetchone()[0] == "Leiden"
        assert c.execute("SELECT value FROM x_setting WHERE key = 'import_map.customer'").fetchone()
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'import'").fetchone()
    assert log["detail"]["added"] == 1 and log["detail"]["kind"] == "customer"
    # bestand is weg na importeren; onthouden koppeling wordt de volgende keer gebruikt
    assert admin.get(url, follow_redirects=False).status_code in (302, 303)
    r4 = upload(admin, "customer", "Klantnummer;Naam;Plaats\n;Tweede;Gouda\n".encode())
    assert '<option value="notes" selected' in r4.text


def test_import_nothing_when_a_chosen_row_fails(admin, company):
    r = upload(admin, "partsgroup", "partsgroup\nGroep A\nGroep B\n".encode())
    url = str(r.url.path)
    # tussen voorbeeld en importeren ontstaat Groep B twee keer (andere gebruiker): fout, dan niets importeren
    with cconn(company) as c:
        c.execute("INSERT INTO partsgroup (partsgroup) VALUES ('Groep B '), (' groep b')")
    r2 = submit(admin, r.text, url, "import", rows=["1", "2"])
    assert r2.status_code == 400 and "Niets geïmporteerd" in r2.text
    with cconn(company) as c:
        assert c.execute("SELECT count(*) FROM partsgroup WHERE partsgroup = 'Groep A'").fetchone()[0] == 0


def test_bad_file_and_expired_token(admin):
    r = upload(admin, "customer", b"", name="leeg.csv")
    assert r.status_code == 400
    r = admin.get(URL + "/" + "0" * 32, follow_redirects=True)
    assert "niet meer beschikbaar" in r.text


def test_export_round_trip(admin, company):
    r = admin.get("/beheer/exporteren/coa.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "rekeningschema.xlsx" in r.headers["content-disposition"]
    r = admin.get("/beheer/exporteren/customer.csv")
    assert r.text.startswith("﻿customernumber;name;")
    back = upload(admin, "customer", r.content, name="klanten.csv")
    assert "0 nieuw, 0 bijwerken" in back.text and "met een fout" in back.text


def test_menu_items(admin):
    page = admin.get("/").text
    assert 'href="/beheer/importeren"' in page and 'href="/beheer/exporteren"' in page


def test_english_headers_are_recognised(admin):
    r = upload(admin, "vendor", "Vendor Number;Name;City\nL-99;Supplier Ltd;York\n".encode())
    assert '<option value="vendornumber" selected' in r.text and '<option value="city" selected' in r.text


def test_import_gifi_then_chart_with_gifi(admin, company):
    """Bouwstap 3 GF5: GIFI-codes inlezen (CSV en xlsx), daarna een rekeningschema met kolom GIFI dat ernaar verwijst."""
    r = upload(admin, "gifi", "accno;description\n020;Goodwill\n0200;GIFI Gebouwen\n".encode(), name="gifi.csv")
    assert "1 nieuw, 0 bijwerken, 1 ongewijzigd, 0 met een fout." in r.text
    r2 = submit(admin, r.text, str(r.url.path), "import")
    assert "Klaar: 1 nieuw, 0 bijgewerkt" in r2.text
    # xlsx met de code als getal: wel ingelezen, met waarschuwing over de voorloopnul
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["accno", "description"])
    wb.active.append([21, "Getal"])
    buf = io.BytesIO()
    wb.save(buf)
    r = upload(admin, "gifi", buf.getvalue(), name="gifi.xlsx")
    assert "1 nieuw" in r.text and "voorloopnul" in r.text
    # rekeningschema met GIFI: bekende code zonder waarschuwing, onbekende code ingelezen mét waarschuwing
    data = "accno;description;category;gifi_accno\n9901;Goodwill nieuw;A;020\n9902;Zonder code;A;777\n".encode()
    r = upload(admin, "coa", data, name="rekeningschema.csv")
    assert "2 nieuw" in r.text and "0 met een fout" in r.text
    assert r.text.count("staat niet in de GIFI-indeling") == 1 and "777" in r.text
    r2 = submit(admin, r.text, str(r.url.path), "import")
    assert "Klaar: 2 nieuw" in r2.text
    with cconn(company) as c:
        rows = c.execute("SELECT accno, gifi_accno FROM chart WHERE accno IN ('9901', '9902') ORDER BY accno").fetchall()
    assert rows == [("9901", "020"), ("9902", "777")]
    page = admin.get("/beheer/gifi").text
    assert 'href="/beheer/gifi/nieuw?code=777"' in page          # signaal op de GIFI-indeling
    # export rekeningschema -> terug importeren: niets verandert, ook de GIFI-kolom niet
    back = upload(admin, "coa", admin.get("/beheer/exporteren/coa.csv").content, name="coa.csv")
    assert "0 nieuw, 0 bijwerken" in back.text and "0 met een fout" in back.text


# ------------------------------------------------------------------ facturen, orders, betalingen (bouwstap 4)

def test_import_sales_invoices_flow(admin, company):
    from webhelpers import nohelp
    form = admin.get(URL).text
    for kind in ("sales_invoice", "sales_order", "purchase_order", "payment"):
        assert f'<option value="{kind}"' in form
    data = ("Factuurnummer;Klantnummer;Factuurdatum;Artikelnummer;Aantal\n"
            "W-1;K001;2026-01-05;KOEK-1;2\n;;;BOEK-1;1\nW-2;K999;2026-01-05;KOEK-1;1\n").encode()
    r = upload(admin, "sales_invoice", data, "facturen.csv")
    assert r.status_code == 200
    page = nohelp(r.text)
    assert '<option value="invnumber" selected' in page and '<option value="qty" selected' in page
    assert "1 nieuw, 0 bijwerken, 0 ongewijzigd, 1 met een fout." in page
    assert ">Netto</th>" in page and ">Totaal</th>" in page and "Klant K999 bestaat niet." in page
    assert re.search(r"<td>W-1</td>\s*<td>Klant A</td>\s*<td>EUR</td><td class=\"num\">45[.,]00</td>", page)
    with cconn(company) as c:
        assert c.execute("SELECT count(*) FROM ar WHERE invnumber = 'W-1'").fetchone()[0] == 0
    done = submit(admin, r.text, str(r.url.path), "import")
    assert done.status_code == 200 and "Klaar: 1 nieuw, 0 bijgewerkt" in done.text and ">Netto</th>" in done.text
    with cconn(company) as c:
        assert c.execute("SELECT netamount FROM ar WHERE invnumber = 'W-1'").fetchone()[0] == 45


def test_import_payments_flow(admin, company):
    from webhelpers import nohelp
    data = "datepaid;amount;invnumber\n2026-01-10;21;F-3001\n2026-01-10;1;XX\n".encode()
    r = upload(admin, "payment", data, "betalingen.csv")
    page = nohelp(r.text)
    assert ">Openstaand</th>" in page and ">Netto</th>" not in page
    assert "Geen factuur gevonden voor XX." in page
    done = submit(admin, r.text, str(r.url.path), "import")
    assert "Klaar: 1 nieuw" in done.text
    with cconn(company) as c:
        assert c.execute("SELECT paid FROM ar WHERE invnumber = 'F-3001'").fetchone()[0] == 21


def test_import_documents_english(admin, company, db):
    db.execute("UPDATE app_user SET language = 'en' WHERE login = 'baas'")
    r = upload(admin, "payment", "datepaid;amount;invnumber\n2026-01-10;1;XX\n".encode(), "p.csv")
    assert "Import: Payments" in r.text and "No invoice found for XX." in r.text
    assert ">Outstanding</th>" in r.text
