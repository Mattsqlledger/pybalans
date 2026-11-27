"""Beheer > Huisstijl (HUISSTIJL_ANALYSE.md HS1–HS12): scherm, rechten, voorbeeld, opslaan, logo, overnemen van
een andere administratie, melding eigen sjablonen, lettertypen en licenties."""

import re
import uuid
from pathlib import Path

import psycopg
import pytest

from sqlledger import docstyle
from sqlledger_web import routes_style

from conftest import DSN, TEMPLATE
from test_web_docsettings import form_values
from webhelpers import csrf, login, make_user, nohelp

URL = "/beheer/huisstijl"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


@pytest.fixture
def admin(client, db, company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas")])
    login(client, "baas", secret)
    return client


def cconn(company):
    return psycopg.connect(f"{DSN} dbname={company['dbname']}", autocommit=True)


def logo_dir(settings, company_id) -> Path:
    return Path(settings.data_dir) / "templates" / str(company_id)


def choose(client, company):
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company["id"]})


def save(client, **changes):
    page = client.get(URL).text
    data = form_values(page)
    data.update(changes)
    data["action"] = "save"
    return client.post(URL, data={**data, "csrf": csrf(client, page)}, follow_redirects=False)


@pytest.fixture
def other_company(db, pg_admin):
    """Tweede administratie (kopie van sl_template)."""
    name = f"wc_{uuid.uuid4().hex[:10]}"
    pg_admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}"')
    row = db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Holding B.V.', %s, %s) RETURNING *",
                     (name, ["gl", "ar"])).fetchone()
    yield row
    pg_admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_menu_page_and_rights(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper")])
    login(client, "boek")
    assert client.get(URL).status_code == 403
    assert 'href="/beheer/huisstijl"' not in client.get("/").text


def test_page_defaults_and_links(admin):
    page = admin.get(URL).text
    assert 'href="/beheer/huisstijl"' in page                     # menu
    assert '<option value="" selected class="font-standard">Standaard (zoals nu)</option>' in page
    assert 'value="#111111"' in page and 'href="/lettertypen"' in page
    assert "Overnemen van" not in nohelp(page)                    # geen andere administratie met beheerrechten
    assert 'src="/beheer/huisstijl/voorbeeld?logo_size=medium&amp;logo_pos=left&amp;color=%23111111' in page
    assert 'href="/beheer/huisstijl"' in admin.get("/beheer/documentinstellingen").text
    assert 'href="/beheer/huisstijl"' in admin.get("/beheer/sjablonen").text


def test_preview_html_and_pdf(admin, company):
    q = "logo_size=small&logo_pos=right&color=%23ffff66&font=inter&address_pos=right&show_head=1&show_footer=1"
    r = admin.get(f"{URL}/voorbeeld?{q}")
    assert r.status_code == 200
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    html = r.text
    assert "url(/lettertypen/inter/Inter-Variable.ttf)" in html     # in de browser via URL, niet als data-URL
    assert "#ffff66" in html                                        # te licht: wel tonen (keuze 2d)
    assert 'id="sl-window" style="right: 16mm"' in html
    pdf = admin.get(f"{URL}/voorbeeld?{q}&pdf=1")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert admin.get(f"{URL}/voorbeeld?font=comic").status_code == 400
    with cconn(company) as c:                                       # niets op 'geprint' gezet
        assert c.execute("SELECT count(*) FROM status WHERE printed").fetchone()[0] == 0


def test_sample_invoice_without_sales(admin, company):
    with cconn(company) as c:
        v = routes_style._sample_vars(c, "de")
    assert v["name"] == "Musterkunde GmbH" and v["invnumber"] == "MUSTER"
    assert v["description"] == ["Beratung", "Artikel A", "Versandkosten"]
    assert v["subtotal"] == "372,95"                                # 4 × 85 + 2 × 12,50 + 7,95
    with cconn(company) as c:
        v = routes_style._sample_vars(c, "en")
    assert v["subtotal"] == "372.95" and v["company"]


def test_save_and_contrast(admin, company):
    r = save(admin, color="#eeeeee")
    assert r.status_code == 400 and "te licht" in r.text
    r = save(admin)                                                 # ongewijzigd: "zo is het goed" (HS10)
    assert r.status_code == 303 and r.headers["location"] == "/beheer/huisstijl?ok=saved"
    with cconn(company) as c:
        assert docstyle.load_rows(c) == {"style.saved": "1"}
    assert "Huisstijl opgeslagen." in admin.get(URL + "?ok=saved").text
    r = save(admin, color="#7A1F2B", font="lora", logo_pos="center", logo_size="large", address_pos="right",
             show_head="0", show_footer="0")
    assert r.status_code == 303
    with cconn(company) as c:
        assert docstyle.load(c) == docstyle.DocStyle(color="#7a1f2b", font="lora", logo_pos="center",
                                                     logo_size="large", address_pos="right", show_head=False,
                                                     show_footer=False, saved=True)
    page = admin.get(URL).text
    assert 'name="font"' in page and '<option value="lora" selected class="font-lora">' in page
    assert re.search(r'name="show_head" value="1"(?! checked)', page)


def test_logo_upload_and_delete(admin, settings, company):
    page = admin.get(URL).text
    r = admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo"}, files={"logo": ("l.png", PNG)},
                   follow_redirects=False)
    assert r.status_code == 303 and (logo_dir(settings, company["id"]) / "logo.png").is_file()
    page = admin.get(URL).text
    assert "Logo verwijderen" in page
    r = admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo", }, files={"logo": ("x.txt", b"hallo")})
    assert r.status_code == 400 and "Alleen PNG, JPEG of SVG." in r.text
    admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo_delete"})
    assert not (logo_dir(settings, company["id"]) / "logo.png").exists()


