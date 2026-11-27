"""Vertalingen: elke gemarkeerde tekst heeft een Engelse vertaling, geen losse
Nederlandse tekst in de sjablonen, en een gebruiker met taal "en" ziet Engels."""

import ast
import re
from pathlib import Path

import pytest
from jinja2 import Environment, nodes

from sqlledger_web import i18n
from sqlledger_web.i18n_en import EN, LIBRARY

from webhelpers import login, make_user

WEB = Path(i18n.__file__).parent
TEMPLATES = sorted((WEB / "templates").rglob("*.html"))
MARKERS = {"_", "N_", "gettext"}
#: helpteksten (helptext.py): ht = zin, hl = knop-/veldnaam, hm = menupad (alle argumenten zijn teksten)
HELP_MARKERS = {"ht", "hl", "hm", "go"}   # go: menupad op de pagina Help (routes_help.py), eerste argument = adres


# ------------------------------------------------------------------ extractor

def _walk(node, parent=None):
    yield node, parent
    for child in node.iter_child_nodes():
        yield from _walk(child, node)


def _is_marker_call(call) -> bool:
    return isinstance(call, nodes.Call) and isinstance(call.node, nodes.Name) and call.node.name in MARKERS


def template_strings(help_labels: bool = True) -> dict:
    """Alle letterlijke ``_("…")``/``N_("…")``-teksten uit de sjablonen: tekst -> vindplaatsen.

    Ook de teksten in ``ht``/``hl``/``hm`` (helpteksten); met ``help_labels=False`` zonder ``hl``/``hm``,
    om te kunnen nagaan dat die namen ook buiten de help bestaan."""
    env = Environment()
    found: dict = {}
    for path in TEMPLATES:
        tree = env.parse(path.read_text())
        for node, parent in _walk(tree):
            if not (isinstance(node, nodes.Call) and isinstance(node.node, nodes.Name)):
                continue
            name = node.node.name
            if name in MARKERS or name == "ht":
                args = node.args[:1]
            elif name in HELP_MARKERS and help_labels:
                args = node.args[1:] if name == "go" else node.args
            else:
                continue
            for arg in args:
                if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
                    found.setdefault(arg.value, []).append(f"{path.relative_to(WEB)}:{node.lineno}")
    return found


def help_labels() -> dict:
    """Knop-, veld- en menunamen uit ``hl``/``hm`` in de helpteksten: tekst -> vindplaatsen."""
    env = Environment()
    found: dict = {}
    for path in sorted((WEB / "templates" / "help").glob("*.html")):
        for node, parent in _walk(env.parse(path.read_text())):
            if isinstance(node, nodes.Call) and isinstance(node.node, nodes.Name) and node.node.name in ("hl", "hm", "go"):
                for arg in node.args[1:] if node.node.name == "go" else node.args:
                    if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
                        found.setdefault(arg.value, []).append(f"{path.relative_to(WEB)}:{node.lineno}")
    return found


