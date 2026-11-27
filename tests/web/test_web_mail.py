"""Documenten mailen (fase 4 stap 1, D9): Beheer > E-mail, mailscherm, nep-SMTP-server en mapmodus."""

import email
import email.policy
import re
import socket

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

from sqlledger_web import mailer
from sqlledger_web.mailer import MailSettings

from test_web_arap import step
from test_web_is import add_item, cdb, posted_id, setup, start  # noqa: F401 (fixtures)
from webhelpers import csrf, login, make_user, post


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos),
                              email.message_from_bytes(envelope.content, policy=email.policy.default)))
        return "250 OK"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp():
    inbox = Inbox()
    inbox.port = free_port()
    ctl = Controller(inbox, hostname="127.0.0.1", port=inbox.port)
    ctl.start()
    yield inbox
    ctl.stop()


@pytest.fixture
def booker(client, db, company, settings, tmp_path, cdb):
    settings.data_dir = str(tmp_path)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    db.execute("UPDATE app_user SET email = 'jan@bedrijf.nl', mail_signature = 'Jan Jansen\nBoekhouding' "
               "WHERE login = 'boek'")
    cdb.execute("UPDATE customer SET email = 'klant@example.com', cc = 'cc@example.com', bcc = 'archief@example.com' "
                "WHERE customernumber = 'K001'")
    login(client, "boek")
    return client


def configure(db, company, **kw):
    ms = MailSettings(company["id"], **{"mode": "folder", "from_name": "Testbedrijf", "from_address": "factuur@bedrijf.nl",
                                        **kw})
    mailer.save(db, ms)
    return ms


def make_invoice(client) -> int:
    page = start(client, "K001")
    page = add_item(client, page, "A-1", qty="2")
    return posted_id(step(client, "/verkoopfactuur", {"action": "post"}, page))


def form_fields(html):
    out = {k: v for k, v in re.findall(r'<input name="(\w+)" value="([^"]*)"', html)}
    m = re.search(r'<textarea name="message"[^>]*>\n?(.*?)</textarea>', html, re.S)
    out["message"] = m.group(1) if m else ""
    return out


# ------------------------------------------------------------------ mapmodus: alles behalve het versturen

