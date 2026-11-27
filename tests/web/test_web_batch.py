"""Documenten > Batch (fase 4 stap 4c, SB9–SB11, SB14, SB15).

Testdata uit sl_template: verkoop zonder artikelen (``ar_transaction``) 950, 3003–3006, 3009, 3010; klant 902
(Bakkerij Cees) heeft een e-mailadres, 900 krijgt er een in de fixture, 903 (USD) niet.
"""

import email
import email.policy
import re
from pathlib import Path

import pytest

import sqlledger
from sqlledger_web import routes_batch
from sqlledger_web.documents import DocumentRenderer

from test_web_dunning import as_form, configure_mail, page_fields
from webhelpers import csrf, login, make_user

URL = "/documenten/batch"
ALL = {"soort": "ar_transaction", "van": "", "tot": "", "relatie": "", "nummer": "", "staat": "alle",
       "geprint": "alle", "gemaild": "alle"}


@pytest.fixture
def cdb(settings, company_db):
    conn = sqlledger.connect(**settings.company_conninfo(company_db))
    if not conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'").rowcount:
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('audittrail', '1')")
    conn.execute("UPDATE customer SET email = 'klant-a@example.test', bcc = 'archief@example.test' WHERE id = 900")
    yield conn
    conn.close()


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


@pytest.fixture
def reader(client, db, company, cdb):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    return client


def status(cdb):
    return {r[0]: (r[1], r[2]) for r in cdb.execute(
        "SELECT trans_id, printed, emailed FROM status WHERE formname = 'transaction'").fetchall()}


def listed(html):
    return [int(x) for x in re.findall(r'name="sel" value="(\d+)"', html)]


def post(client, html, action, sel, **extra):
    data = [(k, v) for k, v in page_fields(html) if k != "sel"]
    data += [("sel", str(i)) for i in sel] + [("csrf", csrf(client, html)), ("action", action)]
    data += list(extra.items())
    return client.post(URL, data=as_form(data))


def test_menu_kinds_and_default_filters(booker):
    home = booker.get("/").text
    # menu Verkoop/Inkoop > Documenten in batch (MN4), elk met de eigen soort voorgekozen
    assert f'href="{URL}?soort=invoice"' in home and f'href="{URL}?soort=vendor_invoice"' in home
    page = booker.get(URL).text
    # eerste keer: vorige + lopende maand, nog niet geprint en niet gemaild
    assert re.search(r'<option value="nee" selected>nee</option>', page)
    for key in ("invoice", "credit_invoice", "vendor_invoice", "packing_list", "sales_order", "sales_quotation",
                "ar_transaction", "ap_transaction", "debit_note"):
        assert f'<option value="{key}"' in page
    page = booker.get(URL, params=ALL).text
    assert listed(page) == [950, 3006, 3005, 3004, 3003, 3010, 3009]
    assert "cees@example.test" in page and "geen e-mailadres" in page
    page = booker.get(URL, params={**ALL, "staat": "open", "relatie": "cees"}).text
    assert listed(page) == [3004]


def test_module_off_hides_kinds(client, db, company, cdb):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["ar", "cash"], company["id"]))
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    page = client.get(URL).text
    assert '<option value="invoice"' in page and '<option value="sales_order"' not in page
    assert '<option value="vendor_invoice"' not in page
    # pakbon alleen uit facturen (orders staan uit)
    assert '<option value="packing_list"' in page
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["gl"], company["id"]))
    assert f'href="{URL}' not in client.get("/").text
    assert client.get(URL).status_code == 403


def test_preview_nothing_pdf_marks_printed(booker, cdb):
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "preview", [3004, 3006])
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "inline" in r.headers["content-disposition"]
    assert status(cdb) == {}
    r = post(booker, page, "pdf", [3006, 3004])
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    assert status(cdb) == {3004: (True, False), 3006: (True, False)}
    trail = cdb.execute("SELECT trans_id, action FROM audittrail WHERE formname = 'transaction' ORDER BY trans_id"
                        ).fetchall()
    assert trail == [(3004, "printed"), (3006, "printed")]
    # standaardfilter "niet geprint": ze verdwijnen uit de lijst; de lijst toont de status
    page = booker.get(URL, params={**ALL, "geprint": "nee"}).text
    assert 3004 not in listed(page) and 3005 in listed(page)
    page = booker.get(URL, params={**ALL, "geprint": "ja"}).text
    assert listed(page) == [3006, 3004]


def test_pdf_failure_records_nothing(booker, cdb, monkeypatch):
    def boom(self, docs):
        raise RuntimeError("kapot sjabloon")
    monkeypatch.setattr(DocumentRenderer, "render_many", boom)
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "pdf", [3004])
    assert r.status_code == 400 and "er is niets vastgelegd" in r.text
    assert status(cdb) == {}


def test_selection_errors(booker, monkeypatch):
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "pdf", [])
    assert r.status_code == 400 and "Vink minstens één document aan." in r.text
    # id van een andere soort (inkoop) of onbekend: niet gevonden
    r = post(booker, page, "pdf", [4001, 99999])
    assert r.status_code == 400 and "2 document(en) niet (meer) gevonden." in r.text
    monkeypatch.setattr(routes_batch, "MAX_BATCH", 2)
    r = post(booker, page, "preview", [3004, 3005, 3006])
    assert r.status_code == 400 and "maximaal 2" in r.text
    r = booker.post(URL, data={"csrf": csrf(booker, page), "action": "wis", **ALL})
    assert r.status_code == 400


