"""Helpteksten per scherm (HELP_ANALYSE.md, golf 1): elk scherm toont het blok "Hoe werkt het?" in de taal van
de gebruiker, met knopnamen tussen de aanhalingstekens van die taal."""

import re

import pytest

from sqlledger_web import i18n
from sqlledger_web.helptext import hl, hm, ht
from sqlledger_web.i18n_de import TEXTS as DE
from sqlledger_web.i18n_en import EN
from sqlledger_web.i18n_fr import TEXTS as FR

from webhelpers import login, make_user

CATALOG = {"nl": {}, "en": EN, "de": DE, "fr": FR}

#: golf 1: scherm -> helpbestand
PAGES = {
    "/": "dashboard",
    "/verkoopfactuur/nieuw": "is", "/inkoopfactuur/nieuw": "is",
    "/verkoop/nieuw": "arap", "/inkoop/nieuw": "arap", "/verkoop/nieuw?creditnota=1": "arap",
    "/inkoop/uploaden": "apupload",
    "/ontvangst/nieuw": "cp_payment", "/betaling/nieuw": "cp_payment",
    "/bank/inlezen": "bank_import", "/bank/regels": "bank_lines", "/bank/vaste-regels": "bank_rules",
    "/afletteren": "rc",
    "/memoriaal/nieuw": "gl",
    "/klanten": "ct_list", "/leveranciers": "ct_list", "/klanten/nieuw": "ct_form", "/leveranciers/nieuw": "ct_form",
    "/artikelen/nieuw": "ic", "/artikelen/nieuw?soort=dienst": "ic",
    "/btw/aangifte": "vat_return", "/btw/icp": "vat_icp",
    "/aanmaningen": "dunning",
}

#: per helpbestand een zin die er altijd in staat (eerste zin onder "Zo doet u het" of "Waarvoor")
SENTENCE = {
    "is": "Klik op {s}, of op {sp} als u de factuur meteen wilt zien of afdrukken.",
    "arap": "Regels zonder bedrag worden bij opslaan weggelaten.",
    "apupload": "Verkeerd bestand gesleept? Klik op {f}.",
    "cp_payment": "U kunt niet meer op een factuur boeken dan er openstaat.",
    "bank_import": "Download bij uw bank het afschrift als CAMT.053 (XML) of MT940.",
    "bank_lines": "Er wordt nooit iets vanzelf geboekt. Elk voorstel bevestigt u zelf.",
    "bank_rules": "Een vaste regel boekt niets vanzelf. Hij geeft alleen een voorstel.",
    "rc": "Vink de regels aan die ook op het afschrift staan.",
    "gl": "Een regel heeft een bedrag in debet óf in credit, niet allebei, en nooit een negatief bedrag.",
    "ct_list": "Een klant of leverancier opzoeken, openen of de lijst exporteren.",
    "ct_form": "Anders dan in SQL-Ledger: een EU-btw-nummer wordt gecontroleerd. SQL-Ledger nam elk nummer aan.",
    "ic": "Anders dan in SQL-Ledger: de omschrijving is verplicht.",
    "vat_return": "Klik op een rubriek om de boekingen erachter te zien.",
    "vat_icp": "Klik op een btw-nummer om de boekingen van die afnemer te zien.",
    "dunning": "Vink per klant de facturen aan die op de brief moeten.",
    "dashboard": "Klik op een tegel of bedrag voor de details.",
}


