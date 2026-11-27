"""Administratie wisselen via het keuzemenu in de kopregel (ADMINISTRATIE_WISSELEN_ANALYSE.md, AW1–AW6)."""

import re

import pytest

from sqlledger_web import appdb

from webhelpers import csrf, login, make_user, post


def _company(db, name, dbname, code="", **kw):
    cols = {"name": name, "dbname": dbname, "code": code, "modules": list(appdb.MODULES), **kw}
    return db.execute(f"INSERT INTO company ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                      "RETURNING *", list(cols.values())).fetchone()


def _choose(client, company_id):
    page = client.get("/administratie").text
    return client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company_id})


def _menu(html):
    """Het keuzemenu uit de kopregel."""
    m = re.search(r'<details class="switcher".*?</details>', html, re.S)
    assert m, "geen keuzemenu in de kopregel"
    return m.group(0)


def _recent(html):
    m = re.search(r'<div class="switchrecent">(.*?)</div>', _menu(html), re.S)
    return re.findall(r'<button name="company_id" value="(\d+)"', m.group(1)) if m else []


@pytest.fixture
def three(db, company, company_db):
    a = _company(db, "Voorbeeld Education BV", company_db, "108220")
    b = _company(db, "Voorbeeld UK Ltd", company_db, "2")
    c = _company(db, "Zonnehoek", company_db, locked=True, lock_message="Controle accountant")
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper"), (a["id"], "bookkeeper"),
                                      (b["id"], "readonly"), (c["id"], "bookkeeper")])
    return a, b, c


def test_switch_goes_to_dashboard_and_remembers_recent(client, db, company, three):
    a, b, c = three
    r = login(client, "jan")
    assert r.url.path == "/administratie"
    r = _choose(client, a["id"])
    assert r.url.path == "/"                                          # AW2 = B: altijd het dashboard
    menu = _menu(r.text)
    assert '<span class="admcode">108220</span> Voorbeeld Education BV' in menu   # AW5: code in de kopregel
    assert _recent(r.text) == []                                      # de huidige staat er niet nog eens in
    _choose(client, b["id"])
    r = _choose(client, company["id"])
    assert _recent(r.text) == [str(b["id"]), str(a["id"])]           # laatst gebruikt bovenaan
    # vanaf een ander scherm wisselen komt ook op het dashboard uit
    page = client.get("/memoriaal/nieuw").text
    r = client.post("/administratie", data={"csrf": csrf(client, page), "company_id": a["id"]})
    assert r.url.path == "/" and _recent(r.text) == [str(company["id"]), str(b["id"])]
    uid = db.execute("SELECT id FROM app_user WHERE login = 'jan'").fetchone()["id"]
    assert db.execute("SELECT count(*) AS n FROM recent_company WHERE user_id = %s", (uid,)).fetchone()["n"] == 3


def test_recent_is_per_user_and_limited(client, db, company, company_db):
    many = [_company(db, f"Adm {i:02d}", company_db) for i in range(9)]
    make_user(db, "jan", memberships=[(c["id"], "bookkeeper") for c in many])
    make_user(db, "piet", memberships=[(c["id"], "bookkeeper") for c in many])
    login(client, "jan")
    for c in many:
        r = _choose(client, c["id"])
    assert _recent(r.text) == [str(c["id"]) for c in reversed(many[3:8])]     # laatste 5, zonder de huidige
    uid = db.execute("SELECT id FROM app_user WHERE login = 'jan'").fetchone()["id"]
    assert db.execute("SELECT count(*) AS n FROM recent_company WHERE user_id = %s", (uid,)).fetchone()["n"] == 6
    client.cookies.clear()
    login(client, "piet")
    r = _choose(client, many[0]["id"])
    assert _recent(r.text) == []                                      # AW4: per gebruiker


def test_menu_shows_only_memberships_and_locked_grey(client, db, company, company_db, three):
    a, b, c = three
    other = _company(db, "Van een ander", company_db)
    off = _company(db, "Uitgezet", company_db, active=False)
    make_user(db, "chef", memberships=[(other["id"], "admin")])
    db.execute("INSERT INTO membership SELECT id, %s, 'bookkeeper', 'jan' FROM app_user WHERE login = 'jan'",
               (off["id"],))
    db.execute("INSERT INTO recent_company SELECT id, %s FROM app_user WHERE login = 'jan'", (c["id"],))
    login(client, "jan")
    menu = _menu(_choose(client, a["id"]).text)
    assert "Van een ander" not in menu and "Uitgezet" not in menu     # AW6: alleen eigen, actieve
    # vergrendeld: grijs, niet te kiezen, met de melding
    assert re.search(rf'value="{c["id"]}" class="closed" disabled title="Controle accountant">', menu)
    assert "Alle tonen" in menu
    assert 'name="q"' not in menu                                     # zoekveld pas vanaf 8 administraties
    # en de server weigert hem nog steeds
    r = post(client, "/administratie", {"company_id": c["id"]}, follow_redirects=False)
    assert r.headers["location"] == "/administratie?ok=company_locked"
    page = client.get("/administratie").text
    assert re.search(rf'value="{c["id"]}" class="choice closed" disabled', page)