def test_reader_only_preview(reader, cdb):
    page = reader.get(URL, params=ALL).text
    assert 'value="preview"' in page and 'value="pdf"' not in page and 'value="mail"' not in page
    assert post(reader, page, "preview", [3004]).content.startswith(b"%PDF")
    assert post(reader, page, "pdf", [3004]).status_code == 403
    assert post(reader, page, "mail", [3004]).status_code == 403
    assert status(cdb) == {}


def test_mail_requires_settings(booker):
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "mail", [3004])
    assert r.status_code == 400 and "E-mail is nog niet ingesteld" in r.text


def test_mail_overview_and_send(booker, db, company, cdb, settings):
    configure_mail(db, company)
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "mail", [3004, 3003, 3006])
    assert r.status_code == 200
    assert "F-3004 – Bakkerij Cees" in r.text and "cees@example.test" in r.text
    assert "F-3006 – Klant A" in r.text and "archief@example.test" in r.text
    assert "F-3003 – Overseas Inc" in r.text and "geen e-mailadres: valt af" in r.text
    assert "Verzenden (2)" in r.text and 'name="onderwerp"' in r.text and "{nummer}" in r.text
    assert status(cdb) == {}
    # eigen onderwerp en bericht voor de hele reeks; overzicht bijwerken toont het ingevulde onderwerp
    r = post(booker, r.text, "mail", [3004, 3003, 3006], onderwerp="Uw factuur {nummer}",
             bericht="Beste {relatie},\n\nHierbij factuur {nummer} van {bedrag}.")
    assert "Uw factuur F-3004" in r.text and "Uw factuur F-3006" in r.text
    assert 'value="Uw factuur {nummer}"' in r.text and "Beste {relatie}," in r.text   # blijft ingevuld
    r2 = post(booker, r.text, "mail_send", [3004, 3003, 3006], onderwerp="Uw factuur {nummer}",
              bericht="Beste {relatie},\n\nHierbij factuur {nummer} van {bedrag}.")
    assert r2.status_code == 200 and "Gemaild: 2, mislukt: 0, zonder e-mailadres: 1." in r2.text
    folder = Path(settings.data_dir) / "mail" / str(company["id"])
    msgs = [email.message_from_bytes(p.read_bytes(), policy=email.policy.default) for p in folder.glob("*.eml")]
    assert sorted(m["subject"] for m in msgs) == ["Uw factuur F-3004", "Uw factuur F-3006"]
    m = next(m for m in msgs if "cees@example.test" in m["to"])
    body = m.get_body(("plain",)).get_content()
    assert "Beste Bakkerij Cees," in body and "Hierbij factuur F-3004 van 96,80" in body
    att = list(m.iter_attachments())
    assert len(att) == 1 and att[0].get_content_type() == "application/pdf"
    assert status(cdb) == {3004: (False, True), 3006: (False, True)}
    note = cdb.execute("SELECT intnotes FROM ar WHERE id = 3004").fetchone()[0]
    assert note.startswith("[email]") and "Uw factuur F-3004" in note and "cees@example.test" in note
    assert cdb.execute("SELECT intnotes FROM ar WHERE id = 3003").fetchone()[0] in (None, "")
    trail = cdb.execute("SELECT trans_id FROM audittrail WHERE action = 'emailed' ORDER BY trans_id").fetchall()
    assert [t[0] for t in trail] == [3004, 3006]
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'batch_emailed'").fetchone()
    assert log and log["detail"]["sent"] == 2 and log["detail"]["without"] == 1


def test_mail_standard_text_and_one_failure(booker, db, company, cdb, settings):
    configure_mail(db, company)
    cdb.execute("UPDATE customer SET email = 'geen adres' WHERE id = 900")
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "mail", [3004, 3006])
    assert "Verzenden (2)" in r.text
    r2 = post(booker, r.text, "mail_send", [3004, 3006])
    assert "Gemaild: 1, mislukt: 1, zonder e-mailadres: 0." in r2.text
    assert "F-3006 – Klant A: niet gemaild" in r2.text
    assert status(cdb) == {3004: (False, True)}
    folder = Path(settings.data_dir) / "mail" / str(company["id"])
    msgs = [email.message_from_bytes(p.read_bytes(), policy=email.policy.default) for p in folder.glob("*.eml")]
    assert len(msgs) == 1 and "F-3004" in msgs[0]["subject"]          # standaardonderwerp uit Beheer > E-mail


def test_render_failure_in_mail_only_that_document(booker, db, company, cdb, monkeypatch):
    configure_mail(db, company)
    real = DocumentRenderer.render_pdf

    def flaky(self, formname, v):
        if v.get("invnumber") == "F-3006":
            raise RuntimeError("kapot")
        return real(self, formname, v)
    monkeypatch.setattr(DocumentRenderer, "render_pdf", flaky)
    page = booker.get(URL, params=ALL).text
    r = post(booker, page, "mail", [3004, 3006])
    r2 = post(booker, r.text, "mail_send", [3004, 3006])
    assert "Gemaild: 1, mislukt: 1" in r2.text and "kon niet worden opgemaakt" in r2.text
    assert status(cdb) == {3004: (False, True)}