def test_logo_saved_at_once_json(admin, settings, company):
    """PB11: kiezen of slepen slaat meteen op via fetch (Accept: application/json); fouten inline."""
    page = admin.get(URL).text
    assert "Sleep het logo hierheen of kies een bestand" in page and "data-logoform" in page
    assert re.search(r'data-logo-delete hidden', page)                 # nog geen eigen logo
    assert "data-logo-save" in page                                     # knop voor zonder JavaScript
    js = {"Accept": "application/json"}
    r = admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo"}, files={"logo": ("l.png", PNG)},
                   headers=js, follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["notice"] == "Logo opgeslagen." and r.json()["logo"].startswith("data:image/png;base64,")
    assert (logo_dir(settings, company["id"]) / "logo.png").is_file()
    r = admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo"}, files={"logo": ("x.txt", b"hallo")},
                   headers=js)
    assert r.status_code == 400 and r.json() == {"ok": False, "error": "Alleen PNG, JPEG of SVG."}
    r = admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo"}, headers=js)
    assert r.status_code == 400 and r.json()["error"] == "Kies een bestand."
    r = admin.post(URL, data={"csrf": csrf(admin, page), "action": "logo"},
                   files={"logo": ("groot.png", PNG + b"\0" * (1024 * 1024))}, headers=js)
    assert r.status_code in (400, 413)
    page = admin.get(URL).text
    assert 'class="logo-preview"' in page and not re.search(r'data-logo-delete hidden', page)
    # hetzelfde logo bij Sjablonen
    assert "logo.png" in admin.get("/beheer/sjablonen").text


