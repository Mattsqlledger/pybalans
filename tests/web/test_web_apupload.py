"""Inkoop > Inkoopfactuur uploaden (fase 4 stap 4e, IU1–IU5): PDF slepen, leverancier, datum,
nummer en totaal; kostenrekening, btw en de rest voorgesteld; Boeken of Volledig scherm."""

import re
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from sqlledger.attachments import decode
from webhelpers import csrf, login, make_user

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def setup(cdb):
    """Oude tarieven (19%/6%) met einddatum; geen inkoophistorie uit de template."""
    cdb.execute("UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL")
    cdb.execute("DELETE FROM acc_trans WHERE trans_id IN (SELECT id FROM ap)")
    cdb.execute("DELETE FROM ap")


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def fields(html):
    """Velden van het uploadformulier zoals de browser ze verstuurt."""
    form = html.split('class="uploadform"', 1)[1].split("</form>", 1)[0]
    data = {}
    for m in re.finditer(r"<input\b[^>]*>", form):
        tag = m.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        if not name or "disabled" in tag or 'type="file"' in tag:
            continue
        if 'type="checkbox"' in tag and " checked" not in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        data[name.group(1)] = value.group(1) if value else ""
    for m in re.finditer(r'<select name="([^"]+)"[^>]*>(.*?)</select>', form, re.S):
        sel = re.search(r'<option value="([^"]*)"[^>]*selected', m.group(2))
        first = re.search(r'<option value="([^"]*)"', m.group(2))
        data[m.group(1)] = sel.group(1) if sel else (first.group(1) if first else "")
    for m in re.finditer(r'<textarea name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S):
        data[m.group(1)] = m.group(2)
    data.pop("csrf", None)
    return data


def step(client, page, data, files=None):
    return client.post("/inkoop/uploaden", data={**fields(page), "csrf": csrf(client, page), **data},
                       files=files, follow_redirects=False)


def upload(client, content=PDF, name="factuur.pdf"):
    page = client.get("/inkoop/uploaden").text
    r = step(client, page, {"action": "upload"}, files={"bestand": (name, content, "application/pdf")})
    assert r.status_code == 200, r.text[:2000]
    return r.text


def choose(client, page, vendor="L002", transdate="2026-04-01"):
    r = step(client, page, {"vc_text": vendor, "transdate": transdate, "action": "update"})
    assert r.status_code == 200, r.text[:2000]
    return r.text


def acc(cdb, trans_id):
    return [(r["accno"], r["amount"]) for r in cdb.execute(
        """SELECT c.accno, ac.amount::numeric AS amount FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
           WHERE trans_id = %s ORDER BY c.accno""", (trans_id,)).fetchall()]


def token_of(page):
    m = re.search(r'name="token" value="([0-9a-f]{32})"', page)
    assert m, "geen token"
    return m.group(1)


# ------------------------------------------------------------------ scherm en bestand

def test_menu_and_empty_page(booker):
    page = booker.get("/inkoop/uploaden").text
    assert 'href="/inkoop/uploaden"' in page and "Inkoopfactuur uploaden" in page
    assert 'type="file" name="bestand"' in page and 'accept="application/pdf,image/jpeg,image/png' in page
    assert "data-dropzone" in page and 'enctype="multipart/form-data"' in page


def test_readonly_role_and_module(client, db, company):
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "kijk")])
    login(client, "kijk")
    assert client.get("/inkoop/uploaden").status_code == 403
    assert 'href="/inkoop/uploaden"' not in client.get("/").text


def test_upload_shows_preview(booker, settings):
    page = upload(booker)
    token = token_of(page)
    assert f'src="/bijlage/tijdelijk/{token}#view=FitH' in page and "factuur.pdf" in page
    files = list((Path(settings.data_dir) / "uploads").iterdir())
    assert len(files) == 2                                     # inhoud + gegevens
    r = booker.get(f"/bijlage/tijdelijk/{token}")
    assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


def test_photo_shows_image(booker):
    page = upload(booker, JPG, "bon.jpg")
    assert f'<img src="/bijlage/tijdelijk/{token_of(page)}"' in page


