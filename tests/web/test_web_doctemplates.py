"""Beheer > Sjablonen (fase 4, D5/DS11): downloaden, uploaden met controle, versies, terug naar standaard,
voorbeeld, logo, varianten; beveiliging (sandbox, paden, grootte, type, rollen)."""

import pytest

from test_web_arap import step
from test_web_is import add_item, cdb, posted_id, setup, start  # noqa: F401 (fixtures)
from webhelpers import csrf, login, make_user


@pytest.fixture
def admin(client, db, company, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    return client


@pytest.fixture
def tdir(tmp_path, company):
    return tmp_path / "templates" / str(company["id"])


def make_invoice(client):
    page = start(client, "K001")
    page = add_item(client, page, "A-1", qty="2")
    return posted_id(step(client, "/verkoopfactuur", {"action": "post"}, page))


def upload(client, name, content, variant="", field="bestand", action="upload", filename="x.html"):
    page = client.get("/beheer/sjablonen").text
    return client.post("/beheer/sjablonen", data={"csrf": csrf(client, page), "action": action, "naam": name,
                                                  "variant": variant},
                       files={field: (filename, content)}, follow_redirects=False)


def post(client, **data):
    page = client.get("/beheer/sjablonen").text
    return client.post("/beheer/sjablonen", data={"csrf": csrf(client, page), **data}, follow_redirects=False)


def test_page_download_and_menu(admin):
    page = admin.get("/beheer/sjablonen").text
    assert 'href="/beheer/sjablonen"' in admin.get("/").text
    for name in ("invoice", "packing_list", "sales_order", "ar_transaction", "_base", "_invoice"):
        assert f"<code>{name}.html</code>" in page
    r = admin.get("/beheer/sjablonen/bestand?naam=invoice")
    assert r.status_code == 200 and 'extends "_invoice.html"' in r.text
    assert r.headers["content-disposition"].startswith("attachment") and r.headers["content-type"].startswith("text/plain")
    assert admin.get("/beheer/sjablonen/bestand?naam=../../etc/passwd").status_code == 404
    assert admin.get("/beheer/sjablonen/bestand?naam=invoice&variant=../x").status_code == 400


def test_upload_checked_saved_versions_reset(admin, cdb, tdir):
    sid = make_invoice(admin)
    good = '{% extends "_invoice.html" %}{% block closing %}<p>Eigen slot {{ invnumber }}</p>{% endblock %}'
    r = upload(admin, "invoice", good.encode())
    assert r.status_code == 303 and r.headers["location"].endswith("ok=saved")
    assert (tdir / "invoice.html").read_text() == good
    page = admin.get("/beheer/sjablonen").text
    assert "<strong>eigen</strong>" in page
    # PDF van de factuur gebruikt het eigen sjabloon
    assert admin.get(f"/document/invoice/{sid}.pdf").status_code == 200
    # fout in de opbouw: geweigerd met regelnummer, oud bestand blijft
    r = upload(admin, "invoice", b"regel 1\n{% if %}\n")
    assert r.status_code == 400 and "Fout in regel 2" in r.text
    assert (tdir / "invoice.html").read_text() == good
    # proef-PDF: sandbox-ontsnapping en onbekende filters worden geweigerd
    r = upload(admin, "invoice", b"{{ ''.__class__.__mro__[1].__subclasses__() }}")
    assert r.status_code == 400 and "proef-PDF" in r.text
    r = upload(admin, "invoice", b"{{ 1|onbekend }}")
    assert r.status_code == 400
    # tweede versie -> vorige versie bewaard en terug te zetten
    upload(admin, "invoice", b"<p>Tweede {{ invnumber }}</p>")
    versions = sorted(p.name for p in tdir.glob("invoice.html.*"))
    assert len(versions) == 1
    stamp = versions[0].split(".html.")[1]
    assert admin.get(f"/beheer/sjablonen/bestand?naam=invoice&versie={stamp}").text == good
    r = post(admin, action="restore", naam="invoice", versie=stamp)
    assert r.status_code == 303 and (tdir / "invoice.html").read_text() == good
    assert post(admin, action="restore", naam="invoice", versie="../../x").status_code == 400
    # terug naar standaard: eerst bevestigen
    r = post(admin, action="reset", naam="invoice")
    assert r.status_code == 200 and "terug naar standaard" in r.text.lower()
    r = post(admin, action="reset_confirmed", naam="invoice")
    assert r.status_code == 303 and not (tdir / "invoice.html").exists()
    log = cdb.execute("SELECT reference, action FROM audittrail WHERE tablename = 'templates' ORDER BY transdate").fetchall()
    assert all(r["reference"] == "invoice" for r in log)          # leeg als de audittrail uit staat


def test_upload_limits_and_names(admin, tdir):
    assert upload(admin, "invoice", b"x" * (512 * 1024 + 1)).status_code == 400
    assert "UTF-8" in upload(admin, "invoice", b"\xff\xfe\x00x").text
    assert upload(admin, "../invoice", b"<p>x</p>").status_code == 400
    assert upload(admin, "invoice", b"<p>x</p>", variant="../x").status_code == 400
    assert not list(tdir.parent.parent.rglob("*.html")) or all("templates" in str(p) for p in tdir.parent.parent.rglob("*.html"))


def test_preview_without_status(admin, cdb):
    sid = make_invoice(admin)
    r = admin.get("/beheer/sjablonen/voorbeeld?naam=invoice")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    assert cdb.execute("SELECT count(*) AS n FROM status WHERE trans_id = %s", (sid,)).fetchone()["n"] == 0
    assert admin.get("/beheer/sjablonen/voorbeeld?naam=sales_quotation").status_code == 404   # geen offerte


def test_logo(admin, tdir):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = upload(admin, "", png, field="logo", action="logo", filename="logo.png")
    assert r.status_code == 303 and (tdir / "logo.png").read_bytes() == png
    assert "logo-preview" in admin.get("/beheer/sjablonen").text
    bad = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = upload(admin, "", bad, field="logo", action="logo", filename="logo.svg")
    assert r.status_code == 400 and (tdir / "logo.png").exists()
    ext = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="http://evil/x.png"/></svg>'
    assert upload(admin, "", ext, field="logo", action="logo", filename="l.svg").status_code == 400
    assert upload(admin, "", b"GIF89a....", field="logo", action="logo", filename="l.gif").status_code == 400
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
    assert upload(admin, "", svg, field="logo", action="logo", filename="l.svg").status_code == 303
    assert (tdir / "logo.svg").exists() and not (tdir / "logo.png").exists()     # één logo
    assert post(admin, action="logo_delete").status_code == 303 and not (tdir / "logo.svg").exists()


def test_variants(admin, cdb, tdir):
    cdb.execute("INSERT INTO language (code, description) VALUES ('Factor', 'Factoring') ON CONFLICT DO NOTHING")
    page = admin.get("/beheer/sjablonen").text
    assert 'value="Factor"' in page
    r = post(admin, action="variant_add", code="Factor")
    assert r.status_code == 303 and (tdir / "Factor").is_dir()
    r = upload(admin, "invoice", b"<p>factor {{ invnumber }}</p>", variant="Factor")
    assert r.status_code in (200, 303) and (tdir / "Factor" / "invoice.html").exists()
    page = admin.get("/beheer/sjablonen?variant=Factor").text
    assert "<strong>eigen</strong>" in page
    r = post(admin, action="variant_remove", variant="Factor")
    assert r.status_code == 400 and (tdir / "Factor").is_dir()                   # nog een eigen sjabloon
    r = post(admin, action="variant_remove_confirmed", variant="Factor")
    assert r.status_code == 400 and (tdir / "Factor" / "invoice.html").exists()  # ook niet zonder bevestigstap
    post(admin, action="reset_confirmed", naam="invoice", variant="Factor")
    assert list((tdir / "Factor").glob("invoice.html.*"))                        # vorige versie bewaard
    # F26: na "Terug naar standaard" is de variant wel te verwijderen, na bevestiging, met de vorige versies
    r = post(admin, action="variant_remove", variant="Factor")
    assert r.status_code == 200 and "Variant Factor verwijderen?" in r.text and "vorige versies" in r.text
    assert (tdir / "Factor").is_dir()
    assert post(admin, action="variant_remove_confirmed", variant="Factor").status_code == 303
    assert not (tdir / "Factor").exists()
    assert post(admin, action="variant_add", code="../../x").status_code == 400


def test_roles(client, db, company, settings, tmp_path):
    settings.data_dir = str(tmp_path)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    assert client.get("/beheer/sjablonen").status_code == 403
    assert client.get("/beheer/sjablonen/bestand?naam=invoice").status_code == 403
    assert client.get("/beheer/sjablonen/voorbeeld?naam=invoice").status_code == 403
