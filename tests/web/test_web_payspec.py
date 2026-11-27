"""Betalingsspecificatie en betalingsregister (fase 4 stap 4b, SB7–SB8, SB15).

Testdata uit compare/arap_seed.sql: klant 900 (Klant A, K001) met F-3001 (121) en F-3006 (263 open),
klant 902 (Bakkerij Cees) met F-3002/F-3004/F-3007.
"""

import email
import email.policy
import re
from pathlib import Path

import pytest

from test_web_cp import ok, start, step
from test_web_dunning import configure_mail
from webhelpers import csrf, login, make_user

KEY = "1100|R-77|2026-07-01|900"


@pytest.fixture
def cdb(settings, company_db):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
        conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
        if not conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'").rowcount:
            conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('audittrail', '1')")
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    cdb.execute("UPDATE customer SET email = 'koek@example.test' WHERE id = 900")
    login(client, "boek")
    return client


def pay_900(client):
    """Ontvangst R-77 van Klant A op F-3001 en F-3006 via het scherm; geeft de pagina na het boeken."""
    page = start(client, "K001")
    calc = ok(step(client, "/ontvangst", {"checked_3001": "1", "checked_3006": "1", "action": "calc"}, page))
    r = step(client, "/ontvangst", {"checked_3001": "1", "checked_3006": "1", "source": "R-77", "action": "post"},
             calc)
    assert r.status_code == 303, r.text[:2000]
    return client.get(r.headers["location"]).text


def trail(cdb):
    return [(r["trans_id"], r["formname"], r["action"]) for r in cdb.execute(
        "SELECT trans_id, formname, action FROM audittrail WHERE formname = 'receipt' AND action <> 'posted' "
        "ORDER BY trans_id").fetchall()]


def spec_post(client, page, action, keys, soort="ontvangsten", **extra):
    data = {"csrf": csrf(client, page), "action": action, "soort": soort, "sel": keys, **extra}
    return client.post("/betalingsregister", data=data)


def test_buttons_after_posting_one(booker, cdb):
    after = pay_900(booker)
    assert 'action="/betalingsregister"' in after and f'name="sel" value="{KEY}"' in after
    r = spec_post(booker, after, "preview", [KEY])
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "inline" in r.headers["content-disposition"]
    assert trail(cdb) == []                                   # voorbeeld legt niets vast
    r = spec_post(booker, after, "pdf", [KEY])
    assert r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    assert trail(cdb) == [(3001, "receipt", "printed"), (3006, "receipt", "printed")]
    assert not cdb.execute("SELECT 1 FROM status WHERE formname = 'receipt'").fetchone()


def test_buttons_after_posting_many(booker, cdb):
    page = ok(booker.get("/ontvangsten?datum=2026-07-01"))
    calc = ok(step(booker, "/ontvangsten", {"vcchecked_900": "1", "vcchecked_902": "1", "action": "calc"}, page))
    r = step(booker, "/ontvangsten", {"action": "post"}, calc)
    assert r.status_code == 303, r.text[:2000]
    after = booker.get(r.headers["location"]).text
    keys = re.findall(r'name="sel" value="([^"]+)" checked', after)
    assert len(keys) == 2 and "Betalingsspecificaties (2)" in after
    r = spec_post(booker, after, "pdf", keys)
    assert r.content.startswith(b"%PDF") and "Betalingsspecificaties-2026-07-01" in r.headers["content-disposition"]
    assert {t[0] for t in trail(cdb)} >= {3001, 3006, 3002, 3004, 3007}


def test_register_lists_and_filters(booker, cdb):
    pay_900(booker)
    page = ok(booker.get("/betalingsregister", params={"soort": "ontvangsten", "van": "2026-07-01"}))
    assert "R-77" in page and "Klant A" in page and "384,00" in page and "koek@example.test" in page
    assert f'value="{KEY}"' in page and 'value="pdf"' in page
    assert "R-77" not in booker.get("/betalingsregister", params={"soort": "betalingen", "van": "2026-07-01"}).text
    assert "R-77" not in booker.get("/betalingsregister", params={"van": "2026-07-02"}).text
    assert 'href="/betalingsregister"' in booker.get("/").text


