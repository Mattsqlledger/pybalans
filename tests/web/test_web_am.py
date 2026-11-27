"""Beheer in de webapp (fase 2, A1–A8)."""

from html.parser import HTMLParser

import psycopg
import pytest
from psycopg.rows import dict_row

from webhelpers import csrf, login, make_user, nohelp


class FormFields(HTMLParser):
    """Velden die een browser bij het versturen van een formulier meestuurt."""

    def __init__(self, action):
        super().__init__(convert_charrefs=True)
        self.action, self.inside, self.fields = action, False, []
        self._select = self._textarea = None
        self._first_option = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.inside = a.get("action") == self.action and not self.fields
        if not self.inside:
            return
        if tag == "input" and a.get("name"):
            t = a.get("type", "text")
            if t in ("checkbox", "radio") and "checked" not in a:
                return
            if t in ("submit", "button"):
                return
            self.fields.append([a["name"], a.get("value", "on" if t in ("checkbox", "radio") else "")])
        elif tag == "select":
            self._select = [a.get("name"), None]
            self._first_option = None
        elif tag == "option" and self._select:
            v = a.get("value")
            if self._first_option is None:
                self._first_option = v
            if "selected" in a:
                self._select[1] = v
        elif tag == "textarea":
            self._textarea = [a.get("name"), ""]

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea[1] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select" and self._select:
            self.fields.append([self._select[0], self._select[1] if self._select[1] is not None
                                else (self._first_option or "")])
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            value = self._textarea[1]
            if value.startswith("\n"):      # de HTML-parser van een browser laat de eerste regelovergang weg
                value = value[1:]
            self.fields.append(self._textarea)
            self._textarea[1] = value
            self._textarea = None


def form_of(html, action):
    p = FormFields(action)
    p.feed(html)
    return dict(p.fields)


def submit(client, url, action_url, changes=None, **kw):
    """Open ``url``, neem het formulier naar ``action_url`` over zoals een browser en verstuur het."""
    html = client.get(url).text
    data = form_of(html, action_url)
    assert data, f"geen formulier {action_url} op {url}"
    for k, v in (changes or {}).items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    return client.post(action_url, data=data, follow_redirects=False, **kw)


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def admin(client, db, company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "admin")])
    login(client, "baas", secret)
    return client


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def table(cdb, sql):
    return cdb.execute(sql).fetchall()


def defaults(cdb):
    return {r["fldname"]: r["fldvalue"] for r in table(cdb, "SELECT * FROM defaults")}


def redirected(r, to):
    assert r.status_code == 303, r.text[:3000]
    assert r.headers["location"].startswith(to), r.headers["location"]


# --- menu en rechten -------------------------------------------------------------------

def test_menu_and_roles(admin):
    home = admin.get("/").text
    for url in ("/beheer/instellingen", "/beheer/rekeningschema", "/beheer/btw", "/beheer/valuta",
                "/beheer/koersen", "/beheer/talen", "/beheer/afdelingen", "/beheer/projecten",
                "/beheer/branches", "/beheer/betaalmethoden", "/beheer/afsluiten", "/beheer/audittrail",
                "/beheer/locks"):
        assert f'href="{url}"' in home, url
        assert admin.get(url).status_code == 200, url


def test_bookkeeper(booker):
    home = booker.get("/").text
    assert 'href="/beheer/afdelingen"' in home and 'href="/beheer/instellingen"' not in home
    assert booker.get("/beheer/afdelingen").status_code == 200
    for url in ("/beheer/instellingen", "/beheer/rekeningschema", "/beheer/valuta", "/beheer/talen",
                "/beheer/locks"):
        assert booker.get(url).status_code == 403, url


# --- instellingen (A3) --------------------------------------------------------------------

