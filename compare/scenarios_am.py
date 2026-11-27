"""Scenario's beheer (fase 2, blok "am"): am.pl als browser vs de formuliercode van de webapp.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios am --dbprefix am``.
Ze zitten ook in de standaardset (scenarios.py voegt ze toe).
"""

ADMIN = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}

#: testdata: btw-rekeningen met twee regels zonder einddatum (Perl weigert dan elke save van het btw-scherm)
TAX_SETUP = [
    "UPDATE tax SET validto = '2012-09-30' WHERE chart_id IN (10076, 10078) AND rate = 0.19",
    "UPDATE tax SET validto = '2018-12-31' WHERE chart_id = 10079 AND rate = 0.06",
]


def am(do, **args):
    return {"op": "am_ui", "args": {"do": do, **args}}


SCENARIOS = [
    {
        "name": "beheer: rekeningschema (am.pl) als browser",
        "config": ADMIN,
        "ops": [
            am("account", accno="4997", description="Diverse  kosten", category="E", link=["AP_amount", "AR_amount"]),
            am("account", id=10044),                                  # betaalrekening ongewijzigd
            am("account", id=10057),                                  # debiteuren ongewijzigd
            am("account", id=10001),                                  # kop ongewijzigd
            am("account", id=10195, description="Koersverschillen", closed=True),
            am("account", accno="4995", description="Kop", category="E", charttype="H"),
            am("account", accno="4994", category="A", link=["AR", "AP"]),            # fout: twee hoofdrekeningen
            am("account", accno="4994", category="A", link=["AR_paid", "AR_tax"]),   # fout: twee AR-keuzes
            am("account", accno="", category="A"),                                    # fout: geen nummer
            am("account", id=10044, accno="1101", mode="save_as_new"),
            am("account", accno="1812", description="Btw nul", category="L", link=["AR_tax", "AP_tax"]),
            am("account", id="accno:4995", mode="delete"),
            am("account", id=10195, mode="delete"),                   # standaardrekening: beide weigeren
        ],
    },
    {
        "name": "beheer: rekening met boekingen/in gebruik verwijderen (Perl controleert niet)",
        "config": ADMIN,
        "setup": ["UPDATE customer SET payment_accno_id = 10046 WHERE id = 900"],
        "ops": [
            am("account", id=10057, mode="delete"),                   # debiteuren, met boekingen
            am("account", id=10046, mode="delete"),                   # betaalrekening van een klant
        ],
        "expect_diff": {
            "op 1": "Perl verwijdert een rekening met boekingen (am.pl toont alleen geen knop; AM::delete_account "
                    "controleert niets). Python weigert (README 71).",
            "op 2": "Perl verwijdert een rekening waar een klant naar verwijst; Python weigert (README 71).",
            "tabel chart": "Zie op 1 en 2: bij Perl zijn de rekeningen weg.",
            "tabel tax": "Zie op 1 en 2.",
            "tabel audittrail": "Perl schrijft 'deleted' voor de verwijderde rekeningen.",
        },
    },
    {
        "name": "beheer: btw-tarieven (am.pl) als browser",
        "config": ADMIN,
        "setup": TAX_SETUP,
        "ops": [
            am("taxes"),
            am("taxes", set=[{"accno": "1801", "n": 1, "validto": "2026-06-30"}],
               add=[{"accno": "1801", "n": 2, "rate": "7.5", "taxnumber": "NL-L2"}]),
            am("taxes", closed={"1811": True}),
            am("taxes", set=[{"accno": "1811", "n": 2, "rate": "10"}]),
        ],
        "expect_diff": {
            "op 4": "BT1 (README 203): het tarief van 9% is al gebruikt (factuur van 10-06-2026); Python weigert het "
                    "te overschrijven, Perl (AM::save_taxes) overschrijft zonder controle.",
            "tabel tax": "Zie op 4: bij Python blijft 9% staan, bij Perl wordt het 10%.",
            "tabel audittrail": "Zie op 4: Perl heeft dezelfde audittrailregel één keer meer (de opslag die Python weigert).",
        },
    },
    {
        "name": "beheer: btw-koppeling weg (Perl wist de tariefhistorie)",
        "config": ADMIN,
        "setup": TAX_SETUP,
        "ops": [am("account", id=10079, link=[])],
        "expect_diff": {"tabel tax": "AM::save_account doet DELETE FROM tax als de btw-vlag weggaat; Python "
                                     "bewaart de tarieven (README 70).",
                        "tabel partstax": "IC_taxservice uit: Perl haalt de btw-rekening bij alle diensten weg "
                                          "(en zet hem bij aan bij álle diensten); Python laat partstax ongemoeid "
                                          "tot de artikelen in fase 3 (README 80)."},
    },
    {
        "name": "beheer: instellingen (am.pl defaults) als browser",
        "config": ADMIN,
        # Perl schrijft revtrans '0' niet terug (onwaar) en wist sleutels buiten het scherm: zie volgende scenario
        "setup": ["DELETE FROM defaults WHERE fldname IN ('fxgain_accno_id', 'fxloss_accno_id')",
                  "DELETE FROM defaults WHERE fldname = 'revtrans' AND fldvalue = '0'",
                  "INSERT INTO defaults VALUES ('cashovershort_accno_id', '10122')"],
        "ops": [
            am("defaults"),
            am("defaults", set={"company": "Nieuw BV", "address": "Straat 1\nPlaats", "cdt": "1",
                                "lock_glnumber": "1", "glnumber": "GL-0200", "typeofcontact": "person",
                                "method": "cash", "fxgainloss": "9210", "precision": "2"}),
            am("defaults", set={"cdt": "", "method": "", "company": "Weer anders"}),
        ],
    },
    {
        "name": "beheer: instellingen wissen sleutels buiten het scherm (Perl-fout)",
        "config": ADMIN,
        "setup": ["INSERT INTO defaults VALUES ('yearend', '6')"],
        "ops": [am("defaults")],
        "expect_diff": {"tabel defaults": "AM::save_defaults gooit de hele tabel weg en schrijft alleen de "
                                          "schermvelden terug: yearend, fxgain_accno_id en fxloss_accno_id "
                                          "verdwijnen en cashovershort_accno_id wordt de eerste rekening van "
                                          "de keuzelijst (geen lege keuze); revtrans '0' wordt niet "
                                          "teruggeschreven. Python schrijft alleen gewijzigde schermvelden "
                                          "(README 73)."},
    },
    {
        "name": "beheer: boekperiode afsluiten (am.pl audit_control)",
        "config": ADMIN,
        "ops": [
            am("closing", closedto="2026-03-31", revtrans="1", audittrail="0"),
            am("closing"),
            am("closing", closedto="", revtrans="0", audittrail="1"),
        ],
    },
    {
        "name": "beheer: valuta (am.pl) als browser",
        "config": ADMIN,
        "ops": [
            am("currency", mode="add", curr="GBP", prec="2"),
            am("currency", mode="add", curr="JPY", prec="0"),
            am("currency", mode="edit", curr="JPY", prec="1"),
            am("currency", mode="move", curr="JPY", move="up"),
            am("currency", mode="move", curr="JPY", move="down"),
            am("currency", mode="delete", curr="JPY"),
        ],
    },
    {
        "name": "beheer: valuta-volgorde en verwijderen (Perl wijzigt standaardvaluta)",
        "config": ADMIN,
        "ops": [
            am("currency", mode="move", curr="USD", move="up"),
            am("currency", mode="delete", curr="EUR"),
        ],
        "expect_diff": {
            "op 1": "AM::move zet USD bovenaan: ongemerkt de nieuwe standaardvaluta. Python weigert (README 74).",
            "op 2": "Perl verwijdert de standaardvaluta die in boekingen gebruikt wordt (alleen de knop ontbreekt); "
                    "Python weigert (README 76).",
            "tabel curr": "Zie op 1 en 2.",
            "tabel audittrail": "Perl schrijft 'deleted' voor de verwijderde standaardvaluta.",
            "tabel customer": "Gevolg van op 1: relaties zonder eigen valuta (curr leeg) volgen de standaardvaluta "
                              "en zijn in Perl daardoor ongemerkt USD geworden (README 74).",
            "tabel vendor": "Idem bij leveranciers.",
        },
    },
    {
        "name": "beheer: koersen (am.pl) als browser",
        "config": ADMIN,
        "ops": [
            am("exchangerate", transdate="2026-07-01", rates={"USD": "1.3"}),
            am("exchangerate", date="2026-06-30"),
            am("exchangerate", date="2026-06-30", rates={"USD": "1.26"}),
            am("exchangerate", date="2026-07-01", rates={"USD": ""}),
        ],
    },
    {
        "name": "beheer: koers naar andere datum (Perl laat de oude staan)",
        "config": ADMIN,
        "ops": [am("exchangerate", date="2026-05-20", transdate="2026-05-21")],
        "expect_diff": {"tabel exchangerate": "am.pl save_exchangerate gebruikt oldtransdate niet: de koers op de "
                                              "oude datum blijft staan. Python verplaatst hem (README 77)."},
    },
    {
        "name": "beheer: afdelingen, branches, betaalmethoden (am.pl) als browser",
        "config": ADMIN,
        "ops": [
            am("department", set={"description": "Service", "role": "C"}),
            am("department", id=600),
            am("department", id=601, set={"description": "Inkoop en logistiek"}),
            am("department", id=601, mode="move", move="up"),
            am("department", id="desc:Service", mode="delete"),
            am("business", set={"description": "Horeca", "discount": "2.5"}),
            am("business", id="desc:Horeca"),
            am("paymentmethod", set={"description": "Contant", "fee": "0.10", "roundchange": "0.05"}),
            am("paymentmethod", set={"description": "Pin", "fee": "", "roundchange": "0"}),
            am("paymentmethod", id="desc:Pin", mode="move", move="up"),
            am("paymentmethod", id="desc:Contant"),
            am("paymentmethod", id="desc:Contant", mode="delete"),
            am("business", id="desc:Horeca", mode="delete"),
        ],
        "expect_diff": {"tabel audittrail": "SL schrijft geen audittrail voor een nieuwe afdeling, branche of "
                                            "betaalmethode (Form::audittrail slaat over zonder id); Python wel "
                                            "(README 243). Zichtbaar bij Service (daarna verwijderd) en Pin (daarna "
                                            "niet meer opgeslagen); bij Horeca en Contant schrijft Perl later dezelfde regel."},
    },
    {
        "name": "beheer: afdeling in gebruik verwijderen (Perl kijkt alleen naar dpt_trans)",
        "config": ADMIN,
        "ops": [am("department", id=601, mode="delete")],
        "expect_diff": {"op 1": "AM::delete_department verwijdert een afdeling die in gl/ar gebruikt wordt "
                                "(get_department kijkt alleen in dpt_trans). Python weigert (README 78).",
                        "tabel department": "Zie op 1.",
                        "tabel audittrail": "Perl schrijft 'deleted' voor de verwijderde afdeling."},
    },
    {
        "name": "beheer: talen en projecten",
        "config": ADMIN,
        "ops": [
            am("language", code="de", description="Duits"),
            am("language", code="de", description="Deutsch", oldcode="de"),
            am("language", code="d e", description="Duits"),          # fout: bestaat al (code zonder spaties)
            am("project", projectnumber="P-003", description="Nieuw", startdate="2026-01-01", customer_id=900),
            am("project", description="Zonder nummer"),
            am("project", id=700, projectnumber="P-001", description="Website (nieuw)", startdate="2026-01-01"),
            am("project", id="number:P-003", mode="delete"),
            am("language", code="de", mode="delete"),
        ],
    },
    {
        "name": "beheer: taal met dubbele spaties en code wijzigen terwijl in gebruik (Perl-fouten)",
        "config": ADMIN,
        "ops": [
            am("language", code="fr", description="Frans  (FR)"),
            am("language", code="eng", description="English", oldcode="en"),
        ],
        "expect_diff": {
            "op 2": "Perl hernoemt een taalcode die in translation gebruikt wordt zonder de verwijzingen bij te "
                    "werken; Python weigert (README 79).",
            "tabel language": "Perl maakt van dubbele spaties in de omschrijving '-' ('Frans-(FR)'); Python één "
                              "spatie (README 79). En zie op 2.",
            "tabel audittrail": "Perl schrijft 'saved' voor de hernoemde taal.",
        },
    },
]