def test_mail_invoice_folder_mode(booker, db, cdb, company, tmp_path):
    configure(db, company)
    sid = make_invoice(booker)
    page = booker.get(f"/verkoopfactuur/{sid}").text
    assert f'href="/document/invoice/{sid}/mail"' in page and "Gemaild: nee" in page
    html = booker.get(f"/document/invoice/{sid}/mail").text
    f = form_fields(html)
    inv = cdb.execute("SELECT invnumber, amount FROM ar WHERE id = %s", (sid,)).fetchone()
    assert f["to"] == "klant@example.com" and f["cc"] == "cc@example.com" and f["bcc"] == "archief@example.com"
    assert f["subject"] == f"Factuur {inv['invnumber']} van Testbedrijf BV"
    assert f["message"].startswith("Beste ") and f["message"].endswith("-- \nJan Jansen\nBoekhouding")
    assert "Testmodus" in html
    r = booker.post(f"/document/invoice/{sid}/mail", data={**f, "csrf": csrf(booker, html), "action": "send"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/verkoopfactuur/{sid}?ok=document_emailed"
    files = list((tmp_path / "mail" / str(company["id"])).glob("*.eml"))
    assert len(files) == 1
    msg = email.message_from_bytes(files[0].read_bytes(), policy=email.policy.default)
    assert msg["From"] == "Testbedrijf <factuur@bedrijf.nl>" and msg["To"] == "klant@example.com"
    assert msg["Reply-To"] == "jan@bedrijf.nl"
    att = [p for p in msg.iter_attachments()]
    assert len(att) == 1 and att[0].get_content_type() == "application/pdf"
    assert att[0].get_filename() == f"Factuur-{inv['invnumber']}.pdf" and att[0].get_content()[:5] == b"%PDF-"
    # status, interne notitie, audittrail, logboek (zoals io.pl print_form)
    assert cdb.execute("SELECT printed, emailed FROM status WHERE trans_id = %s AND formname = 'invoice'",
                       (sid,)).fetchone() == {"printed": False, "emailed": True}
    notes = cdb.execute("SELECT intnotes FROM ar WHERE id = %s", (sid,)).fetchone()["intnotes"]
    assert notes.startswith("[email]\nDatum: ") and "Aan: klant@example.com\nCc: cc@example.com\nBcc: archief@example.com" in notes
    assert f"Onderwerp: Factuur {inv['invnumber']} van Testbedrijf BV\n\nBericht: Beste " in notes and "Jan Jansen" not in notes
    assert cdb.execute("SELECT count(*) AS n FROM audittrail WHERE trans_id = %s AND formname = 'invoice' "
                       "AND action = 'emailed'", (sid,)).fetchone()["n"] == 1
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'document_emailed'").fetchone()["detail"]
    assert log["to"] == ["klant@example.com"] and log["via"].startswith("folder:")
    page = booker.get(f"/verkoopfactuur/{sid}?ok=document_emailed").text
    assert "E-mail verzonden." in page and "Gemaild: ja" in page
    # tweede keer: notitie eronder met een lege regel ertussen
    booker.post(f"/document/invoice/{sid}/mail", data={**f, "message": "", "csrf": csrf(booker, html)})
    notes = cdb.execute("SELECT intnotes FROM ar WHERE id = %s", (sid,)).fetchone()["intnotes"]
    assert notes.count("[email]") == 2 and "\n\n[email]\n" in notes and notes.endswith("Bericht: verzonden")


def test_preview_link_does_not_mark_printed(booker, db, cdb, company):
    configure(db, company)
    sid = make_invoice(booker)
    html = booker.get(f"/document/invoice/{sid}/mail").text
    assert f'href="/document/invoice/{sid}.pdf?voorbeeld=1"' in html
    assert booker.get(f"/document/invoice/{sid}.pdf?voorbeeld=1").status_code == 200
    assert cdb.execute("SELECT count(*) AS n FROM status WHERE trans_id = %s", (sid,)).fetchone()["n"] == 0


# ------------------------------------------------------------------ SMTP

def test_mail_via_smtp(booker, db, cdb, company, smtp):
    configure(db, company, mode="smtp", host="127.0.0.1", port=smtp.port, security="none", reply_to="info@bedrijf.nl")
    sid = make_invoice(booker)
    html = booker.get(f"/document/invoice/{sid}/mail").text
    f = form_fields(html)
    r = post(booker, f"/document/invoice/{sid}/mail",
             {**f, "to": "Klant BV <klant@example.com>; tweede@example.com", "subject": "Uw factuur — €",
              "action": "send"}, follow_redirects=False)
    assert r.status_code == 303
    (mail_from, rcpt, msg), = smtp.messages
    assert mail_from == "factuur@bedrijf.nl"
    assert sorted(rcpt) == ["archief@example.com", "cc@example.com", "klant@example.com", "tweede@example.com"]
    assert "Bcc" not in msg and msg["Cc"] == "cc@example.com"            # bcc alleen in de envelop
    assert msg["Subject"] == "Uw factuur — €" and msg["Reply-To"] == "info@bedrijf.nl"
    assert msg["To"] == "Klant BV <klant@example.com>, tweede@example.com"
    assert msg.get_body(("plain",)).get_content().rstrip().endswith("Boekhouding")


def test_smtp_errors_leave_no_trace(booker, db, cdb, company):
    configure(db, company, mode="smtp", host="127.0.0.1", port=1, security="none")      # niemand luistert
    sid = make_invoice(booker)
    html = booker.get(f"/document/invoice/{sid}/mail").text
    r = post(booker, f"/document/invoice/{sid}/mail", {**form_fields(html), "action": "send"})
    assert r.status_code == 400 and "Verzenden mislukt" in r.text
    assert 'value="klant@example.com"' in r.text                        # ingevulde velden blijven staan
    assert cdb.execute("SELECT count(*) AS n FROM status WHERE trans_id = %s", (sid,)).fetchone()["n"] == 0
    assert not cdb.execute("SELECT intnotes FROM ar WHERE id = %s", (sid,)).fetchone()["intnotes"]
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'document_email_failed'").fetchone()["n"] == 1


def test_smtp_login_refused(booker, db, company, settings):
    class Refuse:
        def __call__(self, server, session, envelope, mechanism, auth_data):
            return AuthResult(success=False, handled=False)

    inbox = Inbox()
    port = free_port()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port, authenticator=Refuse(), auth_require_tls=False)
    ctl.start()
    try:
        configure(db, company, mode="smtp", host="127.0.0.1", port=port, security="none", username="u",
                  password_enc=mailer.encrypt(settings, "geheim"))
        sid = make_invoice(booker)
        html = booker.get(f"/document/invoice/{sid}/mail").text
        r = post(booker, f"/document/invoice/{sid}/mail", {**form_fields(html), "action": "send"})
        assert r.status_code == 400 and "weigert de gebruikersnaam of het wachtwoord" in r.text
        assert not inbox.messages
    finally:
        ctl.stop()


# ------------------------------------------------------------------ controles en rechten

@pytest.mark.parametrize("field,value,error", [
    ("to", "", "Vul een e-mailadres in bij Aan."),
    ("to", "geen-adres", "Ongeldig e-mailadres: geen-adres"),
    ("cc", "a@b.nl\r\nBcc: x@y.nl", "Regeleinden zijn niet toegestaan"),
    ("subject", "Factuur\r\nBcc: x@y.nl", "Regeleinden zijn niet toegestaan"),
    ("subject", "", "Vul een onderwerp in."),
    ("to", ", ".join(f"a{i}@b.nl" for i in range(25)), "Te veel ontvangers."),
])
def test_invalid_input(booker, db, cdb, company, tmp_path, field, value, error):
    configure(db, company)
    sid = make_invoice(booker)
    html = booker.get(f"/document/invoice/{sid}/mail").text
    r = post(booker, f"/document/invoice/{sid}/mail", {**form_fields(html), field: value, "action": "send"})
    assert r.status_code == 400 and error in r.text
    assert not (tmp_path / "mail").exists()


def test_not_configured_and_roles(booker, client, db, cdb, company):
    sid = make_invoice(booker)
    html = booker.get(f"/document/invoice/{sid}/mail").text
    assert "E-mail is niet ingesteld voor deze administratie." in html and "Vraag de beheerder" in html
    r = post(booker, f"/document/invoice/{sid}/mail", {**form_fields(html), "action": "send"})
    assert r.status_code == 400 and "niet ingesteld" in r.text
    assert booker.get(f"/document/vendor_invoice/{sid}/mail").status_code == 404      # is een verkoopfactuur
    assert booker.get(f"/document/onzin/{sid}/mail").status_code == 404
    client.cookies.clear()
    make_user(db, "lees", memberships=[(company["id"], "readonly", "lees")])
    login(client, "lees")
    assert client.get(f"/document/invoice/{sid}/mail").status_code == 403
    assert f"/document/invoice/{sid}/mail" not in client.get(f"/verkoopfactuur/{sid}").text


def test_customer_without_email(booker, db, cdb, company):
    configure(db, company)
    cdb.execute("UPDATE customer SET email = '', cc = '', bcc = '' WHERE customernumber = 'K001'")
    sid = make_invoice(booker)
    html = booker.get(f"/document/invoice/{sid}/mail").text
    assert "heeft geen e-mailadres" in html and form_fields(html)["to"] == ""


def test_english_document_texts(booker, db, cdb, company):
    configure(db, company)
    db.execute("UPDATE company SET doc_language = 'en' WHERE id = %s", (company["id"],))
    sid = make_invoice(booker)
    f = form_fields(booker.get(f"/document/invoice/{sid}/mail").text)
    assert f["subject"].startswith("Invoice ") and f["message"].startswith("Dear ")


# ------------------------------------------------------------------ Beheer > E-mail

@pytest.fixture
def admin(client, db, company, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "admin")])
    db.execute("UPDATE app_user SET email = 'baas@bedrijf.nl' WHERE login = 'baas'")
    login(client, "baas", secret)
    return client