def test_settings_unchanged_and_changed(admin, cdb):
    cdb.execute("UPDATE defaults SET fldvalue = E'\\nStraat 1\\nPlaats  ' WHERE fldname = 'company'")
    cdb.execute("INSERT INTO defaults VALUES ('address', E'\\nPostbus 1\\r\\n1000 AA  Stad'), ('yearend', '6')")
    before = defaults(cdb)
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen"), "/beheer/instellingen?ok=am_saved")
    assert defaults(cdb) == before
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen",
                      {"company": "Nieuw BV", "cdt": "1", "lock_glnumber": "1", "precision": "3"}),
               "/beheer/instellingen")
    d = defaults(cdb)
    assert (d["company"], d["cdt"], d["lock_glnumber"], d["precision"], d["yearend"], d["closedto"]) == \
        ("Nieuw BV", "1", "1", "3", "6", "20251231")
    r = submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"precision": "x"})
    assert r.status_code == 400 and "Precisie" in r.text


def test_settings_max_upload_size(admin, cdb):
    cdb.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
    page = admin.get("/beheer/instellingen").text
    assert 'name="max_upload_size" value=""' in page and "Leeg = 10 MB" in page
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"max_upload_size": "12"}),
               "/beheer/instellingen?ok=am_saved")
    assert defaults(cdb)["max_upload_size"] == "12"
    assert 'name="max_upload_size" value="12"' in admin.get("/beheer/instellingen").text
    r = submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"max_upload_size": "30"})
    assert r.status_code == 400 and "een heel getal van 1 tot en met 25 MB" in r.text
    assert defaults(cdb)["max_upload_size"] == "12"



def test_settings_referenceurl_hidden_value_kept(admin, cdb):
    """VW3: "Map/url referentiedocumenten" staat niet meer op het scherm; de waarde blijft in de database."""
    cdb.execute("INSERT INTO defaults VALUES ('referenceurl', 'file:///docs/')")
    page = nohelp(admin.get("/beheer/instellingen").text)
    assert 'name="referenceurl"' not in page and "referentiedocumenten" not in page
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"company": "Nieuw BV"}),
               "/beheer/instellingen?ok=am_saved")
    d = defaults(cdb)
    assert (d["company"], d["referenceurl"]) == ("Nieuw BV", "file:///docs/")

# --- rekeningschema (A1) --------------------------------------------------------------------

def test_accounts(admin, cdb):
    page = admin.get("/beheer/rekeningschema").text
    assert "Debiteuren" in page and 'href="/beheer/rekeningschema/10057"' in page
    before = table(cdb, "SELECT * FROM chart ORDER BY id")
    for cid in (10044, 10057, 10076, 10001, 10171):        # betaal-, AR-, btw-rekening, kop, charttype 'E'
        redirected(submit(admin, f"/beheer/rekeningschema/{cid}", "/beheer/rekeningschema",
                          {"action": "save"}), f"/beheer/rekeningschema/{cid}")
    after = {r["id"]: r for r in table(cdb, "SELECT * FROM chart ORDER BY id")}
    for r in before:
        if r["id"] != 10171:
            assert after[r["id"]] == r
    assert after[10171]["charttype"] == "A"          # 'E' is geen geldige soort; het scherm maakt er 'A' van
    # nieuw
    r = submit(admin, "/beheer/rekeningschema/nieuw", "/beheer/rekeningschema",
               {"accno": "4996", "description": "Nieuw", "category": "E", "link_AP_amount": "1", "action": "save"})
    redirected(r, "/beheer/rekeningschema/")
    new = int(r.headers["location"].split("/")[-1].split("?")[0])
    assert table(cdb, f"SELECT link FROM chart WHERE id = {new}") == [{"link": "AP_amount"}]
    # fout: dubbel nummer
    r = submit(admin, "/beheer/rekeningschema/nieuw", "/beheer/rekeningschema",
               {"accno": "4996", "category": "E", "action": "save"})
    assert r.status_code == 400 and "bestaat al" in r.text
    # verwijderen met bevestiging; rekening met boekingen heeft geen knop en wordt op de server geweigerd
    page = admin.get(f"/beheer/rekeningschema/{new}").text
    assert 'value="delete"' in page
    r = submit(admin, f"/beheer/rekeningschema/{new}", "/beheer/rekeningschema", {"action": "delete"})
    assert "verwijderen?" in r.text
    redirected(submit(admin, f"/beheer/rekeningschema/{new}", "/beheer/rekeningschema",
                      {"action": "delete_confirmed"}), "/beheer/rekeningschema?ok=am_deleted")
    assert 'value="delete"' not in admin.get("/beheer/rekeningschema/10057").text
    r = submit(admin, "/beheer/rekeningschema/10057", "/beheer/rekeningschema", {"action": "delete_confirmed"})
    assert r.status_code == 400 and "geboekt" in r.text