#: golf 2 (overzichten en afsluiten): scherm -> helpbestand
PAGES2 = {
    "/rapporten/overzicht-verkoop": "rep_arap_list", "/rapporten/overzicht-inkoop": "rep_arap_list",
    "/rapporten/openstaand-verkoop": "rep_open", "/rapporten/openstaand-inkoop": "rep_open",
    "/rapporten/ouderdom-verkoop": "rep_aging", "/rapporten/ouderdom-inkoop": "rep_aging",
    "/rapporten/btw-verkoop": "rep_tax", "/rapporten/btw-inkoop": "rep_tax",
    "/rapporten/onbelast-verkoop": "rep_tax", "/rapporten/onbelast-inkoop": "rep_tax",
    "/rapporten/ontvangsten": "rep_payments", "/rapporten/betalingen": "rep_payments",
    "/rapporten/rekeningschema": "rep_chart", "/rapporten/journaal": "rep_journal",
    "/rapporten/rekeningmutaties": "rep_account", "/rapporten/proefbalans": "rep_trial",
    "/rapporten/winst-en-verlies": "rep_income", "/rapporten/balans": "rep_balance",
    "/beheer/afsluiten": "am_closing", "/beheer/jaarafsluiting": "gl_yearend",
    "/ontvangsten": "cp_payments", "/betalingen": "cp_payments",
    "/ontvangsten/corrigeren": "cp_register", "/betalingen/corrigeren": "cp_register",
    "/memoriaal/nieuw?fx=1": "gl_fx", "/betalingsregister": "cp_payregister",
    "/afletteren/rapport": "rc_report",
    "/documenten/batch?soort=invoice": "batch", "/documenten/batch?soort=vendor_invoice": "batch",
    "/rekeningoverzichten": "dunning_statements",
}

SENTENCE2 = {
    "rep_arap_list": "Een lijst van uw verkoop- of inkoopboekingen in een periode, met netto, btw, totaal, betaald en "
                     "openstaand. Hier zoekt u een factuur terug.",
    "rep_open": "De facturen die nog niet (helemaal) betaald zijn: wat klanten u nog moeten betalen, of wat u "
                "leveranciers nog moet betalen.",
    "rep_aging": "De openstaande facturen per klant of leverancier, verdeeld naar hoe oud ze zijn. Zo ziet u snel wie "
                 "al lang niet betaald heeft.",
    "rep_tax": "Een lijst van de verkoop- of inkoopfacturen met btw per btw-rekening, of van de facturen zonder btw. "
               "Handig om te controleren of de btw op elke factuur klopt, of om een bedrag in de btw-aangifte terug te "
               "zoeken. De aangifte zelf doet u bij {a}.",
    "rep_payments": "Alle ontvangsten (geld erbij) of betalingen (geld eraf) op uw bank- en kasrekeningen in een "
                    "periode, per rekening.",
    "rep_chart": "Al uw grootboekrekeningen met hun saldo. Het overzicht verschijnt meteen; u hoeft niets te kiezen.",
    "rep_journal": "Alle boekingen terugzoeken: memoriaal, verkoop, inkoop en betalingen. U ziet per boeking de "
                   "journaalregels met debet en credit.",
    "rep_account": "Alle boekingen op één grootboekrekening in een periode, met beginsaldo en het saldo na elke regel.",
    "rep_trial": "Klik op een rekening om de mutaties te zien.",
    "rep_income": "Opbrengsten, kosten en het resultaat over een periode, als tabel, als grafiek of allebei.",
    "rep_balance": "Bezittingen, schulden en eigen vermogen op één datum.",
    "am_closing": "Een periode op slot zetten, bijvoorbeeld na de btw-aangifte of de jaarrekening, zodat daarin niets "
                  "meer verandert. Alleen een beheerder kan dit.",
    "gl_yearend": "Aan het eind van het boekjaar boekt u alle opbrengst- en kostenrekeningen naar nul. Het resultaat "
                  "(winst of verlies) komt op een rekening van het eigen vermogen. Alleen een beheerder kan dit.",
    "cp_payments": "Ontvangsten of betalingen van veel klanten of leveranciers in één keer boeken, bijvoorbeeld een "
                   "reeks betalingen die u via de bank klaarzet. U ziet één regel per relatie met het totaal dat "
                   "openstaat.",
    "cp_register": "Een geboekte ontvangst of betaling terugdraaien (storneren) of een ander kenmerk of een andere "
                   "datum geven.",
    "gl_fx": "De waarde in euro van een rekening in vreemde valuta aanpassen, bijvoorbeeld een dollarrekening aan het "
             "eind van het jaar omrekenen tegen de koers van dat moment. Het bedrag in de vreemde valuta blijft "
             "gelijk; alleen de waarde in uw eigen valuta verandert.",
    "cp_payregister": "Een lijst van geboekte ontvangsten en betalingen, om er achteraf een betalingsspecificatie van "
                      "te printen of te mailen. Op de specificatie staat welke facturen met de betaling zijn voldaan.",
    "rc_report": "Laat zien welke regels van een rekening in een periode zijn afgeletterd, bijvoorbeeld om af te "
                 "drukken bij het bankafschrift.",
    "batch": "Veel documenten van één soort in één keer printen of mailen, bijvoorbeeld alle facturen van deze maand "
             "die nog niet verstuurd zijn. De soort staat al goed voor het menu waaruit u komt.",
    "dunning_statements": "Een overzicht voor klanten van alles wat op een datum openstaat, ook wat nog niet vervallen "
                          "is. Elke klant krijgt per valuta één overzicht.",
}

