"""Herhalen met mailen en PDF (fase 4 stap 4d, SB12–SB13).

Testdata uit sl_template: verkoop zonder artikelen 3006 (klant 900, krijgt in de fixture een e-mailadres),
3004 (902 Bakkerij Cees, heeft een e-mailadres), 3003 (903, USD, geen e-mailadres); factuur met artikelen 3001;
memoriaal 20101; inkoop 4002.
"""

import email
import email.policy
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import sqlledger
from sqlledger_web.documents import DocumentRenderer

from test_web_dunning import as_form, configure_mail, page_fields
from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    conn = sqlledger.connect(**settings.company_conninfo(company_db))
    if not conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'").rowcount:
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('audittrail', '1')")
    conn.execute("UPDATE customer SET email = 'klant-a@example.test' WHERE id = 900")
    yield conn
    conn.close()


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def save(client, tid, **data):
    page = client.get(f"/herhalen/{tid}").text
    values = [("reference", ""), ("description", ""), ("startdate", ""), ("repeat", "1"), ("unit", "months"),
              ("howmany", ""), ("action", "save"), ("csrf", csrf(client, page))]
    for k, v in data.items():
        values += [(k, x) for x in v] if isinstance(v, list) else [(k, v)]
    return client.post(f"/herhalen/{tid}", data=as_form(values), follow_redirects=False)


def outputs(cdb, tid):
    e = cdb.execute("SELECT formname, format, message FROM recurringemail WHERE id = %s ORDER BY formname",
                    (tid,)).fetchall()
    p = cdb.execute("SELECT formname, format, printer FROM recurringprint WHERE id = %s ORDER BY formname",
                    (tid,)).fetchall()
    return [tuple(r) for r in e], [tuple(r) for r in p]


def process(client, *ids):
    page = client.get("/terugkerend").text
    fields = [(k, v) for k, v in page_fields(page) if k != "sel"] + [("sel", str(i)) for i in ids]
    fields.append(("csrf", csrf(client, page)))
    return client.post("/terugkerend", data=as_form(fields))


def text(r):
    return re.sub(r"\s+", " ", r.text)


def mails(settings, company):
    folder = Path(settings.data_dir) / "mail" / str(company["id"])
    return [email.message_from_bytes(p.read_bytes(), policy=email.policy.default) for p in folder.glob("*.eml")]


def new_id(cdb, table, orig):
    return cdb.execute(f"SELECT max(id) FROM {table} WHERE id <> %s", (orig,)).fetchone()[0]


def late(days=2):
    return (date.today() - timedelta(days=days)).isoformat()


# --- schemascherm (SB13) ----------------------------------------------------------------

def test_choices_per_booking(booker):
    page = booker.get("/herhalen/3001").text                                        # factuur met artikelen
    for name in ("email", "print"):
        found = re.findall(rf'name="{name}" value="([a-z_]+)"', page)
        assert found == ["invoice", "packing_list", "pick_list"]
    assert 'name="message"' in page and "{nummer}" in page
    page = booker.get("/herhalen/3006").text                                        # zonder artikelen
    assert re.findall(r'name="email" value="([a-z_]+)"', page) == ["transaction"]
    page = booker.get("/herhalen/4002").text                                        # inkoop
    assert re.findall(r'name="print" value="([a-z_]+)"', page) == ["transaction"]
    page = booker.get("/herhalen/20101").text                                       # memoriaal
    assert 'name="email"' not in page and "geen document om te mailen" in page


def test_save_outputs_like_sl(booker, cdb):
    r = save(booker, 3001, startdate="2027-01-01", howmany="3", email=["invoice", "work_order"],
             print=["packing_list", "invoice"], message="Beste {relatie}, hierbij {nummer}.")
    assert r.status_code == 303
    assert outputs(cdb, 3001) == ([("invoice", "pdf", "Beste {relatie}, hierbij {nummer}.")],
                                  [("invoice", "pdf", ""), ("packing_list", "pdf", "")])
    page = booker.get("/herhalen/3001").text
    assert re.search(r'name="email" value="invoice" checked', page)
    assert re.search(r'name="print" value="pick_list"(?! checked)', page)
    assert "Beste {relatie}, hierbij {nummer}." in page
    assert "hier niet uitgevoerd" not in page                                     # alles bekend
    # alles uitvinken wist mailen en PDF (het bericht hoort bij mailen en verdwijnt mee)
    save(booker, 3001, startdate="2027-01-01", howmany="3", message="los bericht")
    assert outputs(cdb, 3001) == ([], [])


