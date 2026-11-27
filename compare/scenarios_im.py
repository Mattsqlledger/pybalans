"""Scenario's import (fase 4 stap 7, blok "im"): im.pl als browser vs ``sqlledger.importer`` met hetzelfde bestand.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios im``. Ook in de standaardset.
Waar SL anders doet (fouten in FASE4_ANALYSE.md stap 7) staat dat bij ``expect_diff``.
"""

ADMIN = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}


#: 1810/1811 hebben in de testdata twee tarieven zonder einddatum; de oude krijgen er een (zoals echte data)
TAXFIX = ["UPDATE tax SET validto = '2025-12-31' WHERE rate IN (0.19, 0.06)"]
#: in echte data heeft elke boeking een koers; in de seed ontbreekt die (Perl rekent dan met 0, zie scenarios.PAY_SETUP)
RATEFIX = ["UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL",
           "UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL"]


def im(type_, csv, rows=None, extra=None):
    args = {"type": type_, "csv": csv}
    if rows:
        args["rows"] = rows
    if extra:
        args["extra"] = extra
    return {"op": "im_ui", "args": args}


SCENARIOS = [
    {
        "name": "import klanten (im.pl): nieuw met nummer, alle velden die het scherm kent",
        "config": ADMIN,
        "expect_diff": {"tabel customer": "nieuwe relatie zoals het scherm: Python zet de startdatum op vandaag; SL's "
                                          "import laat hem leeg (keuze binnen A)",
                        "tabel contact": "nieuwe relatie zoals het scherm: soort 'bedrijf' en de standaardwaarden van "
                                         "de contactpersoon; SL's import laat ze leeg"},
        "setup": ["SELECT setval('addressid', (SELECT max(id) FROM address))",
                  "SELECT setval('contactid', (SELECT max(id) FROM contact))"],
        "ops": [im("customer",
                   "customernumber;name;address1;zipcode;city;country;phone;email;terms;discount;creditlimit;"
                   "taxaccounts;curr;notes\n"
                   "K-100;Importklant BV;Dorpsstraat 1;1234 AB;Utrecht;Nederland;030-1234567;info@import.nl;14;2;"
                   "5000;1810;EUR;eerste import\n"
                   "K-101;Tweede Klant;Markt 2;;Gouda;;;;30;;;;EUR;\n")],
    },
    {
        "name": "import leveranciers (im.pl): bestaande bijwerken",
        "config": ADMIN,
        "expect_diff": {"tabel vendor": "nieuwe relatie zoals het scherm: Python zet de startdatum op vandaag; SL's "
                                          "import laat hem leeg (keuze binnen A)",
                        "tabel contact": "nieuwe relatie zoals het scherm: soort 'bedrijf' en de standaardwaarden van "
                                         "de contactpersoon; SL's import laat ze leeg"},
        "setup": ["SELECT setval('addressid', (SELECT max(id) FROM address))",
                  "SELECT setval('contactid', (SELECT max(id) FROM contact))"],
        "ops": [im("vendor", "vendornumber;name;city;terms;curr\nL-900;Nieuwe Leverancier;Delft;21;EUR\n"),
                im("vendor", "vendornumber;name;city;terms;curr\nL-900;Nieuwe Leverancier;Den Haag;30;EUR\n")],
    },
    {
        "name": "import artikelgroepen en rekeningschema (im.pl)",
        "config": ADMIN,
        "ops": [im("partsgroup", "partsgroup\nGebak\nBrood\n"),
                im("coa", "accno;description;charttype;category;link\n"
                          "9910;Importkosten;A;E;AP_amount\n9920;Importopbrengst;A;I;AR_amount\n")],
    },
    {
        "name": "import memoriaal (im.pl): twee boekingen in balans",
        "config": ADMIN,
        "expect_diff": {
            "tabel gl": "Python zet de medewerker van de gebruiker op de boeking zoals het memoriaalscherm; SL's import "
                        "laat hem leeg",
            "tabel audittrail": "zie tabel gl",
            "tabel exchangerate": "SL's import geeft defaultcurrency niet mee aan GL->post_transaction, dus geldt EUR als "
                                  "vreemde valuta en schrijft SL een koers 1 per datum; Python niet"},
        "ops": [im("gl", "reference;transdate;description;accno;debit;credit\n"
                         "IMP-1;2026-02-01;Herindeling;1200;100;\n"
                         "IMP-1;;;1100;;100\n"
                         "IMP-2;2026-02-02;Correctie;1100;25.50;\n"
                         "IMP-2;;;1200;;25.50\n")],
    },
    {
        "name": "import memoriaal (im.pl): boeking op een kop",
        "config": ADMIN,
        "expect_diff": {
            "tabel gl": "SL's import boekt ook op een kop (1000); Python weigert zoals het memoriaalscherm",
            "tabel acc_trans": "zie tabel gl", "tabel exchangerate": "zie tabel gl", "tabel audittrail": "zie tabel gl"},
        "ops": [im("gl", "reference;transdate;description;accno;debit;credit\n"
                         "IMP-3;2026-02-03;Op kop;1000;10;\nIMP-3;;;1100;;10\n")],
    },
    # ---- bouwstap 4 (BEHEER_EXTRA_ANALYSE.md §3, IM1–IM8)
    {
        "name": "import verkoopfacturen (im.pl): twee facturen, opeenvolgende rijen",
        "config": ADMIN,
        "setup": TAXFIX,
        "expect_diff": {"tabel ar": "Python zet de standaard betaalrekening op de factuur (ar.bank_id) zoals het "
                                    "factuurscherm; SL's import laat hem leeg"},
        "ops": [im("sales_invoice",
                   "invnumber;customernumber;transdate;partnumber;qty;sellprice\n"
                   "IMP-F1;K001;2026-02-01;KOEK-1;2;10\n"
                   "IMP-F1;K001;2026-02-01;BOEK-1;1;25\n"
                   "IMP-F2;K002;2026-02-02;KOEK-1;3;10\n")],
    },
    {
        "name": "import verkooporder en inkooporder (im.pl): termijn en leverdatum",
        "config": ADMIN,
        "setup": TAXFIX,
        "ops": [im("sales_order", "ordnumber;customernumber;transdate;partnumber;qty;sellprice\n"
                                  "IMP-O1;K001;2026-02-01;KOEK-1;4;10\n"
                                  "IMP-O3;K002;2026-02-01;BOEK-1;1;25\n"),
                im("purchase_order", "ordnumber;vendornumber;transdate;reqdate;partnumber;qty;sellprice\n"
                                     "IMP-O2;L001;2026-02-01;2026-02-10;BOEK-1;2;12.5\n")],
    },
    {
        "name": "import inkooporder met het nummer van een verkooporder (Perl-fout IM.pm:703)",
        "config": ADMIN,
        "setup": TAXFIX,
        "expect_diff": {
            "tabel oe": "SL zoekt een bestaande order alleen op nummer (zonder soort) en vervangt de verkooporder door "
                        "de inkooporder; Python laat de verkooporder staan en voegt de inkooporder toe (IM3 A)",
            "tabel orderitems": "zie tabel oe", "tabel audittrail": "zie tabel oe"},
        "ops": [im("sales_order", "ordnumber;customernumber;transdate;partnumber;qty;sellprice\n"
                                  "IMP-O1;K001;2026-02-01;KOEK-1;4;10\n"),
                im("purchase_order", "ordnumber;vendornumber;transdate;partnumber;qty;sellprice\n"
                                     "IMP-O1;L001;2026-02-01;BOEK-1;2;12.5\n")],
    },
    {
        "name": "import betalingen (im.pl): koppelen op betalingskenmerk",
        "config": ADMIN,
        "setup": RATEFIX,
        "expect_diff": {
            "tabel defaults": "SL's import geeft het soort betaling niet door aan CP->post_payment: het volgende "
                              "betaalkenmerk komt onder sleutel '1100' in plaats van 'receipt_1100' (zoals het "
                              "betaalscherm) en het logboek mist de soort en de medewerker",
            "tabel audittrail": "zie tabel defaults"},
        "ops": [im("payment", "datepaid;credit;dcn;source;memo\n2026-02-05;21;DCN1;B-1;import\n",
                   extra={"paymentaccount": "1100--Bank", "currency": "EUR", "defaultcurrency": "EUR"})],
    },
]