#: golf 3 (beheer en systeem): scherm -> helpbestand. Schermen die een toestand nodig hebben (code, mail, import-
#: voorbeeld, back-up controleren/resultaat) staan in test_wave3_state_screens.
PAGES3 = {
    "/beheer/instellingen": "am_settings", "/beheer/ingebruikname": "am_switch",
    "/beheer/ingebruikname/controleblad": "am_switch_sheet",
    "/beheer/rekeningschema": "am_accounts", "/beheer/rekeningschema/nieuw": "am_account",
    "/beheer/gifi": "am_gifi", "/beheer/gifi/nieuw": "am_gifi",
    "/beheer/rgs": "am_rgs", "/beheer/btw": "am_taxes", "/btw/rubrieken": "vat_boxes",
    "/beheer/valuta": "am_currencies", "/beheer/koersen": "am_rates",
    "/beheer/talen": "am_languages", "/beheer/branches": "am_business", "/beheer/betaalmethoden": "am_paymentmethods",
    "/beheer/importeren": "am_import", "/beheer/exporteren": "am_export", "/beheer/huisstijl": "am_style",
    "/beheer/email": "mail_settings", "/beheer/documentinstellingen": "am_docsettings",
    "/beheer/sjablonen": "am_doctemplates", "/beheer/audittrail": "am_audit", "/beheer/locks": "am_locks",
    "/systeem/gebruikers": "sys_users", "/systeem/gebruikers/nieuw": "sys_user_new",
    "/systeem/administraties": "sys_companies", "/systeem/administraties/aanmaken": "sys_company_create",
    "/systeem/administraties/nieuw": "sys_company_edit", "/systeem/back-up-maken": "sys_backup",
    "/systeem/back-up": "sys_restore", "/systeem/rgs": "sys_rgs", "/systeem/logboek": "sys_log",
    "/instellingen": "settings", "/administratie": "choose_company",
    "/betalingen/betaalbestand": "cp_sepa",
}