def test_settings_page(admin, db, company, settings, tmp_path, smtp):
    assert "/beheer/email" in admin.get("/").text
    base = {"mode": "smtp", "host": "127.0.0.1", "port": str(smtp.port), "security": "none",
            "from_name": "Testbedrijf", "from_address": "factuur@bedrijf.nl", "lang": "nl", "action": "save"}
    r = post(admin, "/beheer/email", {**base, "username": "u", "password": "geheim", "host": "smtp.example.com"})
    assert r.status_code == 400 and "Inloggen zonder versleuteling" in r.text
    assert post(admin, "/beheer/email", {**base, "from_address": "x"}).status_code == 400
    r = post(admin, "/beheer/email", {**base, "subject_invoice": "Rekening {nummer}",
                                      "body_invoice": mailer.DEFAULT_TEXTS["nl"]["credit_invoice"]["body"],
                                      "subject_credit_invoice": mailer.DEFAULT_TEXTS["nl"]["credit_invoice"]["subject"]},
             follow_redirects=False)
    assert r.status_code == 303
    ms = mailer.load(db, company["id"])
    assert ms.mode == "smtp" and ms.port == smtp.port
    # alleen afwijkende teksten worden bewaard
    assert ms.texts == {"nl": {"invoice": {"subject": "Rekening {nummer}",
                                           "body": mailer.DEFAULT_TEXTS["nl"]["credit_invoice"]["body"]}}}
    # wachtwoord versleuteld, nooit terug in de pagina
    post(admin, "/beheer/email", {**base, "security": "starttls", "username": "u", "password": "heel-geheim"})
    ms = mailer.load(db, company["id"])
    assert ms.password_enc and "heel-geheim" not in ms.password_enc
    assert mailer.decrypt(settings, ms.password_enc) == "heel-geheim"
    assert (tmp_path / "secret.key").stat().st_mode & 0o077 == 0
    page = admin.get("/beheer/email").text
    assert "heel-geheim" not in page and ms.password_enc not in page and "Opgeslagen wachtwoord wissen" in page
    post(admin, "/beheer/email", {**base, "security": "starttls", "username": "u"})          # leeg = houden
    assert mailer.load(db, company["id"]).password_enc == ms.password_enc
    post(admin, "/beheer/email", {**base, "clear_password": "1"})
    assert mailer.load(db, company["id"]).password_enc == ""
    # testmail naar het eigen adres
    r = post(admin, "/beheer/email", {"action": "test", "lang": "nl"})
    assert r.status_code == 200 and "Testmail verstuurd naar baas@bedrijf.nl." in r.text
    (__, rcpt, msg), = smtp.messages
    assert rcpt == ["baas@bedrijf.nl"] and msg["Subject"] == "Testmail van Testbedrijf"