def python_strings() -> dict:
    """Alle letterlijke ``_("…")``/``N_("…")``-teksten uit sqlledger_web/*.py."""
    found: dict = {}
    for path in sorted(WEB.glob("*.py")):
        if path.name in ("i18n.py", "i18n_en.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name in MARKERS:
                    found.setdefault(node.args[0].value, []).append(f"{path.name}:{node.lineno}")
    return found


def all_strings(help_labels: bool = True) -> dict:
    found = template_strings(help_labels)
    for text, where in python_strings().items():
        found.setdefault(text, []).extend(where)
    return found


def test_help_labels_exist_on_screens():
    """Een knop-, veld- of menunaam in de help (``hl``/``hm``) is dezelfde tekst als op een scherm of in het
    menu, zodat de help meeloopt als een label verandert (HELP_ANALYSE HL3)."""
    labels = help_labels()
    assert labels
    elsewhere = {t for t, where in all_strings(help_labels=False).items()
                 if any(not w.startswith("templates/help/") for w in where)}
    elsewhere.add("Hoe werkt het?")   # de kop van elk helpblok; de pagina Help verwijst ernaar
    missing = {t: w[:2] for t, w in labels.items() if t not in elsewhere}
    assert not missing, f"helplabels die nergens op een scherm staan: {missing}"


# ------------------------------------------------------------------ (a) catalogus volledig

def test_every_string_has_english_translation():
    found = all_strings()
    assert len(found) > 1000
    missing = {t: w[:2] for t, w in found.items() if not EN.get(t)}
    assert not missing, f"{len(missing)} teksten zonder Engelse vertaling: {list(missing.items())[:30]}"


def test_placeholders_match():
    """Dezelfde {plaatshouders} in het Nederlands en het Engels."""
    fields = lambda s: sorted(re.findall(r"\{(\w*)\}", s))  # noqa: E731
    wrong = [(nl, en) for nl, en in {**EN, **LIBRARY}.items() if fields(nl) != fields(en)]
    assert not wrong, wrong[:20]


def test_no_stale_catalog_entries():
    found = all_strings()
    stale = [t for t in EN if t not in found]
    assert not stale, f"vertalingen zonder gebruik: {stale[:30]}"


def test_library_messages_translated_in_catalog():
    """Bibliotheekmeldingen (exacte tekst) zitten in de catalogus en gettext vindt ze."""
    assert set(LIBRARY) <= set(i18n.CATALOGS["en"])
    token = i18n.current_language.set("en")
    try:
        for nl, en in list(LIBRARY.items())[:5]:
            assert i18n.gettext(nl) == en
    finally:
        i18n.current_language.reset(token)


# ------------------------------------------------------------------ (b) geen losse tekst

#: zichtbare tekst die in elke taal hetzelfde is
ALLOWED_TEXT = {"· Matthijs Havinga · © 2026", "SQL-Ledger 3.2.12 · Dieter Simader (DWS Systems Inc.)", "SQL-Ledger", "· SQL-Ledger", "PyBalans", "· PyBalans", "GPL-2.0-or-later", "CSV", "Excel", "PDF", "FX", "IP", "· 2FA", "(SQL-Ledger )", "MB.",
                "XXXX-XXXX-XXXX", "FX  "}
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def test_no_untranslated_text_in_templates():
    problems = []
    for path in TEMPLATES:
        src = re.sub(r"<script.*?</script>|<style.*?</style>", "", path.read_text(), flags=re.S)
        src = JINJA.sub(" ", src)
        for m in re.finditer(r"\b(title|placeholder|aria-label|alt)=\"([^\"]*)\"", src):
            if re.search(r"[A-Za-z]{2,}", m.group(2)) and m.group(2).strip() not in ALLOWED_TEXT \
                    and m.group(2) not in ALLOWED_TEXT:
                problems.append(f"{path.name}: {m.group(1)}={m.group(2)!r}")
        for line in re.sub(r"<[^>]*>", "\n", src).splitlines():
            t = re.sub(r"\s+", " ", line.strip())
            if re.search(r"[A-Za-zë]{2,}", t) and t not in ALLOWED_TEXT:
                problems.append(f"{path.name}: {t!r}")
    assert not problems, problems


#: letterlijke teksten in sjabloon-expressies die geen zichtbare tekst zijn
ALLOWED_CONST = {" current", " bad", " single", "/systeem/", "/afletteren", "/afletteren/rapport", " disabled", " nowrap", "inv.calcdiscount",
                 "CAMT.053", "MT940"}


def test_no_unmarked_literals_in_template_expressions():
    """Teksten met een hoofdletter of spatie in ``{{ }}``/``{% %}`` horen in _() of N_()."""
    env = Environment()
    problems = []
    for path in TEMPLATES:
        for node, parent in _walk(env.parse(path.read_text())):
            if not (isinstance(node, nodes.Const) and isinstance(node.value, str)):
                continue
            v = node.value
            if v in ALLOWED_CONST or not re.search(r"[A-Za-z]{2,}", v) or not re.search(r"[A-Z ]", v):
                continue
            if _is_marker_call(parent) and parent.args and parent.args[0] is node:
                continue
            if isinstance(parent, nodes.Call) and isinstance(parent.node, nodes.Name) \
                    and parent.node.name == "pgettext":
                continue
            if isinstance(parent, nodes.Call) and isinstance(parent.node, nodes.Name) \
                    and parent.node.name in HELP_MARKERS and node in parent.args:
                continue
            if isinstance(parent, (nodes.Extends, nodes.Include, nodes.FromImport, nodes.Import)):
                continue
            problems.append(f"{path.name}:{node.lineno}: {v!r}")
    assert not problems, problems


# ------------------------------------------------------------------ (c) Engels in de app

@pytest.fixture
def english(client, db, company):
    secret = make_user(db, "admin", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "jan")])
    db.execute("UPDATE app_user SET language = 'en' WHERE login = 'admin'")
    login(client, "admin", secret)
    page = client.get("/administratie").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/administratie", data={"csrf": csrf, "company_id": company["id"]})
    return client