def test_copy_from_other_company(client, db, company, other_company, settings):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas"),
                                                          (other_company["id"], "admin", "baas")])
    login(client, "baas", secret)
    choose(client, company)
    with cconn(other_company) as c:
        docstyle.save(c, docstyle.DocStyle(color="#0f5560", font="source-serif-4", logo_pos="right"))
    d = logo_dir(settings, other_company["id"])
    d.mkdir(parents=True)
    (d / "logo.png").write_bytes(PNG)
    own = logo_dir(settings, company["id"])
    own.mkdir(parents=True)
    (own / "logo.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 20)

    page = client.get(URL).text
    assert "Overnemen van" in page and "Holding B.V." in page
    r = client.post(URL, data={"csrf": csrf(client, page), "action": "load", "bron": str(other_company["id"])})
    assert r.status_code == 200 and "Huisstijl van Holding B.V. geladen — nog niet opgeslagen." in r.text
    assert 'value="#0f5560"' in r.text and f'bron={other_company["id"]}' in r.text
    with cconn(company) as c:
        assert docstyle.load_rows(c) == {}                          # laden slaat niets op
    data = form_values(r.text)
    r = client.post(URL, data={**data, "action": "save", "csrf": csrf(client, r.text)}, follow_redirects=False)
    assert r.status_code == 303
    with cconn(company) as c:
        assert docstyle.load(c).font == "source-serif-4"
        log = c.execute("SELECT reference, action FROM audittrail WHERE formname = 'docstyle'").fetchall()
    assert (own / "logo.png").read_bytes() == PNG and not (own / "logo.jpg").exists()
    with cconn(other_company) as c:                                 # de bron is niet veranderd
        assert docstyle.load(c).font == "source-serif-4"
    # audittrail staat in sl_template uit: dan geen regels, wel het logboek van de app
    assert log == [] or log == [("Holding B.V.", "copied")]
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'docstyle_saved'").fetchone()["n"] == 1


def test_copy_requires_admin_role(client, db, company, other_company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "baas"),
                                                          (other_company["id"], "bookkeeper", "baas")])
    login(client, "baas", secret)
    choose(client, company)
    page = client.get(URL).text
    assert "Overnemen van" not in nohelp(page)
    r = client.post(URL, data={"csrf": csrf(client, page), "action": "load", "bron": str(other_company["id"])})
    assert r.status_code == 403
    assert client.get(f"{URL}/voorbeeld?bron={other_company['id']}").status_code == 403


def test_own_templates_notice(admin, settings, company):
    d = logo_dir(settings, company["id"])
    (d / "en").mkdir(parents=True)
    (d / "invoice.html").write_text('{% extends "_invoice.html" %}')
    (d / "en" / "packing_list.html").write_text('{{ style.color }}{% extends "packing_list.html" %}')
    page = admin.get(URL).text
    assert "Voor deze documenten heeft u een eigen sjabloon" in page
    assert "Factuur, Pakbon (variant en) (gebruikt de huisstijl)." in page
    assert "eigen basissjabloon" not in page
    (d / "_base.html").write_text("<html>{% block body %}{% endblock %}</html>")
    assert "De huisstijl geldt daardoor voor geen enkel document" in admin.get(URL).text


def test_fonts_page_and_files(admin):
    page = admin.get("/lettertypen").text
    for name in ("Liberation Sans", "Inter", "Source Sans 3", "Source Serif 4", "Lora"):
        assert name in page
    assert page.count("SIL Open Font License 1.1") >= 5 and "Reserved Font Name" in page
    r = admin.get("/lettertypen/lora/Lora-Variable.ttf")
    assert r.status_code == 200 and r.headers["content-type"] == "font/ttf"
    assert admin.get("/lettertypen/lora/OFL.txt").status_code == 200
    assert admin.get("/lettertypen/lora/..%2F..%2Fmain.py").status_code == 404
    assert admin.get("/lettertypen/comic/x.ttf").status_code == 404
    css = admin.get("/lettertypen/lettertypen.css").text
    assert ".font-inter {" in css and ".font-standard {" in css


def test_start_list_points_here(admin, company):
    from sqlledger.config import UserConfig
    from sqlledger.dashboard import Dashboard
    with cconn(company) as c:
        Dashboard(c, UserConfig(login="baas")).enable_start_list()
    block = admin.get("/dashboard/blok/start").text
    assert 'href="/beheer/huisstijl">Logo en huisstijl</a>' in block
    save(admin)
    assert re.search(r'class="done">.*?Logo en huisstijl', admin.get("/dashboard/blok/start").text, re.S)