def test_register_mail(booker, db, company, cdb, settings):
    pay_900(booker)
    configure_mail(db, company)
    page = ok(booker.get("/betalingsregister", params={"van": "2026-07-01"}))
    r = spec_post(booker, page, "mail", [KEY], van="2026-07-01")
    assert r.status_code == 200 and "Betalingsspecificaties mailen" in r.text and "koek@example.test" in r.text
    assert "Verzenden (1)" in r.text and trail(cdb) == []
    r2 = spec_post(booker, r.text, "mail_send", [KEY], van="2026-07-01")
    assert "Gemaild: 1, mislukt: 0, zonder e-mailadres: 0." in r2.text
    folder = Path(settings.data_dir) / "mail" / str(company["id"])
    msgs = [email.message_from_bytes(p.read_bytes(), policy=email.policy.default) for p in folder.glob("*.eml")]
    assert len(msgs) == 1 and msgs[0]["subject"].startswith("Betalingsspecificatie")
    body = msgs[0].get_body(("plain",)).get_content()
    assert "384,00" in body and "F-3006, F-3001" in body
    assert [p.get_content_type() for p in msgs[0].iter_attachments()] == ["application/pdf"]
    assert trail(cdb) == [(3001, "receipt", "emailed"), (3006, "receipt", "emailed")]


def test_unknown_or_voided_key(booker, cdb):
    page = ok(booker.get("/betalingsregister"))
    r = spec_post(booker, page, "preview", ["1100|X|2026-07-01|900"])
    assert r.status_code == 400 and "niet (meer) gevonden" in r.text
    r = spec_post(booker, page, "preview", [])
    assert r.status_code == 400 and "Vink minstens één betaling aan." in r.text


def test_corrigeren_has_spec_buttons(booker, cdb):
    pay_900(booker)
    page = ok(booker.get("/ontvangsten/corrigeren", params={"weergave": "exclude", "van": "2026-07-01"}))
    assert 'formaction="/betalingsregister"' in page and 'name="soort" value="ontvangsten"' in page
    r = spec_post(booker, page, "preview", [KEY], rekening="", relatie="")
    assert r.content.startswith(b"%PDF")


def test_reader_only_preview(client, db, company, cdb, booker):
    pay_900(booker)
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    page = ok(client.get("/betalingsregister", params={"van": "2026-07-01"}))
    assert 'value="preview"' in page and 'value="pdf"' not in page
    assert spec_post(client, page, "preview", [KEY]).content.startswith(b"%PDF")
    assert spec_post(client, page, "pdf", [KEY]).status_code == 403
    assert spec_post(client, page, "mail", [KEY]).status_code == 403


def test_vendor_check_and_admin_screens(client, db, company, cdb, booker):
    page = start(booker, "Leverancier B", prefix="betaling")
    m = re.search(r'name="checked_(\d+)"', page)
    calc = ok(step(booker, "/betaling", {f"checked_{m.group(1)}": "1", "action": "calc"}, page))
    r = step(booker, "/betaling", {f"checked_{m.group(1)}": "1", "source": "C-5", "action": "post"}, calc)
    after = booker.get(r.headers["location"]).text
    key = re.search(r'name="sel" value="([^"]+)"', after).group(1)
    assert spec_post(booker, after, "preview", [key], soort="betalingen").content.startswith(b"%PDF")
    secret = make_user(db, "beheer", totp=True, memberships=[(company["id"], "admin", "admin")])
    login(client, "beheer", secret)
    assert "check" in client.get("/beheer/email").text
    tpl = client.get("/beheer/sjablonen").text
    assert "Betalingsspecificatie betaling (leverancier)" in tpl and "_payment" in tpl
    for name in ("receipt", "check", "_payment"):
        r = client.get("/beheer/sjablonen/voorbeeld", params={"naam": name})
        assert r.status_code == 200 and r.content.startswith(b"%PDF"), name
    ds = client.get("/beheer/documentinstellingen").text
    assert 'name="intro_receipt"' in ds and 'name="intro_check"' in ds
