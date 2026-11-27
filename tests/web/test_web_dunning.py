"""Verkoop > Aanmaningen en Rekeningoverzichten (fase 4 stap 4a, SB1–SB6, SB15).

Testdata uit compare/arap_seed.sql (zie tests/test_dunning.py): op 30-06-2026 brieven voor klant 900
(F-3006 niveau 1 → 2, F-3009), 902 (F-3002, F-3004, F-3007 niveau 2 → 3) en 903 (USD).
"""

import email
import email.policy
import re
from pathlib import Path

import pytest

import sqlledger
from sqlledger_web import mailer
from sqlledger_web.mailer import MailSettings

from webhelpers import csrf, login, make_user

ASOF = "2026-06-30"


@pytest.fixture
def cdb(settings, company_db):
    conn = sqlledger.connect(**settings.company_conninfo(company_db))
    yield conn
    conn.close()


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    cdb.execute("UPDATE customer SET email = 'koek@example.test', cc = 'cc@example.test' WHERE id = 900")
    login(client, "boek")
    return client


@pytest.fixture
def reader(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    return client


def levels(cdb):
    return dict(cdb.execute("SELECT trans_id, formname FROM status WHERE formname LIKE 'reminder_' "
                            "ORDER BY trans_id").fetchall())


def page_fields(html):
    """Alle invoervelden van het werkscherm zoals de browser ze verstuurt (aangevinkt, verborgen, keuzes).
    De facturen/klanten (item, st) staan niet vooraf aangevinkt: hier als na "alles selecteren"."""
    data = []
    for m in re.finditer(r'<input type="(hidden|checkbox)" name="([^"]+)" value="([^"]*)"([^>]*)>', html):
        kind, name, value, rest = m.groups()
        if name == "csrf" or (kind == "checkbox" and "checked" not in rest and name not in ("item", "st")):
            continue
        data.append((name, value))
    for m in re.finditer(r'<select name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        sel = re.search(r'<option value="([^"]*)" selected', m.group(2))
        data.append((m.group(1), sel.group(1) if sel else ""))
    return data


def as_form(pairs):
    """httpx wil een dict; herhaalde namen (item, buckets) als lijst."""
    out = {}
    for k, v in pairs:
        out.setdefault(k, []).append(v)
    return out


def submit(client, url, html, action, drop=(), extra=()):
    data = [(k, v) for k, v in page_fields(html) if (k, v) not in drop and k not in drop]
    data += [("csrf", csrf(client, html)), ("action", action), *extra]
    return client.post(url, data=as_form(data))


def configure_mail(db, company):
    mailer.save(db, MailSettings(company["id"], mode="folder", from_name="Testbedrijf",
                                 from_address="debiteuren@bedrijf.nl"))


def test_reminder_page_lists_letters(booker):
    page = booker.get("/aanmaningen", params={"asof": ASOF}).text
    assert "F-3006" in page and "F-3007" in page and "F-3001" not in page
    assert "Tweede herinnering" in page and "Aanmaning" in page
    assert 'name="lvl_3007" value="3"' in page and 'name="lvl_3006" value="2"' in page
    assert "koek@example.test" in page and "geen e-mailadres" in page
    assert 'href="/aanmaningen"' in booker.get("/").text
    # niets vooraf aangevinkt (wens gebruiker 2026-10-11), wel "alles selecteren"
    assert not re.search(r'name="item" value="\d+" checked', page)
    assert 'data-check-all="item"' in page
    st = booker.get("/rekeningoverzichten", params={"asof": ASOF}).text
    assert not re.search(r'name="st" value="[^"]+" checked', st) and 'data-check-all="st"' in st


def test_preview_records_nothing_and_pdf_records_level(booker, cdb):
    page = booker.get("/aanmaningen", params={"asof": ASOF}).text
    before = levels(cdb)
    r = submit(booker, "/aanmaningen", page, "preview")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "inline" in r.headers["content-disposition"]
    assert levels(cdb) == before
    # alleen klant 900: F-3002/F-3004/F-3007 (902) en F-3003 (903) uitgevinkt
    drop = [("item", "3002"), ("item", "3004"), ("item", "3007"), ("item", "3003")]
    r = submit(booker, "/aanmaningen", page, "pdf", drop=drop)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert levels(cdb) == {3006: "reminder2", 3007: "reminder2", 3009: "reminder1"}
    printed = cdb.execute("SELECT printed FROM status WHERE trans_id = 3006 AND formname = 'reminder2'").fetchone()
    assert printed[0] is True
    # nog een keer vanaf dezelfde (oude) pagina: zelfde niveau, niet nog eens omhoog
    submit(booker, "/aanmaningen", page, "pdf", drop=drop)
    assert levels(cdb)[3006] == "reminder2"