# --- btw (A2) ---------------------------------------------------------------------------------

def test_taxes(admin, cdb):
    before = table(cdb, "SELECT * FROM tax ORDER BY chart_id, validto, rate")
    redirected(submit(admin, "/beheer/btw", "/beheer/btw", {"action": "save"}), "/beheer/btw?ok=am_saved")
    assert table(cdb, "SELECT * FROM tax ORDER BY chart_id, validto, rate") == before
    # nieuw tarief voor 1801 per datum (BT1): het huidige krijgt vanzelf de einddatum
    html = admin.get("/beheer/btw").text
    assert 'name="newrate_10077"' in html and "add_10077" not in html
    data = form_of(html, "/beheer/btw")
    data.update({"newrate_10077": "7,5", "newstart_10077": "2026-07-01"})
    redirected(admin.post("/beheer/btw", data={**data, "action": "save"}, follow_redirects=False), "/beheer/btw")
    assert table(cdb, "SELECT rate, validto FROM tax WHERE chart_id = 10077 ORDER BY validto") == [
        {"rate": 0.06, "validto": __import__("datetime").date(2026, 6, 30)}, {"rate": 0.075, "validto": None}]
    # alleen een percentage: melding
    data = form_of(admin.get("/beheer/btw").text, "/beheer/btw")
    r = admin.post("/beheer/btw", data={**data, "newrate_10077": "8", "action": "save"})
    assert r.status_code == 400 and "zowel het percentage als de ingangsdatum" in r.text
    # een gebruikt tarief overschrijven wordt geweigerd: 1801 gebruikt op 15-06-2026, 6% wordt 7%
    cdb.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate) VALUES (3001, 10077, 0.6, '2026-06-15')")
    i = min(int(k.split("_")[-1]) for k, v in data.items() if k.startswith("chart_id_") and v == "10077")
    assert data[f"validto_{i}"] == "2026-06-30"
    before = table(cdb, "SELECT * FROM tax ORDER BY chart_id, validto, rate")
    r = admin.post("/beheer/btw", data={**data, f"rate_{i}": "7", "action": "save"})
    assert r.status_code == 400 and "al gebruikt in boekingen tot en met 15-06-2026" in r.text
    assert table(cdb, "SELECT * FROM tax ORDER BY chart_id, validto, rate") == before


# --- afsluiten + slot (A4, A7) -------------------------------------------------------------------