SENTENCE3 = {
    "cp_sepa": "Een SEPA-betaalbestand maken van inkoopfacturen die u wilt betalen. U leest het bestand in bij uw bank "
               "(internetbankieren); de bank voert de overboekingen uit. In PyBalans wordt niets geboekt: de "
               "betalingen worden afgeletterd als u daarna het bankafschrift inleest.",
    "am_gifi": 'Een eigen, grovere indeling van uw rekeningen. Meerdere rekeningen krijgen dezelfde GIFI-code; de rapporten tellen ze dan samen onder die code.',
    "am_account": 'Eén rekening of kopregel van het rekeningschema maken of wijzigen.',
    "am_accounts": 'Alle rekeningen en kopregels van uw grootboek, met hun soort en koppelingen. Hier voegt u een rekening toe of past u er een aan.',
    "am_audit": 'Wie wanneer welke boeking of stamgegevens heeft opgeslagen, gemaild of verwijderd. Handig bij controle en om terug te zoeken wie iets veranderd heeft.',
    "am_business": 'Een indeling van klanten en leveranciers naar soort bedrijf, bijvoorbeeld Groothandel of Horeca.',
    "am_currencies": "De valuta's waarin u factureert of boekt. De bovenste is uw standaardvaluta: alle bedragen in de boekhouding staan daarin, en koersen zijn ten opzichte daarvan.",
    "am_docsettings": 'Wat er op uw documenten staat: btw-nummer, bankrekening, eigen teksten en welke kolommen de regels hebben. Zonder HTML-kennis. Leeg of standaard betekent: zoals de standaardsjablonen.',
    "am_doctemplates": 'Voor wie de opmaak van een document verder wil aanpassen dan {h} en {d} toelaten. U past de HTML van het sjabloon aan op uw eigen computer en uploadt het hier.',
    "am_export": 'Uw klanten, leveranciers, artikelen, diensten, artikelgroepen, het rekeningschema of de GIFI-indeling als Excel- of CSV-bestand, in precies het formaat van {m}.',
    "am_import": 'Klanten, leveranciers, artikelen, diensten, artikelgroepen, rekeningen, GIFI-codes, memoriaalboekingen, verkoopfacturen, verkooporders, inkooporders of betalingen in één keer inlezen uit een CSV- of Excel-bestand. U ziet eerst precies wat er gebeurt; pas na {b} wordt iets opgeslagen.',
    "am_languages": 'De talen waarin u documenten naar klanten en leveranciers stuurt. Een taal kiest u daarna bij de relatie en op de boeking.',
    "am_locks": "Een boeking die iemand open heeft, is voor anderen alleen-lezen. Ze zien dan een melding dat de boeking in gebruik is, met de naam van wie hem open heeft. Blijft zo'n lock hangen, bijvoorbeeld omdat iemand het scherm sloot zonder op te slaan, dan geeft u het hier vrij.",
    "am_paymentmethods": 'Hoe er betaald wordt, bijvoorbeeld Overschrijving, Pin of Contant. U kunt het vastleggen bij een relatie, op een boeking en bij elke betaling.',
    "am_rate_edit": 'De koersen van alle vreemde valuta op één datum invoeren of wijzigen.',
    "am_rates": 'De wisselkoersen per datum. Boekt u in een vreemde valuta, dan vult het programma de koers van de boekdatum uit deze lijst in.',
    "am_rgs": 'Elke rekening een code geven uit het Referentie Grootboekschema (RGS). Dan sluit uw rekeningschema aan op de standaardindeling die banken, accountants en de Belastingdienst gebruiken.',
    "am_settings": 'De vaste gegevens van deze administratie: uw bedrijfsgegevens, de documenttaal, hoe het programma rekent en nummert, en een paar keuzes voor de btw. Alleen de beheerder ziet dit scherm.',
    "am_style": 'Logo, kleur, lettertype en indeling van alle documenten van deze administratie: facturen, orders, offertes, pakbonnen, aanmaningen en betalingsspecificaties. Rechts ziet u meteen een voorbeeld.',
    "am_switch": 'Een lijst met alles wat moet kloppen voordat u na de overstap uit SQL-Ledger alleen nog met dit programma werkt. Dezelfde lijst staat bovenaan het dashboard, alleen voor de beheerder.',
    "am_switch_sheet": 'Eén blad met de cijfers van de ingelezen administratie, om naast de laatste cijfers uit SQL-Ledger te leggen. U opent het met {b} op {m}.',
    "am_taxes": 'Het percentage van elke btw-rekening, met de periode waarin het geldt. Facturen en boekingen rekenen de btw met het tarief dat op de factuurdatum geldt.',
    "choose_company": 'De administratie kiezen waarin u werkt. U ziet alleen de administraties waar u toegang toe hebt, met uw rol.',
    "mail_send": 'Een document als PDF mailen naar de klant of leverancier.',
    "mail_settings": 'Hoe facturen en andere documenten van deze administratie gemaild worden: via welke mailserver, met welk afzenderadres, en met welke standaardtekst. De PDF gaat altijd als bijlage mee.',
    "settings": 'Uw eigen gegevens, notatie, taal, wachtwoord en tweestapsverificatie. Geldt voor u in alle administraties.',
    "sys_backup": 'Per administratie een volledige back-up downloaden: alle gegevens en de bijlagen. Een back-up is ook de manier om een administratie naar een andere server te verhuizen.',
    "sys_code": 'De code waarmee de gebruiker zelf een (nieuw) wachtwoord kiest.',
    "sys_companies": 'Alle administraties van deze installatie. Hier begint u een nieuwe administratie, leest u een back-up in of koppelt u een bestaande database. Alleen de systeembeheerder ziet dit menu.',
    "sys_company_create": 'Een nieuwe, lege administratie in een eigen database, met een rekeningschema, btw-tarieven en (in Nederland) de rubrieken van de btw-aangifte en RGS-codes.',
    "sys_company_edit": 'Een SQL-Ledger-database die al op de databaseserver staat als administratie gebruiken, of van een bestaande administratie de naam, code, modules en status wijzigen.',
    "sys_log": 'Wat er in de app is gebeurd: inloggen (ook mislukt), wijzigingen aan gebruikers en administraties, back-ups, exports, en meer. Voor alle administraties samen, het nieuwste bovenaan.',
    "sys_restore": 'Een back-up inlezen als nieuwe administratie: het bestand dat SQL-Ledger maakt (.sql of .sql.gz), een pg_dump in platte SQL, of een back-up van deze app. Een oudere SQL-Ledger-versie wordt meteen bijgewerkt.',
    "sys_restore_check": 'Kijken of het de goede back-up is en de nieuwe administratie een naam geven, voordat er iets wordt ingelezen.',
    "sys_restore_result": 'De stand van het inlezen en daarna wat er is ingelezen.',
    "sys_rgs": 'De officiële RGS-lijst inlezen. Die is nodig voor {m}, waar u per rekening een RGS-code kiest. Eén lijst geldt voor alle administraties van deze installatie.',
    "sys_user_edit": 'Gegevens, rechten per administratie en beveiliging van één gebruiker.',
    "sys_user_new": 'Een account aanmaken. De gebruiker kiest zelf een wachtwoord met een eenmalige code die u na het aanmaken krijgt.',
    "sys_users": 'Alle gebruikers van deze installatie, met hun administraties en rol. Hier maakt u een nieuwe gebruiker of opent u een gebruiker om toegang, rol en beveiliging te regelen. Alleen de systeembeheerder ziet het menu {m}.',
    "totp_setup": 'Een tweede stap bij het inloggen: naast uw wachtwoord een code van 6 cijfers uit een app op uw telefoon.',
    "vat_boxes": 'Per btw-rekening de rubriek van de Nederlandse btw-aangifte kiezen, en de rekeningen voor de btw-boeking. Daarna maakt het programma de aangifte voor u bij {m}.',
}