def test_perl_outputs_kept(booker, cdb):
    save(booker, 3001, startdate="2027-01-01", howmany="3", print=["invoice"])
    cdb.execute("UPDATE recurringprint SET printer = 'kantoor', format = 'postscript' WHERE id = 3001")
    cdb.execute("INSERT INTO recurringprint (id, formname, format, printer) VALUES (3001, 'work_order', 'pdf', 'lp')")
    page = booker.get("/herhalen/3001").text
    assert "hier niet uitgevoerd: work_order" in page
    save(booker, 3001, startdate="2027-01-01", howmany="3", print=["invoice"], email=["invoice"])
    assert outputs(cdb, 3001)[1] == [("invoice", "pdf", "kantoor"), ("work_order", "pdf", "lp")]
    assert "work_order" in text(booker.get("/terugkerend"))


# --- verwerken: mailen (SB12) -------------------------------------------------------------

def test_process_mails_new_document(booker, db, company, cdb, settings):
    configure_mail(db, company)
    save(booker, 3006, startdate=late(), howmany="2", email=["transaction"], message="Beste {relatie},\n\nFactuur {nummer}.")
    orig = cdb.execute("SELECT formname, printed, emailed FROM status WHERE trans_id = 3006").fetchall()
    assert "mailen: Factuur" in text(booker.get("/terugkerend"))
    r = process(booker, 3006)
    t = text(r)
    assert "Verwerkt: 1, overgeslagen: 0" in t
    new = new_id(cdb, "ar", 3006)
    number = cdb.execute("SELECT invnumber FROM ar WHERE id = %s", (new,)).fetchone()[0]
    assert f"Factuur {number} gemaild aan klant-a@example.test" in t
    msgs = mails(settings, company)
    assert len(msgs) == 1 and number in msgs[0]["subject"]
    body = msgs[0].get_body(("plain",)).get_content()
    assert "Beste Klant A," in body and f"Factuur {number}." in body
    assert [a.get_content_type() for a in msgs[0].iter_attachments()] == ["application/pdf"]
    st = cdb.execute("SELECT printed, emailed FROM status WHERE trans_id = %s AND formname = 'transaction'",
                     (new,)).fetchone()
    assert tuple(st) == (False, True)
    note = cdb.execute("SELECT intnotes FROM ar WHERE id = %s", (new,)).fetchone()[0]
    assert note.startswith("[email]") and "klant-a@example.test" in note
    assert cdb.execute("SELECT nextdate FROM recurring WHERE id = 3006").fetchone()[0] > date.today()
    # het origineel is niet opnieuw gemaild
    assert cdb.execute("SELECT formname, printed, emailed FROM status WHERE trans_id = 3006").fetchall() == orig


def test_mail_failure_keeps_booking_and_moves_schedule(booker, db, company, cdb, settings):
    configure_mail(db, company)
    cdb.execute("UPDATE customer SET email = 'geen adres' WHERE id = 900")
    save(booker, 3006, startdate=late(), howmany="3", email=["transaction"])
    save(booker, 3003, startdate=late(), howmany="3", email=["transaction"])     # 903: geen e-mailadres
    cdb.execute("INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', %s, 1.1)", (late(),))
    before = {i: cdb.execute("SELECT nextdate FROM recurring WHERE id = %s", (i,)).fetchone()[0] for i in (3003, 3006)}
    r = process(booker, 3006, 3003)
    t = text(r)
    assert "Verwerkt: 2, overgeslagen: 0" in t
    assert "niet gemaild: de relatie heeft geen e-mailadres" in t
    assert re.search(r"Factuur \S+ niet gemaild: ", t)
    for i in (3003, 3006):
        assert cdb.execute("SELECT nextdate FROM recurring WHERE id = %s", (i,)).fetchone()[0] > before[i]
    assert cdb.execute("SELECT count(*) FROM status WHERE emailed").fetchone()[0] == 0
    assert mails(settings, company) == []
    actions = [r["action"] for r in db.execute("SELECT action FROM audit_log ORDER BY id").fetchall()]
    assert "letter_email_failed" in actions and "recurring_email_failed" in actions


def test_mail_not_configured(booker, cdb):
    save(booker, 3006, startdate=late(), howmany="2", email=["transaction"])
    t = text(process(booker, 3006))
    assert "Verwerkt: 1" in t and "niet gemaild: E-mail is nog niet ingesteld" in t