def test_closing_and_company_lock(admin, cdb, client, db, company):
    redirected(submit(admin, "/beheer/afsluiten", "/beheer/afsluiten",
                      {"closedto": "2026-03-31", "revtrans": "1", "audittrail": None, "action": "save"}),
               "/beheer/afsluiten")
    d = defaults(cdb)
    assert d["closedto"] == "20260331" and d["revtrans"] == "1" and "audittrail" not in d
    cdb.execute("UPDATE defaults SET fldvalue = '20173112' WHERE fldname = 'closedto'")
    page = admin.get("/beheer/afsluiten").text
    assert "20173112" in page
    redirected(submit(admin, "/beheer/afsluiten", "/beheer/afsluiten", {"action": "save"}), "/beheer/afsluiten")
    assert defaults(cdb)["closedto"] == "20173112"
    # op slot
    redirected(submit(admin, "/beheer/afsluiten", "/beheer/afsluiten",
                      {"lock_message": "Controle accountant", "action": "lock"}), "/beheer/afsluiten")
    assert db.execute("SELECT locked, lock_message FROM company").fetchone() == \
        {"locked": True, "lock_message": "Controle accountant"}
    assert admin.get("/beheer/afsluiten").status_code == 200        # beheerder kan er nog in
    admin.cookies.clear()
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    r = login(client, "boek")
    page = client.get("/").text                       # niet automatisch gekozen: keuzescherm
    assert "op slot" in page and "Controle accountant" in page
    r = client.post("/administratie", data={"csrf": csrf(client, page), "company_id": str(company["id"])},
                    follow_redirects=False)
    redirected(r, "/administratie?ok=company_locked")
    db.execute("UPDATE app_session SET company_id = %s", (company["id"],))   # bv. al gekozen vóór het slot
    r = client.get("/memoriaal/nieuw", follow_redirects=False)
    redirected(r, "/administratie?ok=company_locked")
    assert "op slot gezet" in client.get(r.headers["location"]).text
    db.execute("UPDATE company SET locked = false")
    assert "Memoriaalboeking" in client.get("/memoriaal/nieuw").text


# --- valuta en koersen (A5) ------------------------------------------------------------------------

def test_currencies(admin, cdb):
    def post(data):
        return submit(admin, "/beheer/valuta", "/beheer/valuta", data)
    redirected(post({"curr": "gbp", "prec": "2", "action": "add"}), "/beheer/valuta")
    redirected(post({"curr": "JPY", "action": "add"}), "/beheer/valuta")
    r = post({"curr": "EUR", "action": "add"})
    assert r.status_code == 400 and "bestaat al" in r.text
    page = admin.get("/beheer/valuta").text
    assert "standaard" in page
    r = admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "USD", "action": "up"})
    assert r.status_code == 400 and "bovenaan" in r.text
    admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "JPY", "action": "up"})
    assert [r["curr"].strip() for r in table(cdb, "SELECT curr FROM curr ORDER BY rn")] == ["EUR", "USD", "JPY", "GBP"]
    r = admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "GBP", "action": "default"})
    assert "kan geen standaardvaluta worden" in r.text and "geboekt" in r.text
    r = admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "GBP", "action": "default_confirmed"})
    assert r.status_code == 400
    r = admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "JPY", "action": "delete"})
    assert "verwijderen?" in r.text
    admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "JPY", "action": "delete_confirmed"})
    # VW4: decimalen alleen-lezen met uitleg; wijzigen kan niet meer
    r = admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "GBP", "prec_GBP": "3", "action": "prec"})
    assert r.status_code == 400
    page = nohelp(admin.get("/beheer/valuta").text)
    assert 'name="prec' not in page and "PyBalans rekent met de algemene precisie" in page
    assert [(r["curr"].strip(), r["rn"], r["prec"]) for r in table(cdb, "SELECT * FROM curr ORDER BY rn")] == \
        [("EUR", 1, 2), ("USD", 2, 2), ("GBP", 3, 2)]


def test_default_currency_new_admin(admin, cdb):
    for t in ("acc_trans", "ar", "ap", "gl", "oe", "exchangerate"):
        cdb.execute(f"DELETE FROM {t}")
    page = admin.get("/beheer/valuta").text
    r = admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "USD", "action": "default"})
    assert "wordt de standaardvaluta" in r.text
    redirected(admin.post("/beheer/valuta", data={"csrf": csrf(admin, page), "curr": "USD",
                                                  "action": "default_confirmed"}, follow_redirects=False),
               "/beheer/valuta")
    assert [r["curr"].strip() for r in table(cdb, "SELECT curr FROM curr ORDER BY rn")] == ["USD", "EUR"]