#: vaste regel onder "Let op" bij de rapporten (golf 2, V1)
REPORT_LINE = ("Kies de opties en klik op {t}. Meer keuzes staan onder {m}. Het scherm onthoudt uw laatste keuzes. "
               "Met CSV, Excel, PDF of {a} neemt u het resultaat mee.")
REPORT_LINE_NOFORM = "Met CSV, Excel, PDF of {a} neemt u het resultaat mee."


#: golf 4: rest (orders, artikelen, terugkerend, projecten/uren, bijlagen)
PAGES4 = {
    "/verkooporder/nieuw": "oe_form", "/inkooporder/nieuw": "oe_form", "/offerte/nieuw": "oe_form",
    "/offerteaanvraag/nieuw": "oe_form",
    "/verkooporders": "oe_list", "/inkooporders": "oe_list", "/offertes": "oe_list", "/offerteaanvragen": "oe_list",
    "/artikelen": "ic_list", "/artikelen-groepen": "ic_groups", "/artikelen/nieuw?soort=samengesteld": "ic_assembly",
    "/artikelen-assembleren": "ic_asmlist", "/artikelen-stuklijsten": "ic_asmlist", "/artikelen-productie": "ic_asmlist",
    "/bijlagen": "attach_overview", "/terugkerend": "recurring_list",
    "/beheer/afdelingen": "am_departments", "/beheer/afdelingen/nieuw": "am_departments",
    "/beheer/projecten": "am_projects", "/beheer/projecten/nieuw": "am_projects",
    "/uren/maandstaat": "jc_sheet", "/uren/toewijzingen": "jc_assignments", "/uren/tijdkaarten": "jc_list",
    "/uren/tijdkaart/nieuw": "jc_form", "/uren/verkooporders": "jc_orders", "/uren/tarieven": "jc_rates",
    "/rapporten/uren-per-project": "rep_hours",
}