@pytest.mark.parametrize("content, name, text", [
    (b"<html><script>alert(1)</script>", "factuur.pdf", "Alleen PDF, JPG of PNG"),
    (b"", "leeg.pdf", "leeg"),
])
def test_bad_file_refused(booker, settings, content, name, text):
    page = booker.get("/inkoop/uploaden").text
    r = step(booker, page, {"action": "upload"}, files={"bestand": (name, content, "application/pdf")})
    assert r.status_code == 400 and text in r.text
    assert not (Path(settings.data_dir) / "uploads").exists() or not list((Path(settings.data_dir) / "uploads").iterdir())


def test_too_large_refused(booker):
    page = booker.get("/inkoop/uploaden").text
    big = PDF + b"0" * (10 * 1024 * 1024)
    r = step(booker, page, {"action": "upload"}, files={"bestand": ("groot.pdf", big, "application/pdf")})
    assert r.status_code in (400, 413) and "10 MB" in r.text


def test_own_limit(booker, cdb):
    cdb.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
    cdb.execute("INSERT INTO defaults VALUES ('max_upload_size', '2')")
    page = booker.get("/inkoop/uploaden").text
    assert 'data-max-bytes="2097152"' in page and "(hoogstens 2 MB)" in page
    big = PDF + b"0" * (2 * 1024 * 1024)
    r = step(booker, page, {"action": "upload"}, files={"bestand": ("groot.pdf", big, "application/pdf")})
    assert r.status_code == 400 and "Het bestand is groter dan 2 MB." in r.text
    cdb.execute("UPDATE defaults SET fldvalue = '15' WHERE fldname = 'max_upload_size'")
    big = PDF + b"0" * (12 * 1024 * 1024)
    token_of(upload(booker, big))


def test_token_of_other_user_not_usable(booker, client, db, company):
    token = token_of(upload(booker))
    booker.post("/logout", data={"csrf": csrf(booker, booker.get("/").text)})
    make_user(db, "ander", memberships=[(company["id"], "bookkeeper", "ander")])
    login(client, "ander")
    assert client.get(f"/bijlage/tijdelijk/{token}").status_code == 404
    assert client.get("/bijlage/tijdelijk/../../etc").status_code == 404


# ------------------------------------------------------------------ voorstellen (IU3–IU5)

def test_vendor_without_history(booker):
    page = choose(booker, upload(booker))
    f = fields(page)
    assert f["vc_text"] == "L002 Graanhandel De Molen"
    assert f["expense"] == "" and f["tax_accno"] == "1800"
    assert f["duedate"] == "2026-05-01"                           # termijn 30 dagen
    assert "Kies een kostenrekening" in page or "kies zelf" in page


def test_vendor_with_history_and_live_calc(booker, cdb):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "M-1", "total": "121,00", "expense": "8100", "action": "post"})
    assert r.status_code == 303
    page = choose(booker, upload(booker, name="tweede.pdf"))
    f = fields(page)
    assert f["expense"].startswith("8100") and f["tax_accno"] == "1800"
    assert "laatste boeking" in page
    r = step(booker, page, {"total": "100,00", "action": "calc"})
    assert r.status_code == 200 and 'id="upload-calc"' in r.text
    calc = r.text.split('id="upload-calc"', 1)[1]
    assert "82,64" in calc and 'value="17,36"' in calc


def test_manual_tax_keeps_total(booker):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"total": "100,00", "action": "calc"})
    auto = fields(r.text.replace('id="upload-calc"', 'class="uploadform" id="upload-calc"', 1))
    r = step(booker, page, {"total": "100,00", "tax": "17,35", "tax_auto": auto["tax_auto"], "action": "calc"})
    assert "82,65" in r.text and 'value="17,35"' in r.text


def test_no_tax_choice(booker):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"total": "100,00", "tax_accno": "geen", "action": "calc"})
    assert "100,00" in r.text.split('id="upload-calc"', 1)[1]


