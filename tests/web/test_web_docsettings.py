"""Beheer > Documentinstellingen (fase 4 stap 3): scherm, opslaan per taal, rollen, en dat PDF en mail de
instellingen gebruiken."""

import re

import pytest

from sqlledger_web import documents
from test_web_arap import step
from test_web_is import add_item, cdb, posted_id, setup, start  # noqa: F401 (fixtures)
from webhelpers import csrf, login, make_user

URL = "/beheer/documentinstellingen"


@pytest.fixture
def admin(client, db, company, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    return client


@pytest.fixture
def html_pdf(monkeypatch):
    """PDF-route geeft de HTML terug, zodat de inhoud te controleren is."""
    monkeypatch.setattr(documents.DocumentRenderer, "render_pdf",
                        lambda self, formname, v, overrides=None: self.render_html(formname, v, overrides).encode())


def form_values(page):
    """Alle velden van het formulier zoals de browser ze zou versturen."""
    data = {}
    for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', page):
        tag, name = m.group(0), m.group(1)
        if 'type="checkbox"' in tag or 'type="radio"' in tag:
            if " checked" in tag:
                data[name] = re.search(r'value="([^"]*)"', tag).group(1)
        else:
            v = re.search(r'value="([^"]*)"', tag)
            data[name] = v.group(1) if v else ""
    for m in re.finditer(r'<textarea name="([^"]+)"[^>]*>\n?(.*?)</textarea>', page, re.S):
        data[m.group(1)] = m.group(2)
    for m in re.finditer(r'<select name="([^"]+)"[^>]*>(.*?)</select>', page, re.S):
        sel = re.search(r'<option value="([^"]*)" selected', m.group(2))
        data[m.group(1)] = sel.group(1) if sel else ""
    return data


def save(client, taal="nl", **changes):
    page = client.get(f"{URL}?taal={taal}").text
    data = form_values(page)
    data.update(changes)
    for k in [k for k, v in changes.items() if v is None]:
        data.pop(k)
    return client.post(URL, data={**data, "csrf": csrf(client, page)}, follow_redirects=False)


def make_invoice(client):
    page = start(client, "K001")
    page = add_item(client, page, "A-1", qty="2")
    return posted_id(step(client, "/verkoopfactuur", {"action": "post"}, page))


def test_page_menu_and_roles(admin, client, db, company):
    page = admin.get(URL).text
    assert f'href="{URL}"' in admin.get("/").text
    assert "Documentinstellingen" in page and 'name="taxnumber"' in page and 'name="mode_sales_invoice_barcode"' in page
    assert 'name="closing_invoice"' in page and 'placeholder="Dank voor uw bestelling."' in page
    assert "/beheer/sjablonen/voorbeeld?naam=packing_list&amp;variant=nl" in page
    en = admin.get(f"{URL}?taal=en").text
    assert 'placeholder="Thank you for your order."' in en and 'name="taal" value="en"' in en
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    client.cookies.clear()
    login(client, "boek")
    assert client.get(URL).status_code == 403
    assert client.post(URL, data={"csrf": csrf(client, client.get("/").text)}).status_code == 403


def test_save_per_language(admin, cdb):
    # zonder wijzigingen opslaan: niets in de tabel
    r = save(admin)
    assert r.status_code == 303 and r.headers["location"] == f"{URL}?taal=nl&ok=1"
    assert cdb.execute("SELECT count(*) AS n FROM x_doc_setting").fetchone()["n"] == 0
    r = save(admin, taxnumber="NL001234567B01", text_footer="Voet NL", closing_invoice="Bedankt",
             mode_sales_invoice_barcode="column", label_sales_invoice_barcode="EAN-code", weights_order="1",
             payinfo=None)
    assert r.status_code == 303
    assert "Documentinstellingen opgeslagen." in admin.get(r.headers["location"]).text
    r = save(admin, "de", text_footer="Fußzeile DE", label_sales_invoice_barcode="EAN DE")
    assert r.status_code == 303
    rows = {r["key"]: r["value"] for r in cdb.execute("SELECT key, value FROM x_doc_setting").fetchall()}
    assert rows["taxnumber"] == "NL001234567B01" and rows["text:nl:footer"] == "Voet NL"
    assert rows["text:de:footer"] == "Fußzeile DE" and rows["text:nl:closing:invoice"] == "Bedankt"
    assert '"de": "EAN DE"' in rows["columns:sales_invoice"] and '"nl": "EAN-code"' in rows["columns:sales_invoice"]
    assert rows["weights:order"] == "1" and '"payinfo": false' in rows["bank"]
    page = admin.get(f"{URL}?taal=de").text
    assert "Fußzeile DE" in page and 'value="EAN DE"' in page and "Voet NL" not in page
    assert cdb.execute("SELECT count(*) AS n FROM audittrail WHERE tablename = 'x_doc_setting'").fetchone()["n"] >= 8


def test_errors_shown(admin, cdb):
    r = save(admin, taxnumber="X" * 41, bank_mode="fixed", bank_accno="1100")
    assert r.status_code == 400
    assert "Het btw-nummer is te lang." in r.text and "Kies een bankrekening met bankgegevens." in r.text
    assert 'value="' + "X" * 41 + '"' in r.text      # invoer blijft staan
    assert cdb.execute("SELECT to_regclass('x_doc_setting') AS t").fetchone()["t"] is None


def test_pdf_and_preview_use_settings(admin, cdb, html_pdf):
    cdb.execute("""INSERT INTO bank (id, name, iban, bic) SELECT id, 'Interbank NV', 'NL02RABO0123456789', 'RABONL2U'
                   FROM chart WHERE accno = '1100'""")
    other = cdb.execute("""SELECT id, accno FROM chart WHERE charttype = 'A' AND category = 'A' AND accno <> '1100'
                           ORDER BY accno LIMIT 1""").fetchone()
    cdb.execute("""INSERT INTO bank (id, name, iban, bic) VALUES (%s, 'Spaarbank', 'NL91ABNA0417164300', 'ABNANL2A')""",
                (other["id"],))
    sid = make_invoice(admin)
    cdb.execute("UPDATE parts SET barcode = '8712345678906' WHERE partnumber = 'A-1'")
    before = admin.get(f"/document/invoice/{sid}.pdf").text
    assert "NL91ABNA0417164300" not in before and ">EAN</th>" not in before
    assert save(admin, taxnumber="NL001234567B01", bank_mode="fixed", bank_accno=other["accno"], bank_name="1",
                mode_sales_invoice_barcode="auto", closing_invoice="Tot **ziens**").status_code == 303
    html = admin.get(f"/document/invoice/{sid}.pdf").text
    assert "Btw-nummer NL001234567B01" in html and "Spaarbank IBAN NL91ABNA0417164300" in html
    assert ">EAN</th>" in html and "8712345678906" in html and "Tot <strong>ziens</strong>" in html
    preview = admin.get("/beheer/sjablonen/voorbeeld?naam=invoice&variant=en").text
    assert "VAT number NL001234567B01" in preview and "Thank you for your order." in preview


def test_mail_attachment_uses_settings(admin, cdb, html_pdf, settings, tmp_path):
    sid = make_invoice(admin)
    assert save(admin, closing_invoice="Mailslot").status_code == 303
    seen = []
    orig = documents.DocumentRenderer.render_pdf

    def spy(self, formname, v, overrides=None):
        seen.append(self.settings.text("nl", "closing", "invoice"))
        return orig(self, formname, v, overrides)
    documents.DocumentRenderer.render_pdf = spy
    try:
        page = admin.get(f"/document/invoice/{sid}/mail").text
        admin.post(f"/document/invoice/{sid}/mail", data={"csrf": csrf(admin, page), "to": "klant@example.org",
                                                          "subject": "Factuur", "message": "Hallo"})
    finally:
        documents.DocumentRenderer.render_pdf = orig
    assert seen == ["Mailslot"]