SENTENCE4 = {
    "oe_form": "Een order of offerte met artikelen uit uw artikellijst. Een verkooporder en een offerte maakt u voor "
               "een klant, een inkooporder en een offerteaanvraag voor een leverancier. Het programma rekent de btw en "
               "het totaal uit. Later zet u hem met één klik om naar een order of factuur.",
    "oe_list": "Uw orders of offertes van één soort zoeken en openen.",
    "ic_list": "Uw artikelen, diensten en samengestelde artikelen zoeken en openen. U kunt ook zoeken in de factuur-, "
               "order- en offerteregels waarin ze voorkomen.",
    "ic_groups": "Groepen om uw artikelen in te delen, bijvoorbeeld Hout of Gereedschap. U kiest de groep daarna bij "
                 "het artikel en kunt erop zoeken in {m}.",
    "ic_history": "Tegen welke prijs u dit artikel verkocht of ingekocht hebt: alle facturen, orders en offertes met "
                  "dit artikel, de nieuwste bovenaan.",
    "ic_stock": "De voorraad van een artikel aanpassen, bijvoorbeeld na een telling of bij breuk. Elke correctie wordt "
                "met reden bewaard.",
    "ic_build": "Van onderdelen een samengesteld artikel maken (assembleren) of het weer uit elkaar halen "
                "(demonteren). De voorraad van het product gaat omhoog en die van de onderdelen omlaag, of andersom.",
    "ic_asmlist": "Overzichten van uw samengestelde artikelen:",
    "ic_assembly": "Een product dat u maakt uit andere artikelen en diensten, met een stuklijst. Bij verkoop gaan de "
                   "onderdelen uit de voorraad; de kostprijs komt uit de onderdelen.",
    "attach_overview": "Alle bijlagen van deze administratie op één plek: de PDF's en foto's bij boekingen, facturen, "
                       "orders, offertes, klanten, leveranciers en artikelen. U zoekt een bestand terug en opent het, "
                       "of gaat naar de boeking waar het bij hoort.",
    "recurring_list": "Alle herhaalschema's op één lijst. Hier maakt u de boekingen, facturen en orders aan die weer "
                      "aan de beurt zijn.",
    "recurring_schedule": "Een boeking, factuur of order laten terugkomen, bijvoorbeeld de huur elke maand of een "
                          "abonnement elk kwartaal. U legt vast hoe vaak en hoe lang.",
    "am_departments": "Afdelingen om boekingen naar onderdeel van het bedrijf te kunnen uitsplitsen in de rapporten.",
    "am_projects": "Projecten om omzet, kosten en uren per opdracht te volgen. U kiest een project per regel op een "
                   "boeking, factuur of order, en op elke tijdkaart.",
    "jc_sheet": "Uren schrijven per maand: een rij per project en dienst, een kolom per dag. Bij {u} zijn het uw eigen "
                "uren; bij {m} kiest de boekhouder een medewerker.",
    "jc_assignments": "Welke medewerker op welk project met welke dienst uren schrijft. Alleen die combinaties staan "
                      "in zijn maandstaat.",
    "jc_list": "Alle geschreven uren, één regel per tijdkaart, met het totaal aan uren en bedrag.",
    "jc_form": "Eén blok uren van één medewerker op één project met één dienst.",
    "jc_orders": "Uren van klantprojecten factureren. Het programma zet de gekozen uren op een verkooporder; daarna "
                 "maakt u de factuur vanuit de order.",
    "jc_rates": "Wat een uur van een medewerker het bedrijf kost. Daarmee rekent het rapport {m} de kostwaarde en de "
                "marge uit. Er wordt niets mee geboekt.",
    "rep_hours": "Per project de geschreven uren, wat ze kosten en wat ze opbrengen.",
}