def test_letter_with_only_credit_left_is_refused(booker, cdb):
    cdb.execute("""INSERT INTO ar (id, invnumber, transdate, duedate, customer_id, netamount, amount, paid, invoice,
                                   curr, approved) VALUES (3100, 'C-3100', '2026-06-28', '2026-06-28', 900, -20, -20, 0,
                                   false, 'EUR', true)""")
    cdb.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                   VALUES (3100, (SELECT id FROM chart WHERE accno = '1200'), 20, '2026-06-28', true)""")
    page = booker.get("/aanmaningen", params={"asof": ASOF}).text
    assert "C-3100" in page and "te verrekenen" in page
    keep = {"3009"}
    drop = [("item", x) for x in ("3002", "3004", "3007", "3003", "3006") if x not in keep]
    r = submit(booker, "/aanmaningen", page, "pdf", drop=drop)
    assert r.status_code == 400 and "Geen aanmaning" in r.text
    assert 3009 not in levels(cdb)


def test_manual_levels(booker, cdb):
    page = booker.get("/aanmaningen", params={"asof": ASOF}).text
    data = [(k, v) for k, v in page_fields(page) if not k.startswith("set_")]
    data += [("set_3006", "0"), ("set_3007", "2"), ("set_3002", "3"), ("set_3004", "0"), ("set_3009", "0"),
             ("set_3003", "0"), ("csrf", csrf(booker, page)), ("action", "levels")]
    r = booker.post("/aanmaningen", data=as_form(data))
    assert r.status_code == 200 and "Aanmaanniveau opgeslagen (2 facturen)." in r.text
    assert levels(cdb) == {3002: "reminder3", 3007: "reminder2"}


def test_mail_confirm_and_send_folder_mode(booker, db, company, cdb, settings):
    configure_mail(db, company)
    page = booker.get("/aanmaningen", params={"asof": ASOF}).text
    r = submit(booker, "/aanmaningen", page, "mail")
    assert r.status_code == 200
    assert "koek@example.test" in r.text and "Overseas Inc" in r.text and "geen e-mailadres: valt af" in r.text
    assert "Verzenden (2)" in r.text            # 900 en 902 (cees@example.test); 903 heeft geen adres
    r2 = submit(booker, "/aanmaningen", r.text, "mail_send")
    assert r2.status_code == 200 and "Gemaild: 2, mislukt: 0, zonder e-mailadres: 1." in r2.text
    folder = Path(settings.data_dir) / "mail" / str(company["id"])
    msgs = [email.message_from_bytes(p.read_bytes(), policy=email.policy.default) for p in folder.glob("*.eml")]
    subjects = sorted(m["subject"].split(" openstaande")[0] for m in msgs)
    assert subjects == ["Aanmaning", "Tweede herinnering"]
    m900 = next(m for m in msgs if "koek@example.test" in m["to"])
    assert m900["cc"] == "cc@example.test"
    att = [p for p in m900.iter_attachments()]
    assert att and att[0].get_content_type() == "application/pdf"
    rows = dict(((t, f), e) for t, f, e in cdb.execute(
        "SELECT trans_id, formname, emailed FROM status WHERE formname LIKE 'reminder_'").fetchall())
    assert rows[(3006, "reminder2")] is True and rows[(3007, "reminder3")] is True
    assert (3003, "reminder1") not in rows       # niet gemaild (geen adres): niets vastgelegd


def test_mail_requires_settings(booker):
    page = booker.get("/aanmaningen", params={"asof": ASOF}).text
    r = submit(booker, "/aanmaningen", page, "mail")
    assert r.status_code == 400 and "E-mail is nog niet ingesteld" in r.text


def test_reader_only_preview(reader, cdb):
    page = reader.get("/aanmaningen", params={"asof": ASOF}).text
    assert 'value="preview"' in page and 'value="pdf"' not in page and "set_3006" not in page
    r = submit(reader, "/aanmaningen", page, "preview")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = submit(reader, "/aanmaningen", page, "pdf")
    assert r.status_code == 403
    assert levels(cdb) == {3006: "reminder1", 3007: "reminder2"}


def test_statements_page_pdf_and_mail(booker, db, company, cdb):
    if not cdb.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'").rowcount:
        cdb.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('audittrail', '1')")
    page = booker.get("/rekeningoverzichten", params={"asof": ASOF, "buckets": ["c0", "c30", "c60", "c90"]}).text
    assert "Bakkerij Cees" in page and "Overseas Inc" in page and "384,70" in page
    assert "0–30" in page and "&gt; 90" in page
    r = submit(booker, "/rekeningoverzichten", page, "preview")
    assert r.content.startswith(b"%PDF")
    r = submit(booker, "/rekeningoverzichten", page, "pdf", drop=[("st", "903-USD")])
    assert r.content.startswith(b"%PDF")
    trail = cdb.execute("SELECT trans_id, formname, action FROM audittrail WHERE formname = 'statement' "
                        "ORDER BY trans_id").fetchall()
    assert [t[0] for t in trail] == [900, 902]
    configure_mail(db, company)
    r = submit(booker, "/rekeningoverzichten", page, "mail")
    assert "Verzenden (2)" in r.text
    r2 = submit(booker, "/rekeningoverzichten", r.text, "mail_send")
    assert "Gemaild: 2, mislukt: 0, zonder e-mailadres: 1." in r2.text


def test_statement_historical_asof(booker):
    # vóór de deelbetaling van 20-06 staat F-3002 nog volledig open: 272,50 + 96,80 + 65,40
    page = booker.get("/rekeningoverzichten", params={"asof": "2026-06-15", "buckets": ["c0", "c30", "c60", "c90"]}).text
    assert "434,70" in page


def test_admin_screens_know_letters(client, db, company):
    secret = make_user(db, "beheer", totp=True, memberships=[(company["id"], "admin", "admin")])
    login(client, "beheer", secret)
    assert "reminder2" in client.get("/beheer/email").text
    tpl = client.get("/beheer/sjablonen").text
    assert "Aanmaning niveau 3" in tpl and "_reminder" in tpl
    # vandaag staat er in de testdata van alles open (facturen van 2026): voorbeeld van brief en gedeeld deel
    for name in ("statement", "reminder3", "_reminder"):
        r = client.get("/beheer/sjablonen/voorbeeld", params={"naam": name})
        assert r.status_code == 200 and r.content.startswith(b"%PDF"), name
    ds = client.get("/beheer/documentinstellingen").text
    assert 'name="intro_reminder1"' in ds and 'name="intro_invoice"' not in ds