def _title(html: str) -> str:
    return re.search(r"<title>(.*?) · PyBalans</title>", html, re.S).group(1).strip()


@pytest.mark.parametrize("url, title, texts", [
    ("/memoriaal/nieuw", "Add GL transaction", ["Transaction date", "Account", "Debit", "Credit", "Save"]),
    ("/memoriaal/20101", "Edit GL transaction", ["Save as new", "Delete"]),
    ("/verkoop/nieuw", "Add AR transaction", ["Customer", "Invoice date", "Due date"]),
    ("/betalingen", "Payments", ["Payment account", "Due date from"]),
    ("/rapporten/proefbalans", "Trial balance", ["Show"]),
    ("/rapporten/balans?run=1", "Balance sheet", ["Total assets"]),
    ("/klanten", "Customers", ["Search"]),
    ("/beheer/instellingen", "Defaults", ["Company name"]),
    ("/bank/inlezen", "Import bank statement", ["Bank account"]),
    ("/afletteren", "Reconciliation", ["Account"]),
    ("/terugkerend", "Recurring transactions", ["Process"]),
])
def test_english_pages(english, url, title, texts):
    r = english.get(url)
    assert r.status_code == 200, url
    html = r.text
    assert '<html lang="en">' in html
    assert _title(html) == title
    for t in texts:
        assert t in html, (url, t)
    # menu in het Engels
    for item in ("General Ledger", "Add transaction", "Trial balance", "Reconciliation", "Logout"):
        assert item in html, (url, item)
    for nl in ("Boeking toevoegen", "Proefbalans", "Uitloggen", "Afletteren"):
        assert nl not in html, (url, nl)


def test_english_notice_and_error(english):
    r = english.get("/instellingen?ok=saved")
    assert "Saved." in r.text
    assert "Page not found." in english.get("/bestaat-niet").text