def test_render_failure_only_that_mail(booker, db, company, cdb, monkeypatch):
    configure_mail(db, company)

    def boom(self, formname, v):
        raise RuntimeError("kapot")
    monkeypatch.setattr(DocumentRenderer, "render_pdf", boom)
    save(booker, 3006, startdate=late(), howmany="2", email=["transaction"])
    t = text(process(booker, 3006))
    assert "Verwerkt: 1" in t and "kon niet worden opgemaakt" in t


# --- verwerken: PDF (SB12) ----------------------------------------------------------------

def _pdf_form(html):
    m = re.search(r'<form method="post" action="/terugkerend/pdf".*?</form>', html, re.S)
    assert m, "geen PDF-knop"
    return re.findall(r'name="doc" value="([^"]+)"', m.group(0))


def test_process_pdf_button(booker, cdb):
    save(booker, 3006, startdate=late(), howmany="2", print=["transaction"])
    save(booker, 3004, startdate=late(), howmany="2", print=["transaction"])
    r = process(booker, 3006, 3004)
    docs = _pdf_form(r.text)
    assert len(docs) == 2 and all(d.startswith("ar_transaction:") for d in docs)
    assert "PDF van de nieuwe documenten (2)" in r.text
    assert cdb.execute("SELECT count(*) FROM status WHERE printed").fetchone()[0] == 0     # nog niets
    data = as_form([("doc", d) for d in docs] + [("csrf", csrf(booker, r.text))])
    pdf = booker.post("/terugkerend/pdf", data=data)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "attachment" in pdf.headers["content-disposition"]
    ids = sorted(int(d.split(":")[1]) for d in docs)
    rows = cdb.execute("SELECT trans_id, printed, emailed FROM status WHERE formname = 'transaction' ORDER BY 1").fetchall()
    assert [tuple(x) for x in rows] == [(i, True, False) for i in ids]


def test_pdf_failure_records_nothing(booker, cdb, monkeypatch):
    save(booker, 3006, startdate=late(), howmany="2", print=["transaction"])
    r = process(booker, 3006)
    docs = _pdf_form(r.text)
    assert cdb.execute("SELECT nextdate FROM recurring WHERE id = 3006").fetchone()[0] > date.today()

    def boom(self, items):
        raise RuntimeError("kapot")
    monkeypatch.setattr(DocumentRenderer, "render_many", boom)
    resp = booker.post("/terugkerend/pdf", data=as_form([("doc", d) for d in docs] + [("csrf", csrf(booker, r.text))]))
    assert resp.status_code == 400 and "PDF kon niet worden gemaakt" in resp.text
    assert not cdb.execute("SELECT 1 FROM status WHERE trans_id = %s", (int(docs[0].split(":")[1]),)).fetchone()


def test_pdf_post_checks(booker, client, db, company, cdb):
    page = booker.get("/terugkerend").text
    r = booker.post("/terugkerend/pdf", data=as_form([("doc", "invoice:3006"), ("doc", "nonsense"),
                                                       ("csrf", csrf(booker, page))]))
    assert r.status_code == 400 and "geen documenten" in r.text          # 3006 is geen factuur met artikelen
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    home = client.get("/").text
    r = client.post("/terugkerend/pdf", data={"doc": "ar_transaction:3006", "csrf": csrf(client, home)})
    assert r.status_code == 403


def test_invoice_mail_and_pdf_together(booker, db, company, cdb, settings):
    configure_mail(db, company)
    cdb.execute("UPDATE customer SET email = 'factuur@example.test' WHERE id = (SELECT customer_id FROM ar WHERE id = 3001)")
    save(booker, 3001, startdate=late(), howmany="2", email=["invoice"], print=["invoice", "packing_list"])
    r = process(booker, 3001)
    t = text(r)
    assert "Verwerkt: 1" in t
    docs = _pdf_form(r.text)
    new = new_id(cdb, "ar", 3001)
    assert docs == [f"invoice:{new}", f"packing_list:{new}"]
    number = cdb.execute("SELECT invnumber FROM ar WHERE id = %s", (new,)).fetchone()[0]
    assert f"Factuur {number} gemaild aan" in t
    msgs = mails(settings, company)
    assert len(msgs) == 1 and number in msgs[0]["subject"]
    st = cdb.execute("SELECT formname, printed, emailed FROM status WHERE trans_id = %s", (new,)).fetchall()
    assert [tuple(x) for x in st] == [("invoice", False, True)]


def test_gl_schedule_without_outputs_unchanged(booker, cdb):
    save(booker, 20101, startdate=late(), howmany="2")
    r = process(booker, 20101)
    assert "Verwerkt: 1" in text(r) and "/terugkerend/pdf" not in r.text