def test_rates(admin, cdb):
    page = admin.get("/beheer/koersen").text
    assert 'href="/beheer/koersen/datum?datum=2026-06-30"' in page and "1,25" in page
    before = table(cdb, "SELECT * FROM exchangerate ORDER BY transdate")
    redirected(submit(admin, "/beheer/koersen/datum?datum=2026-06-30", "/beheer/koersen/datum",
                      {"action": "save"}), "/beheer/koersen/datum?datum=2026-06-30")
    assert table(cdb, "SELECT * FROM exchangerate ORDER BY transdate") == before
    redirected(submit(admin, "/beheer/koersen/datum", "/beheer/koersen/datum",
                      {"transdate": "2026-07-01", "rate_USD": "1,3", "action": "save"}), "/beheer/koersen/datum")
    redirected(submit(admin, "/beheer/koersen/datum?datum=2026-07-01", "/beheer/koersen/datum",
                      {"transdate": "2026-07-02", "action": "save"}), "/beheer/koersen/datum?datum=2026-07-02")
    rows = table(cdb, "SELECT curr, transdate::text AS d, exchangerate FROM exchangerate ORDER BY transdate")
    assert [(r["d"], r["exchangerate"]) for r in rows] == [("2026-05-20", 1.1), ("2026-06-30", 1.25),
                                                          ("2026-07-02", 1.3)]
    redirected(submit(admin, "/beheer/koersen/datum?datum=2026-07-02", "/beheer/koersen/datum",
                      {"action": "delete_confirmed"}), "/beheer/koersen?ok=am_deleted")
    assert len(table(cdb, "SELECT * FROM exchangerate")) == 2
    assert "Geen koersen" in admin.get("/beheer/koersen?valuta=USD&van=2027-01-01").text


# --- eenvoudige lijsten (A6) ------------------------------------------------------------------

def test_departments(booker, cdb):
    redirected(submit(booker, "/beheer/afdelingen/nieuw", "/beheer/afdelingen",
                      {"description": "Service", "role": "C", "action": "save"}), "/beheer/afdelingen")
    new = table(cdb, "SELECT id FROM department WHERE description = 'Service'")[0]["id"]
    before = table(cdb, "SELECT * FROM department ORDER BY id")
    redirected(submit(booker, "/beheer/afdelingen/600", "/beheer/afdelingen", {"action": "save"}),
               "/beheer/afdelingen")
    assert table(cdb, "SELECT * FROM department ORDER BY id") == before
    page = booker.get("/beheer/afdelingen").text
    booker.post("/beheer/afdelingen", data={"csrf": csrf(booker, page), "key": str(new), "action": "up"})
    assert [r["id"] for r in table(cdb, "SELECT id FROM department ORDER BY rn")] == [600, new, 601]
    r = submit(booker, "/beheer/afdelingen/601", "/beheer/afdelingen", {"action": "delete_confirmed"})
    assert r.status_code == 400 and "in gebruik" in r.text
    redirected(submit(booker, f"/beheer/afdelingen/{new}", "/beheer/afdelingen", {"action": "delete_confirmed"}),
               "/beheer/afdelingen?ok=am_deleted")