def test_settings_admin_only(booker):
    assert booker.get("/beheer/email").status_code == 403


def test_signature_in_my_settings(booker, db):
    page = booker.get("/instellingen").text
    assert "Jan Jansen\nBoekhouding</textarea>" in page
    post(booker, "/instellingen", {"action": "profile", "name": "Jan", "email": "jan@bedrijf.nl",
                                   "numberformat": "1.000,00", "dateformat": "dd-mm-yy", "language": "nl",
                                   "mail_signature": "Groet,\r\nJan"})
    assert db.execute("SELECT mail_signature FROM app_user WHERE login = 'boek'").fetchone()["mail_signature"] == "Groet,\nJan"


# ------------------------------------------------------------------ eenheden

def test_fill_placeholders():
    v = {"invnumber": "F-1", "invtotal": "121,00", "company": "<b>"}
    assert mailer.fill("{nummer} {number} {bedrag} {bedrijf} {onbekend} {nummer.__class__}", v) == \
        "F-1 F-1 121,00 <b> {onbekend} {nummer.__class__}"


def test_parse_addresses():
    assert mailer.parse_addresses("A <a@b.nl>; c@d.co.uk") == [("A", "a@b.nl"), ("", "c@d.co.uk")]
    for bad in ("a@b", "a b@c.nl", "<a@b.nl>x@y.nl", "a@b.nl\nBcc: c@d.nl", "@b.nl"):
        with pytest.raises(mailer.MailError):
            mailer.parse_addresses(bad)