def _t(lang, text):
    return CATALOG[lang].get(text, text)


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).replace("&#39;", "'").replace("&amp;", "&")


@pytest.fixture(params=["nl", "en", "de", "fr"])
def admin(request, client, db, company):
    secret = make_user(db, "admin", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "jan")])
    db.execute("UPDATE app_user SET language = %s WHERE login = 'admin'", (request.param,))
    login(client, "admin", secret)
    page = client.get("/administratie").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/administratie", data={"csrf": csrf, "company_id": company["id"]})
    return client, request.param


def _check_pages(client, lang, pages, sentences):
    for url, key in pages.items():
        r = client.get(url)
        assert r.status_code == 200, url
        html = r.text
        assert html.count('<details class="help">') == 1, url
        block = html[html.index('<details class="help">'):]
        block = block[:block.index("</details>")]
        assert _t(lang, "Hoe werkt het?") in block, url
        assert _t(lang, "Zo doet u het") in block, url
        plain = _plain(block)
        expected = _t(lang, sentences[key])
        if "{" in expected:  # alleen het stuk vóór de eerste plaatshouder
            expected = expected.split("{")[0]
        assert expected.strip() in plain, (url, lang, expected)


def test_every_wave1_screen_has_help(admin):
    client, lang = admin
    _check_pages(client, lang, PAGES, SENTENCE)


def test_every_wave2_screen_has_help(admin):
    client, lang = admin
    _check_pages(client, lang, PAGES2, SENTENCE2)


def test_report_line(admin):
    """Vaste regel over de rapportknoppen (V1); Rekeningschema heeft geen formulier, dus alleen de knoppen."""
    client, lang = admin
    line = _t(lang, REPORT_LINE).split("{")[0].strip()
    noform = _t(lang, REPORT_LINE_NOFORM).split("{")[0].strip()
    assert line in _plain(client.get("/rapporten/proefbalans").text)
    chart = _plain(client.get("/rapporten/rekeningschema").text)
    assert noform in chart and line not in chart


def test_fx_screen_has_own_help(admin):
    """De koersverschilboeking toont alleen haar eigen help, niet die van het gewone memoriaal."""
    client, lang = admin
    html = client.get("/memoriaal/nieuw?fx=1").text
    assert _t(lang, SENTENCE["gl"]).split("{")[0].strip() not in _plain(html)