def test_dutch_is_default(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    html = client.get("/administratie").text
    assert '<html lang="nl">' in html and "Uitloggen" in html


# ------------------------------------------------------------------ (d) Duits en Frans (stap 2b, TL1/TL8)

from sqlledger_web import i18n_de, i18n_fr, libmsg  # noqa: E402
from sqlledger_web.i18n_en import CONTEXT  # noqa: E402

OTHER = {"de": i18n_de, "fr": i18n_fr}
_fields = lambda s: sorted(re.findall(r"\{(\w*)\}", s))  # noqa: E731
#: teksten die in het Duits/Frans terecht gelijk zijn aan het Nederlands (zelfde woord of code)
SAME_AS_NL = {
    "de": {"Logo", "Code", "Login", "Varianten", "Fax", "Cc", "Bcc", "Bank", "IBAN", "BIC", "SIC", "Clearingnummer",
           "Methode", "Posten", "Stornodatum", "CSV", "Website", "Server", "Details", "Barcode", "Gewicht", "Artikel",
           "Artikelnummer", "Nr.", "Status", "Saldo", "Branche", "Branches", "Project", "PDF", "E-mail", "Totaal",
           "Storno", "Passiva", "alle", "Alle", "GIFI", "Nummer", "Memo", "Datum", "Netto", "automatisch", "beide",
           "Detail", "Startdatum", "Land", "Titel", "Nr", "Kit", "Kits", "ja", "Dashboard", "{year}: {amount}", "Startcode",
           "Copyright", "Laden", "Klein", "Links", "Rechts", "Petrol", "Bordeaux", "RGS", "Codes"},
    "fr": {"Copyright", "Bordeaux", "Logo", "Code", "Login", "Menu", "Fax", "Cc", "Bcc", "IBAN", "BIC", "SIC", "CSV", "Kit", "Kits", "Module",
           "Modules", "Total", "PDF", "Session", "Signature", "Date", "Description", "Type", "Message", "Port", "Version",
           "E-mail", "Rapport", "GIFI", "Niveau", "Via", "Document", "RGS", "Codes", "D/C"},
}
# v1.1 interne uren: in het Duits/Frans hetzelfde woord
SAME_AS_NL["de"] |= {"intern", "Marge", "Box"}
SAME_AS_NL["fr"] |= {"Marge"}
# naam van het Britse formulier (BTW_LANDEN_ANALYSE.md BL10): in elke taal "VAT Return"
for _lang in ("de", "fr"):
    SAME_AS_NL[_lang] |= {"VAT Return {period}"}


def _pairs(lang):
    mod = OTHER[lang]
    yield from ((nl, t, "tekst") for nl, t in {**mod.LIBRARY, **mod.TEXTS}.items())
    yield from ((nl, t, f"context {c}") for c, d in mod.CONTEXT.items() for nl, t in d.items())
    yield from ((libmsg.TEMPLATES[src][0], t, "melding") for src, t in mod.LIBMSG.items())


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_every_string_translated(lang):
    mod = OTHER[lang]
    missing = [t for t in all_strings() if not mod.TEXTS.get(t)]
    assert not missing, f"{len(missing)} teksten zonder vertaling ({lang}): {missing[:20]}"
    assert set(mod.LIBRARY) == set(LIBRARY)
    assert {c: set(d) for c, d in mod.CONTEXT.items()} == {c: set(d) for c, d in CONTEXT.items()}
    assert set(mod.LIBMSG) == set(libmsg.TEMPLATES), "elk meldingsjabloon heeft een vertaling"
    assert set(mod.TERMS) == set(libmsg.TERMS)
    assert [nl for nl, __ in mod.FRAGMENTS] == [nl for nl, __ in libmsg.FRAGMENTS_EN]
    stale = [t for t in mod.TEXTS if t not in EN]
    assert not stale, f"vertalingen zonder gebruik ({lang}): {stale[:20]}"


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_placeholders_and_spacing(lang):
    wrong = [(nl, t, k) for nl, t, k in _pairs(lang)
             if _fields(nl) != _fields(t) or nl[:1].isspace() != t[:1].isspace()
             or nl[-1:].isspace() != t[-1:].isspace() or not t.strip()]
    assert not wrong, wrong[:20]


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_nothing_left_in_dutch(lang):
    same = [nl for nl, t, __ in _pairs(lang)
            if t == nl and re.search(r"[A-Za-z]{2,}", nl) and nl.strip(" .:") not in SAME_AS_NL[lang]]
    assert not same, f"{len(same)} teksten onvertaald ({lang}): {same[:30]}"


@pytest.mark.parametrize("header, expected", [
    (None, "nl"), ("", "nl"), ("de-DE,de;q=0.9,en;q=0.8", "de"), ("fr-FR", "fr"), ("en-GB,en;q=0.9", "en"),
    ("es-ES,fr;q=0.5,de;q=0.7", "de"), ("nl-BE", "nl"), ("ja, zh", "nl"), ("*", "nl"), ("de;q=0", "nl"),
    ("x" * 5000, "nl"),
])
def test_browser_language(header, expected):
    assert i18n.browser_language(header) == expected


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_library_message_in_language(lang):
    token = i18n.current_language.set(lang)
    try:
        text = libmsg.lib_message("Transaction is locked by jan")
        assert "jan" in text and text == OTHER[lang].LIBMSG["Transaction is locked by {holder}"].format(holder="jan")
        assert libmsg.lib_message("customer 7 not found") == \
            OTHER[lang].LIBMSG["{vc} {vc_id} not found"].format(vc_id="7") \
            if "{vc}" not in OTHER[lang].LIBMSG["{vc} {vc_id} not found"] else True
    finally:
        i18n.current_language.reset(token)


def test_login_page_follows_browser(client, db):
    make_user(db, "anna")                   # zonder gebruikers: naar /welkom (eerste start)
    for header, lang in (("de-DE,de;q=0.9", "de"), ("fr-FR,fr;q=0.9", "fr"), ("es", "nl")):
        html = client.get("/login", headers={"Accept-Language": header}).text
        assert f'<html lang="{lang}">' in html
        assert (OTHER[lang].TEXTS.get("Inloggen") if lang != "nl" else "Inloggen") in html


@pytest.fixture(params=["de", "fr"])
def foreign(request, client, db, company):
    secret = make_user(db, "admin", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "jan")])
    db.execute("UPDATE app_user SET language = %s WHERE login = 'admin'", (request.param,))
    login(client, "admin", secret)
    page = client.get("/administratie").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/administratie", data={"csrf": csrf, "company_id": company["id"]})
    return client, request.param, db