def test_projects_languages_business_paymentmethods(admin, cdb):
    redirected(submit(admin, "/beheer/projecten/nieuw", "/beheer/projecten",
                      {"description": "Nieuw project", "startdate": "2026-01-01", "customer_id": "900",
                       "kind": "customer", "action": "save"}), "/beheer/projecten")     # soort verplicht (v1.1 IN1)
    p = table(cdb, "SELECT projectnumber, customer_id FROM project WHERE description = 'Nieuw project'")
    assert p == [{"projectnumber": "1", "customer_id": 900}]
    before = table(cdb, "SELECT * FROM project ORDER BY id")
    redirected(submit(admin, "/beheer/projecten/700", "/beheer/projecten", {"action": "save"}), "/beheer/projecten")
    assert table(cdb, "SELECT * FROM project ORDER BY id") == before
    assert "Nieuw project" not in admin.get("/beheer/projecten?status=inactive").text
    # talen
    redirected(submit(admin, "/beheer/talen/nieuw", "/beheer/talen",
                      {"code": "de", "description": "Duits  (DE)", "action": "save"}), "/beheer/talen")
    assert table(cdb, "SELECT description FROM language WHERE code = 'de'") == [{"description": "Duits (DE)"}]
    r = submit(admin, "/beheer/talen/en", "/beheer/talen", {"code": "eng", "action": "save"})
    assert r.status_code == 400 and "in gebruik" in r.text
    # branches en betaalmethoden; korting (VW5) en kosten (VW6) staan niet meer op het scherm en blijven staan
    redirected(submit(admin, "/beheer/branches/nieuw", "/beheer/branches",
                      {"description": "Horeca", "action": "save"}), "/beheer/branches")
    assert table(cdb, "SELECT discount FROM business") == [{"discount": 0}]
    bid = table(cdb, "SELECT id FROM business")[0]["id"]
    cdb.execute("UPDATE business SET discount = 0.025")
    before = table(cdb, "SELECT * FROM business")
    for url in ("/beheer/branches", f"/beheer/branches/{bid}"):
        page = nohelp(admin.get(url).text)
        assert "Korting" not in page and 'name="discount"' not in page and "2,5" not in page
    redirected(submit(admin, f"/beheer/branches/{bid}", "/beheer/branches", {"action": "save"}), "/beheer/branches")
    assert table(cdb, "SELECT * FROM business") == before
    redirected(submit(admin, f"/beheer/branches/{bid}", "/beheer/branches",
                      {"description": "Horeca en catering", "action": "save"}), "/beheer/branches")
    assert table(cdb, "SELECT description, discount FROM business") == \
        [{"description": "Horeca en catering", "discount": 0.025}]
    redirected(submit(admin, "/beheer/betaalmethoden/nieuw", "/beheer/betaalmethoden",
                      {"description": "Contant", "roundchange": "0.05", "action": "save"}),
               "/beheer/betaalmethoden")
    pm = table(cdb, "SELECT id, fee, roundchange, rn FROM paymentmethod")
    assert [(r["fee"], r["roundchange"], r["rn"]) for r in pm] == [(0, 0.05, 1)]
    cdb.execute("UPDATE paymentmethod SET fee = 0.1")
    for url in ("/beheer/betaalmethoden", f"/beheer/betaalmethoden/{pm[0]['id']}"):
        page = nohelp(admin.get(url).text)
        assert ">Kosten<" not in page and 'name="fee"' not in page and "Afronding" in page
    before = table(cdb, "SELECT * FROM paymentmethod")
    redirected(submit(admin, f"/beheer/betaalmethoden/{pm[0]['id']}", "/beheer/betaalmethoden", {"action": "save"}),
               "/beheer/betaalmethoden")
    assert table(cdb, "SELECT * FROM paymentmethod") == before
    redirected(submit(admin, f"/beheer/betaalmethoden/{pm[0]['id']}", "/beheer/betaalmethoden",
                      {"roundchange": "0.1", "action": "save"}), "/beheer/betaalmethoden")
    assert [(r["fee"], r["roundchange"]) for r in table(cdb, "SELECT fee, roundchange FROM paymentmethod")] == \
        [(0.1, 0.1)]

# --- audittrail en locks (A7), fxgainloss (A8) ------------------------------------------------------