def test_locked_company_open_for_its_admin(client, db, company_db):
    a = _company(db, "Eerste", company_db)
    c = _company(db, "Op slot", company_db, locked=True)
    secret = make_user(db, "chef", totp=True, memberships=[(a["id"], "admin", "jan"), (c["id"], "admin", "jan")])
    db.execute("INSERT INTO recent_company SELECT id, %s FROM app_user WHERE login = 'chef'", (c["id"],))
    login(client, "chef", secret)
    menu = _menu(_choose(client, a["id"]).text)
    assert re.search(rf'<button name="company_id" value="{c["id"]}">Op slot <span class="tag">op slot</span>', menu)


def test_search_from_eight_companies(client, db, company, company_db):
    names = ["Voorbeeld Education BV", "Voorbeeld UK Ltd", "Zonnehoek", "Hartman", "Jansen", "Klaassen",
             "De Vries", "Bakker"]
    rows = [_company(db, n, company_db, code=str(100 + i)) for i, n in enumerate(names)]
    make_user(db, "jan", memberships=[(r["id"], "bookkeeper") for r in rows])
    login(client, "jan")
    menu = _menu(_choose(client, rows[2]["id"]).text)
    assert 'placeholder="Andere administratie openen"' in menu
    # alle andere administraties staan klaar voor het zoeken (JS), de huidige niet
    finds = re.findall(r'<li hidden data-find="([^"]*)">', menu)
    assert len(finds) == 7 and "zonnehoek 102" not in " ".join(finds)
    assert "voorbeeld uk ltd 101 " + company_db in finds
    # zonder JavaScript: de keuzepagina filtert op naam, code of database
    def listed(q):
        page = client.get("/administratie", params={"q": q}).text
        return [re.sub(r"\s+", " ", name).strip() for name in
                re.findall(r'value="\d+" class="choice[^"]*">\s*(?:<span class="admcode">\d+</span>)?([^<]+)<', page)]
    assert listed("voorbeeld") == ["Voorbeeld Education BV", "Voorbeeld UK Ltd"]
    assert listed("105") == ["Klaassen"]
    assert listed(company_db[:6].upper()) == sorted(names)
    assert "Geen administratie gevonden" in client.get("/administratie", params={"q": "xyz"}).text


def test_dirty_form_warning_text_in_menu(client, db, company, three):
    login(client, "jan")
    html = _choose(client, company["id"]).text
    assert 'data-leave="Wijzigingen verlaten?' in _menu(html)          # AW3: static/switcher.js vraagt het
    assert '<script src="/static/switcher.js" defer></script>' in html


def test_sysadmin_sets_code(client, db, company):
    secret = make_user(db, "anna", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "jan")])
    login(client, "anna", secret)
    url = f"/systeem/administraties/{company['id']}"
    base = {"name": "Testbedrijf", "dbname": company["dbname"], "mod_gl": "1", "active": "1"}
    r = post(client, url, {**base, "code": "108220"})
    assert r.url.path == url and 'value="108220"' in r.text
    assert db.execute("SELECT code FROM company").fetchone()["code"] == "108220"
    assert '<td class="admcode">108220</td>' in client.get("/systeem/administraties").text
    r = post(client, url, {**base, "code": "1 2"})
    assert r.status_code == 400 and "zonder spaties" in r.text
    r = post(client, url, {**base, "code": "x" * 13})
    assert r.status_code == 400 and "hoogstens 12 tekens" in r.text
    # uniek (hoofdletters tellen niet), leeg mag vaker; elk een eigen database (F23)
    r = post(client, "/systeem/administraties/nieuw",
             {**base, "name": "Tweede", "code": "108220", "dbname": "nog_niet_2"}, page="/systeem/administraties/nieuw")
    assert r.status_code == 400 and "al een administratie met deze code" in r.text
    db.execute("UPDATE company SET code = 'VOORB'")
    r = post(client, "/systeem/administraties/nieuw",
             {**base, "name": "Tweede", "code": "voorb", "dbname": "nog_niet_2"}, page="/systeem/administraties/nieuw")
    assert r.status_code == 400
    for name in ("Derde", "Vierde"):
        r = post(client, "/systeem/administraties/nieuw", {**base, "name": name, "code": "", "dbname": f"nog_niet_{name}"},
                 page="/systeem/administraties/nieuw")
        assert r.status_code == 200
    r = _choose(client, company["id"])
    assert '<span class="admcode">VOORB</span> Testbedrijf' in _menu(r.text)


@pytest.mark.parametrize("lang, texts", [
    ("en", ["Switch company", "Show all", "Leave changes?"]),
    ("de", ["Mandant wechseln", "Alle anzeigen", "Änderungen verwerfen?"]),
    ("fr", ["Changer de dossier", "Tout afficher", "Abandonner les modifications"]),
])
def test_menu_translated(client, db, company, three, lang, texts):
    a, b, c = three
    db.execute("UPDATE app_user SET language = %s WHERE login = 'jan'", (lang,))
    login(client, "jan")
    _choose(client, a["id"])
    menu = _menu(_choose(client, company["id"]).text)
    for t in texts:
        assert t in menu, t