def test_labels_quoted_in_user_language(admin):
    client, lang = admin
    html = client.get("/memoriaal/nieuw").text
    block = html[html.index('<details class="help">'):]
    left, right = {"nl": ("“", "”"), "en": ("“", "”"), "de": ("„", "“"), "fr": ("« ", " »")}[lang]
    assert f"{left}{_t(lang, 'Opslaan')}{right}" in block
    menu = client.get("/verkoopfactuur/nieuw").text
    assert f"<em>{_t(lang, 'Verkoop')} &gt; {_t(lang, 'Boeking toevoegen')}</em>" in menu


def test_helpers_escape():
    token = i18n.current_language.set("nl")
    try:
        assert str(ht("Klik op {b}.", b="<b>x</b>")) == "Klik op &lt;b&gt;x&lt;/b&gt;."
        assert str(ht("Klik op {b}.", b=hl("Opslaan"))) == "Klik op “Opslaan”."
        assert str(hm("Verkoop", "Verkoopfactuur")) == "<em>Verkoop &gt; Verkoopfactuur</em>"
        assert str(ht("A & B")) == "A &amp; B"
    finally:
        i18n.current_language.reset(token)


def test_readonly_sees_help(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    html = client.get("/klanten").text
    assert '<details class="help">' in html


def test_favicon(client, db, company):
    """Pictogram (optie B): in elke pagina gelinkt, en /favicon.ico werkt zonder in te loggen."""
    make_user(db, "lees2", memberships=[(company["id"], "readonly")])
    login(client, "lees2")
    html = client.get("/klanten").text
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in html
    for path in ("/static/favicon.svg", "/static/favicon-32.png", "/static/apple-touch-icon.png"):
        assert client.get(path).status_code == 200, path
    client.cookies.clear()
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"] == "image/x-icon" and r.content[:4] == b"\0\0\1\0"


def test_every_wave3_screen_has_help(admin):
    client, lang = admin
    _check_pages(client, lang, PAGES3, SENTENCE3)


def test_wave3_state_screens(admin, db):
    """Golf 3: schermen zonder vaste URL (gebruiker bewerken, eenmalige code, 2FA instellen) en de oude
    Back-up-helpblokken die vervangen zijn (V5)."""
    client, lang = admin
    uid = db.execute("SELECT id FROM app_user WHERE login = 'admin'").fetchone()["id"]
    _check_pages(client, lang, {f"/systeem/gebruikers/{uid}": "sys_user_edit"}, SENTENCE3)
    page = client.get("/systeem/back-up-maken").text
    block = page[page.index('<details class="help">'):]
    assert "<pre class=\"cmd\">" in block[:block.index("</details>")]
    assert _t(lang, "Automatisch elke nacht (Mac)") in page


def test_every_wave4_screen_has_help(admin):
    client, lang = admin
    _check_pages(client, lang, PAGES4, SENTENCE4)


def test_wave4_state_screens(admin, settings, company_db):
    """Golf 4: schermen vanuit een artikel, een samengesteld artikel en een boeking (Herhalen)."""
    import psycopg
    from psycopg.rows import dict_row
    from test_web_asm import add_assembly
    from test_web_ic import add_part
    client, lang = admin
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as cdb:
        cdb.execute("UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'")
        pid = add_part(client)
        aid = add_assembly(client, [("T-1", "2")])
        tid = cdb.execute("SELECT id FROM ar ORDER BY id LIMIT 1").fetchone()["id"]
    _check_pages(client, lang, {
        f"/artikelen/{pid}/historie": "ic_history", f"/artikelen/{pid}/voorraad": "ic_stock",
        f"/artikelen/{pid}/waar-gebruikt": "ic_asmlist", f"/artikelen/{aid}": "ic_assembly",
        f"/artikelen/{aid}/assembleren": "ic_build", f"/artikelen/{aid}/stuklijst": "ic_asmlist",
        f"/herhalen/{tid}": "recurring_schedule"}, SENTENCE4)
    # een gewoon artikel houdt de help van golf 1
    _check_pages(client, lang, {f"/artikelen/{pid}": "ic"}, SENTENCE)