def test_fixed_default_from_vendor_screen(booker, cdb):
    page = booker.get("/leveranciers/905").text
    assert 'name="expense_accno"' in page
    r = booker.post("/leveranciers", data={**_ct_fields(page), "csrf": csrf(booker, page), "expense_accno": "8020",
                                           "action": "save"}, follow_redirects=False)
    assert r.status_code == 303, re.findall(r'class="error"[^>]*>([^<]*)', r.text)
    assert cdb.execute("SELECT expense_chart_id FROM x_vendor_default WHERE vendor_id = 905").fetchone()
    assert 'value="8020" selected' in booker.get("/leveranciers/905").text
    f = fields(choose(booker, upload(booker)))
    assert f["expense"].startswith("8020")
    page = booker.get("/leveranciers/905").text
    r = booker.post("/leveranciers", data={**_ct_fields(page), "csrf": csrf(booker, page), "expense_accno": "1600",
                                           "action": "save"}, follow_redirects=False)
    assert r.status_code == 400                                   # geen inkooprekening


def _ct_fields(page):
    from test_web_ct import form_fields
    return form_fields(page.split('class="ctform"', 1)[1].split("</form>", 1)[0])


# ------------------------------------------------------------------ boeken

def test_post_books_and_stores_pdf(booker, cdb, settings):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "M-77", "total": "121,00", "expense": "8100", "description": "Meel",
                            "action": "post"})
    assert r.status_code == 303, r.text[:3000]
    m = re.fullmatch(r"/inkoop/uploaden\?ok=upload_posted&geboekt=(\d+)", r.headers["location"])
    tid = int(m.group(1))
    ap = cdb.execute("SELECT * FROM ap WHERE id = %s", (tid,)).fetchone()
    assert (ap["invnumber"], ap["vendor_id"], ap["amount"], ap["netamount"]) == ("M-77", 905, 121, 100)
    assert str(ap["duedate"]) == "2026-05-01" and ap["description"] == "Meel"
    assert acc(cdb, tid) == [("1600", Decimal("121")), ("1800", Decimal("-21")), ("8100", Decimal("-100"))]
    ref = cdb.execute("""SELECT r.*, a.filename FROM reference r JOIN archive a ON a.id = r.archive_id
                         WHERE r.trans_id = %s""", (tid,)).fetchone()
    assert (ref["formname"], ref["filename"], ref["description"]) == ("ap_transaction", "factuur.pdf", "factuur.pdf")
    rows = cdb.execute("SELECT bt FROM archivedata WHERE archive_id = %s ORDER BY rn", (ref["archive_id"],)).fetchall()
    assert decode(x["bt"] for x in rows) == PDF
    assert not list((Path(settings.data_dir) / "uploads").iterdir())   # tijdelijk bestand weg
    page = booker.get(r.headers["location"]).text
    assert f'href="/inkoop/{tid}"' in page and "M-77" in page             # melding met link; leeg scherm
    assert 'name="token" value=""' in page


def test_post_needs_fields(booker, cdb):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "", "total": "121,00", "expense": "8100", "action": "post"})
    assert r.status_code == 400 and "factuurnummer" in r.text
    r = step(booker, page, {"invnumber": "X1", "total": "", "expense": "8100", "action": "post"})
    assert r.status_code == 400 and "totaalbedrag" in r.text
    r = step(booker, page, {"invnumber": "X1", "total": "121,00", "expense": "", "action": "post"})
    assert r.status_code == 400 and "kostenrekening" in r.text
    page2 = booker.get("/inkoop/uploaden").text
    r = step(booker, page2, {"vc_text": "L002", "invnumber": "X1", "total": "121,00", "expense": "8100",
                             "action": "post"})
    assert r.status_code == 400 and "Sleep eerst de factuur" in r.text
    assert cdb.execute("SELECT count(*) AS n FROM ap").fetchone()["n"] == 0


def test_post_tax_must_be_chosen_with_two_accounts(booker, cdb):
    cdb.execute("INSERT INTO vendortax VALUES (905, (SELECT id FROM chart WHERE accno = '1801'))")
    cdb.execute("UPDATE tax SET validto = NULL WHERE chart_id = (SELECT id FROM chart WHERE accno = '1801')")
    page = choose(booker, upload(booker))
    assert fields(page)["tax_accno"] == ""
    r = step(booker, page, {"invnumber": "X1", "total": "106,00", "expense": "8100", "action": "post"})
    assert r.status_code == 400 and "btw-rekening" in r.text
    r = step(booker, page, {"invnumber": "X1", "total": "106,00", "expense": "8100", "tax_accno": "1801",
                            "action": "post"})
    tid = int(re.search(r"geboekt=(\d+)", r.headers["location"]).group(1))
    assert acc(cdb, tid) == [("1600", Decimal("106")), ("1801", Decimal("-6")), ("8100", Decimal("-100"))]