def test_audit_and_locks(admin, cdb):
    redirected(submit(admin, "/beheer/talen/nieuw", "/beheer/talen",
                      {"code": "fr", "description": "Frans", "action": "save"}), "/beheer/talen")
    page = admin.get("/beheer/audittrail?tabel=language").text
    assert "<td>fr</td>" in page and "saved" in page
    r = admin.get("/beheer/audittrail?csv=1&tabel=language")
    assert r.headers["content-type"].startswith("text/csv") and ";fr;" in r.text
    cdb.execute("INSERT INTO semaphore VALUES (5001, 'jan', 'gl', '9999999999'), (950, 'admin', 'ar', '1')")
    page = admin.get("/beheer/locks").text
    assert "Jan Jansen" in page and "(verlopen)" in page
    admin.post("/beheer/locks", data={"csrf": csrf(admin, page), "id": "5001", "login": "jan", "action": "release"})
    assert [r["id"] for r in table(cdb, "SELECT id FROM semaphore")] == [950]
    r = admin.post("/beheer/locks", data={"csrf": csrf(admin, page), "action": "release_all"})
    assert "Alle locks vrijgeven?" in r.text
    admin.post("/beheer/locks", data={"csrf": csrf(admin, page), "action": "release_all_confirmed"})
    assert table(cdb, "SELECT id FROM semaphore") == []


def test_fxgainloss_fixed_on_select(client, db, company, cdb):
    cdb.execute("DELETE FROM defaults WHERE fldname = 'fxgainloss_accno_id'")
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "admin")])
    login(client, "baas", secret)
    client.get("/")
    assert defaults(cdb)["fxgainloss_accno_id"] == "10195"


# --- menu productie (v1.0.1 PB20) ------------------------------------------------------------

PRODUCTION_LINKS = ('href="/artikelen-assembleren"', 'href="/artikelen-stuklijsten"', 'href="/artikelen-productie"',
                    'href="/artikelen/nieuw?soort=samengesteld"')


def _menu(client):
    return client.get("/").text.split('<nav class="menu"', 1)[1].split("</nav>", 1)[0]


def test_production_menu_follows_assemblies_and_setting(admin, cdb, company):
    from sqlledger_web import production
    nav = _menu(admin)
    assert 'href="/artikelen"' in nav
    assert not [u for u in PRODUCTION_LINKS if u in nav]           # sl_template: geen samengestelde artikelen
    # directe adressen blijven werken
    assert admin.get("/artikelen-assembleren").status_code == 200
    assert admin.get("/artikelen/nieuw?soort=samengesteld").status_code == 200

    # schakelaar in Beheer > Instellingen
    page = admin.get("/beheer/instellingen").text
    assert "Productie (samengestelde artikelen) tonen" in page
    assert 'name="shown_show_production" value="0"' in page
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"show_production": "1"}),
               "/beheer/instellingen?ok=am_saved")
    assert cdb.execute("SELECT value FROM x_setting WHERE key = 'show_production'").fetchone()["value"] == "1"
    nav = _menu(admin)
    assert all(u in nav for u in PRODUCTION_LINKS)
    # opnieuw opslaan zonder wijziging laat het staan; uitvinken zet het weer uit
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen"), "/beheer/instellingen?ok=am_saved")
    assert all(u in _menu(admin) for u in PRODUCTION_LINKS)
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"show_production": None}),
               "/beheer/instellingen?ok=am_saved")
    assert not [u for u in PRODUCTION_LINKS if u in _menu(admin)]

    # een samengesteld artikel zet het vanzelf aan (na opslaan van een artikel of na de bewaartijd)
    cdb.execute("UPDATE parts SET assembly = true WHERE id = (SELECT min(id) FROM parts)")
    production.forget(company)
    assert all(u in _menu(admin) for u in PRODUCTION_LINKS)
    page = admin.get("/beheer/instellingen").text
    assert "Staat aan, want deze administratie heeft samengestelde artikelen." in page
    assert "shown_show_production" not in page


def test_production_menu_without_inventory_module(admin, db, company):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["ar", "ap", "gl"], company["id"]))
    page = admin.get("/beheer/instellingen").text
    assert "show_production" not in page