def _get_pages(app):
    out, todo = [], list(app.routes)
    while todo:
        r = todo.pop()
        if hasattr(r, "original_router"):  # ingesloten router (nieuwere FastAPI)
            todo.extend(r.original_router.routes)
            continue
        if "GET" in getattr(r, "methods", ()) and "{" not in r.path and not r.path.startswith(("/static", "/logout")) \
                and not r.path.endswith((".pdf", ".csv", ".xlsx", ".html", ".png")):
            out.append(r.path)
    return sorted(set(out))


#: pagina's die zonder parameters al gegevens tonen (rekeningnamen per taal): alleen renderen en lang= controleren
DATA_PAGES = {"/beheer/ingebruikname/controleblad"}


def test_all_pages_in_language(foreign):
    """Renderproef (TL8): elke GET-pagina zonder parameters werkt in het Duits/Frans en toont geen
    Nederlandse schermtekst (catalogusteksten van 12+ tekens die in de taal anders luiden)."""
    client, lang, db = foreign
    catalog = i18n.CATALOGS[lang]
    dutch = [nl for nl in i18n.CATALOGS["en"] if len(nl) >= 12 and catalog.get(nl) != nl and "{" not in nl
             and not any(nl in other and nl != other for other in catalog.values())]
    pages = _get_pages(client.app)
    assert len(pages) > 40
    def visible(text):
        text = re.sub(r"<script.*?</script>|<option[^>]*>.*?</option>", "", text, flags=re.S)
        attrs = " ".join(re.findall(r'\b(?:title|placeholder|aria-label)="([^"]*)"', text))
        text = re.sub(r"<[^>]+>", "\n", text) + "\n" + attrs  # alleen zichtbare tekst, geen URL's
        return text.replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&")

    problems = []
    for path in pages:
        r = client.get(path)
        if r.status_code >= 500:
            problems.append(f"{path}: {r.status_code}")
            continue
        html = visible(r.text)
        # het huisstijlvoorbeeld is een document: dat heeft de documenttaal, niet de schermtaal
        if "<html" in r.text and f'<html lang="{lang}">' not in r.text and not path.startswith("/beheer/huisstijl/"):
            problems.append(f"{path}: verkeerde lang")
        # het controleblad (6d-2) toont rapporten met rekeningnamen: gegevens, per taal uit SL's vertaaltabel
        found = [nl for nl in dutch if nl in html] if path not in DATA_PAGES else []
        if found:  # ook op de Engelse pagina? dan zijn het gegevens (rekeningnamen e.d.), geen schermtekst
            db.execute("UPDATE app_user SET language = 'en' WHERE login = 'admin'")
            english = visible(client.get(path).text)
            db.execute("UPDATE app_user SET language = %s WHERE login = 'admin'", (lang,))
            found = [nl for nl in found if nl not in english]
        if found:
            problems.append(f"{path}: {found[:3]}")
    assert not problems, problems


def test_activation_sets_browser_language_and_formats(client, db, settings):
    """TL4/TL6: een nieuwe gebruiker krijgt bij activeren de browsertaal en de notatie die daarbij past;
    een bestaande gebruiker die een nieuwe code inwisselt houdt zijn instellingen."""
    from sqlledger_web import accounts
    from webhelpers import PASSWORD, post

    uid = accounts.create_user(db, "nieuw", "Nieuw")
    code = accounts.issue_setup_code(db, settings, uid, None)
    client.headers["Accept-Language"] = "de-DE,de;q=0.9"
    r = post(client, "/activeren", {"login": "nieuw", "code": code, "password": PASSWORD, "repeat": PASSWORD})
    assert r.url.path == "/login" and '<html lang="de">' in r.text
    u = accounts.get_user(db, user_id=uid)
    assert (u["language"], u["numberformat"], u["dateformat"]) == ("de", "1.000,00", "dd.mm.yy")

    db.execute("UPDATE app_user SET language = 'nl', last_login = now() WHERE id = %s", (uid,))
    code = accounts.issue_setup_code(db, settings, uid, None)
    client.headers["Accept-Language"] = "fr-FR"
    post(client, "/activeren", {"login": "nieuw", "code": code, "password": PASSWORD, "repeat": PASSWORD})
    u = accounts.get_user(db, user_id=uid)
    assert (u["language"], u["numberformat"], u["dateformat"]) == ("nl", "1.000,00", "dd.mm.yy")