def test_post_without_tax(booker, cdb):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "X2", "total": "50,00", "expense": "8100", "tax_accno": "geen",
                            "action": "post"})
    tid = int(re.search(r"geboekt=(\d+)", r.headers["location"]).group(1))
    assert acc(cdb, tid) == [("1600", Decimal("50")), ("8100", Decimal("-50"))]


def test_duplicate_invnumber_asks(booker, cdb):
    page = choose(booker, upload(booker))
    step(booker, page, {"invnumber": "D-1", "total": "121,00", "expense": "8100", "action": "post"})
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "d-1", "total": "121,00", "expense": "8100", "action": "post"})
    assert r.status_code == 200 and "bestaat al bij deze leverancier" in r.text
    # hetzelfde bestand hangt ook al bij de eerste boeking (stap 6b, RB5): zonder "toch toevoegen" niet boeken
    assert "Dit bestand hangt al bij:" in r.text and "Inkoopboeking D-1" in r.text
    r2 = step(booker, r.text, {"action": "post"})
    assert r2.status_code == 400 and "toch toevoegen" in r2.text
    r = step(booker, r.text, {"action": "post", f"dup_ok_{token_of(r.text)}": "1"})
    assert r.status_code == 303
    assert cdb.execute("SELECT count(*) AS n FROM ap WHERE lower(invnumber) = 'd-1'").fetchone()["n"] == 2
    ids = [x["archive_id"] for x in cdb.execute("""SELECT r.archive_id FROM reference r JOIN ap a ON a.id = r.trans_id
                                                   WHERE lower(a.invnumber) = 'd-1' ORDER BY a.id""")]
    assert len(set(ids)) == 2                                      # niet gedeeld: twee archiefstukken


def test_closed_period_error_shown(booker, cdb):
    cdb.execute("UPDATE defaults SET fldvalue = '2026-12-31' WHERE fldname = 'closedto'")
    if not cdb.execute("SELECT 1 FROM defaults WHERE fldname = 'closedto'").fetchone():
        cdb.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', '2026-12-31')")
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "C1", "total": "121,00", "expense": "8100", "action": "post"})
    assert r.status_code == 400 and "afgesloten periode" in r.text
    assert token_of(r.text)                                        # bestand blijft staan


def test_full_screen(booker, cdb):
    page = choose(booker, upload(booker))
    r = step(booker, page, {"invnumber": "V-9", "total": "121,00", "expense": "8100", "action": "full"})
    assert r.status_code == 200 and "Inkoopboeking toevoegen" in r.text
    full = r.text
    assert 'name="amount_1" value="100,00"' in full and 'name="tax_1800" value="21,00"' in full
    assert 'name="invnumber" value="V-9"' in full and "factuur.pdf" in full
    assert 'enctype="multipart/form-data"' in full
    from test_web_arap import step as arap_step
    r = arap_step(booker, "/inkoop", {"action": "post"}, full)
    assert r.status_code == 303, r.text[:3000]
    tid = int(re.search(r"/inkoop/(\d+)", r.headers["location"]).group(1))
    assert acc(cdb, tid) == [("1600", Decimal("121")), ("1800", Decimal("-21")), ("8100", Decimal("-100"))]
    assert cdb.execute("SELECT count(*) AS n FROM reference WHERE trans_id = %s AND archive_id IS NOT NULL",
                       (tid,)).fetchone()["n"] == 1


def test_discard_file(booker, settings):
    page = upload(booker)
    r = step(booker, page, {"action": "discard"})
    assert r.status_code == 200 and 'name="token" value=""' in r.text
    assert not list((Path(settings.data_dir) / "uploads").iterdir())


def test_old_pending_files_cleaned(booker, settings):
    import os
    import time
    token = token_of(upload(booker))
    d = Path(settings.data_dir) / "uploads"
    old = time.time() - 2 * 86400
    for p in d.iterdir():
        os.utime(p, (old, old))
    upload(booker, name="nieuw.pdf")
    assert not any(p.name.startswith(token) for p in d.iterdir())
