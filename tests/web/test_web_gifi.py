"""Beheer > GIFI-indeling in de webapp (bouwstap 3, GF1–GF6)."""

from test_web_am import admin, booker, cdb, redirected, submit, table  # noqa: F401 (fixtures)
from webhelpers import csrf, nohelp


def test_menu_place_and_rights(admin):
    menu = admin.get("/").text
    i = menu.index('href="/beheer/rekeningschema"')
    assert i < menu.index('href="/beheer/gifi"') < menu.index('href="/beheer/rgs"')       # GF6
    page = admin.get("/beheer/gifi").text
    assert "GIFI-indeling" in page and "RGS" in page                                     # helptekst noemt RGS


def test_bookkeeper_has_no_access(booker):
    assert 'href="/beheer/gifi"' not in booker.get("/").text
    for url in ("/beheer/gifi", "/beheer/gifi/nieuw", "/beheer/gifi/0200"):
        assert booker.get(url).status_code == 403, url


def test_list_count_and_orphans(admin, cdb):
    cdb.execute("UPDATE chart SET gifi_accno = '0200' WHERE accno = '0205'")
    cdb.execute("UPDATE chart SET gifi_accno = '010' WHERE accno = '0010'")
    page = nohelp(admin.get("/beheer/gifi").text)
    row = page[page.index('href="/beheer/gifi/0200"'):]
    assert '<td class="num">2</td>' in row[:row.index("</tr>")]
    row = page[page.index('href="/beheer/gifi/0205"'):]
    assert '<td class="num">—</td>' in row[:row.index("</tr>")]
    assert "verwijzen naar een GIFI-code die hieronder niet bestaat" in page
    assert 'href="/beheer/gifi/nieuw?code=010"' in page
    # de link vult de code alvast in; opslaan lost de dode verwijzing op
    form = admin.get("/beheer/gifi/nieuw?code=010").text
    assert 'name="accno" value="010"' in form
    redirected(submit(admin, "/beheer/gifi/nieuw?code=010", "/beheer/gifi",
                      {"description": "Terreinen", "action": "save"}), "/beheer/gifi?ok=am_saved")
    assert "hieronder niet bestaat" not in nohelp(admin.get("/beheer/gifi").text)


def test_add_rename_delete(admin, cdb):
    redirected(submit(admin, "/beheer/gifi/nieuw", "/beheer/gifi",
                      {"accno": "020", "description": "Goodwill", "action": "save"}), "/beheer/gifi")
    r = submit(admin, "/beheer/gifi/nieuw", "/beheer/gifi", {"accno": "020", "description": "x", "action": "save"})
    assert r.status_code == 400 and "GIFI-code 020 bestaat al." in r.text
    # in gebruik: geen knop Verwijderen, wel de rekeningen; hernoemen neemt ze mee (GF3)
    edit = nohelp(admin.get("/beheer/gifi/0200").text)
    assert 'value="delete"' not in edit and 'href="/beheer/rekeningschema/' in edit
    redirected(submit(admin, "/beheer/gifi/0200", "/beheer/gifi", {"accno": "0200A", "action": "save"}),
               "/beheer/gifi")
    assert table(cdb, "SELECT gifi_accno FROM chart WHERE accno = '0200'") == [{"gifi_accno": "0200A"}]
    # verwijderen van een gebruikte code wordt ook op de server geweigerd (GF2)
    r = submit(admin, "/beheer/gifi/0200A", "/beheer/gifi", {"action": "delete_confirmed"})
    assert r.status_code == 400 and "in gebruik bij rekening 0200" in r.text
    # ongebruikt: vragen, dan verwijderen
    r = submit(admin, "/beheer/gifi/020", "/beheer/gifi", {"action": "delete"})
    assert r.status_code == 200 and "GIFI-code verwijderen?" in r.text
    redirected(submit(admin, "/beheer/gifi/020", "/beheer/gifi", {"action": "delete_confirmed"}),
               "/beheer/gifi?ok=am_deleted")
    assert table(cdb, "SELECT 1 FROM gifi WHERE accno = '020'") == []
    assert admin.get("/beheer/gifi/020").status_code == 404


def test_account_form_list_and_warning(admin, cdb):
    """GF4: zoeklijst uit de tabel; een onbekende code mag, met waarschuwing; geen knop "Kopieer naar
    rekeningschema"."""
    cid = table(cdb, "SELECT id FROM chart WHERE accno = '0200'")[0]["id"]
    page = nohelp(admin.get(f"/beheer/rekeningschema/{cid}").text)
    assert 'list="gifilist"' in page and '<option value="0205" data-n="0205"' in page
    assert "staat niet in de GIFI-indeling" not in page
    r = submit(admin, f"/beheer/rekeningschema/{cid}", "/beheer/rekeningschema", {"gifi_accno": "999X"})
    redirected(r, f"/beheer/rekeningschema/{cid}")
    page = nohelp(admin.get(f"/beheer/rekeningschema/{cid}").text)
    assert "GIFI-code 999X staat niet in de GIFI-indeling" in page
    assert 'href="/beheer/gifi/nieuw?code=999X"' in page
    assert "Kopieer" not in page and "Copy to COA" not in page


def test_import_and_export(admin, cdb):
    """GF5: soort GIFI-indeling in Beheer > Importeren/Exporteren."""
    assert 'value="gifi"' in admin.get("/beheer/importeren").text
    assert "GIFI-indeling" in admin.get("/beheer/exporteren").text
    r = admin.get("/beheer/exporteren/gifi.csv")
    assert r.status_code == 200, r.text[:500]
    text = r.content.decode("utf-8-sig")
    assert text.startswith("accno;description\r\n") and "0200;" in text
    page = admin.get("/beheer/importeren").text
    r = admin.post("/beheer/importeren", data={"csrf": csrf(admin, page), "kind": "gifi"},
                   files={"bestand": ("gifi.csv", "accno;description\r\n0300;Nieuw\r\n0200;GIFI Gebouwen\r\n")},
                   follow_redirects=True)
    assert r.status_code == 200, r.text[:2000]
    preview = nohelp(r.text)
    assert "0300" in preview and "nieuw" in preview and "ongewijzigd" in preview
