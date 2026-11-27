"""Scenario's facturen met artikelen (fase 3 stap 2, blok "is"): is.pl/ir.pl als browser vs Python.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios is --dbprefix is``.
"""

ADMIN = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}

#: testdata: artikelen met voorraad, een dienst, eenduidige btw-tarieven
IS_SETUP = [
    "DELETE FROM tax WHERE taxnumber IS NULL",
    "INSERT INTO parts (id, partnumber, description, unit, sellprice, lastcost, listprice, inventory_accno_id, "
    "income_accno_id, expense_accno_id, onhand) VALUES "
    "(2001, 'A-1', 'Appels', 'kg', 5, 2, 5, (SELECT id FROM chart WHERE accno = '7001'), "
    " (SELECT id FROM chart WHERE accno = '8520'), (SELECT id FROM chart WHERE accno = '8020'), 0), "
    "(2002, 'B-1', 'Bananen', 'kg', 3.5, 1.25, 3.5, (SELECT id FROM chart WHERE accno = '7001'), "
    " (SELECT id FROM chart WHERE accno = '8520'), (SELECT id FROM chart WHERE accno = '8020'), 0), "
    "(2003, 'D-1', 'Bezorgen', 'st', 10, 0, 10, NULL, (SELECT id FROM chart WHERE accno = '8520'), "
    " (SELECT id FROM chart WHERE accno = '8100'), 0)",
    "INSERT INTO partstax (parts_id, chart_id) SELECT p, c.id FROM (VALUES (2001), (2002), (2003)) v(p) "
    "CROSS JOIN chart c WHERE c.accno IN ('1810', '1800')",
    "INSERT INTO parts (id, partnumber, description, unit, sellprice, lastcost, listprice, inventory_accno_id, "
    "income_accno_id, expense_accno_id, onhand) VALUES "
    "(2004, 'C-1', 'Cake', 'st', 12.95, 4.333, 12.95, (SELECT id FROM chart WHERE accno = '7002'), "
    " (SELECT id FROM chart WHERE accno = '8530'), (SELECT id FROM chart WHERE accno = '8030'), 0)",
    "INSERT INTO partstax (parts_id, chart_id) SELECT 2004, id FROM chart WHERE accno IN ('1811', '1801')",
    # samengesteld artikel P-1 = 2 x A-1 + 1 x B-1 + bezorgen, 3 op voorraad (geassembleerd)
    "INSERT INTO parts (id, partnumber, description, unit, sellprice, listprice, lastcost, income_accno_id, assembly, "
    "onhand) VALUES (2010, 'P-1', 'Fruitmand', 'st', 25, 25, 0, (SELECT id FROM chart WHERE accno = '8520'), true, 3)",
    "INSERT INTO partstax (parts_id, chart_id) SELECT 2010, id FROM chart WHERE accno = '1810'",
    "INSERT INTO assembly (id, aid, parts_id, qty, bom, adj) VALUES (1, 2010, 2001, 2, false, true), "
    "(2, 2010, 2002, 1, false, true), (3, 2010, 2003, 1, false, true)",
    "UPDATE customer SET curr = 'USD' WHERE id = 903",
    "SELECT setval('invoiceid', 1000)",
]


def isu(**args):
    return {"op": "is_ui", "args": args}


SCENARIOS = [
    {
        "name": "factuurscherm (is.pl/ir.pl): inkoop, verkoop met kostprijs, opnieuw opslaan",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"},
                          {"partnumber": "B-1", "qty": "4", "sellprice": "1.25"}]), "save_as": "p1"},
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5"},
                          {"partnumber": "D-1", "qty": "1", "sellprice": "10", "discount": "10"}]), "save_as": "s1"},
            isu(vc="customer", id="$s1"),
            isu(vc="vendor", id="$p1"),
        ],
    },
    {
        "name": "factuurscherm: verkoop zonder voorraad, kostprijs bij latere inkoop, aantal wijzigen, verwijderen",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "3", "sellprice": "5"}]), "save_as": "s1"},
            {**isu(vc="customer", transdate="2026-03-02", vc_id=902,
                   lines=[{"partnumber": "A-1", "qty": "2", "sellprice": "5.5"}]), "save_as": "s2"},
            {**isu(vc="vendor", transdate="2026-03-10", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "2.10"}]), "save_as": "p1"},
            isu(vc="customer", id="$s2", set={"1": {"qty": "1"}}),
            {**isu(vc="vendor", transdate="2026-03-12", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "6", "sellprice": "2.40"}]), "save_as": "p2"},
            isu(vc="customer", id="$s1", mode="delete"),
        ],
    },
    {
        "name": "factuurscherm: btw inbegrepen, twee tarieven, korting",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905, taxincluded=True,
                   lines=[{"partnumber": "C-1", "qty": "3", "sellprice": "4.36"},
                          {"partnumber": "A-1", "qty": "7", "sellprice": "2.42"}]), "save_as": "p1"},
            {**isu(vc="customer", transdate="2026-03-01", vc_id=902, taxincluded=True,
                   lines=[{"partnumber": "C-1", "qty": "2", "sellprice": "12.95", "discount": "5"},
                          {"partnumber": "A-1", "qty": "3", "sellprice": "4.99"},
                          {"partnumber": "D-1", "qty": "1", "sellprice": "10"}]), "save_as": "s1"},
            isu(vc="customer", id="$s1"),
        ],
    },
    {
        "name": "factuurscherm: vreemde valuta met betalingen",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "B-1", "qty": "10", "sellprice": "1.25"}]), "save_as": "p1"},
            {**isu(vc="customer", transdate="2026-05-20", vc_id=903, currency="USD", exchangerate="0.9",
                   lines=[{"partnumber": "B-1", "qty": "3", "sellprice": "3.33"},
                          {"partnumber": "D-1", "qty": "1", "sellprice": "11.11"}],
                   payments=[{"amount": "10", "datepaid": "2026-05-25", "accno": "1100", "source": "B1",
                              "exchangerate": "0.95"},
                             {"amount": "11.10", "datepaid": "2026-05-20", "accno": "1100", "source": "B2",
                              "exchangerate": "0.9"}]),
             "save_as": "s1"},
            isu(vc="customer", id="$s1"),
        ],
    },
    {
        "name": "factuurscherm: volledig betaald, prijs van inkoop wijzigen, post als nieuw",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "5", "sellprice": "2"}],
                   payments=[{"amount": "12.10", "datepaid": "2026-02-05", "accno": "1100", "source": "U1"}]),
             "save_as": "p1"},
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "2", "sellprice": "5"}],
                   payments=[{"amount": "12.10", "datepaid": "2026-03-01", "accno": "1100", "source": "K1"}]),
             "save_as": "s1"},
            isu(vc="vendor", id="$p1", set={"1": {"sellprice": "2.2"}}),
            {**isu(vc="customer", id="$s1", mode="post_as_new"), "save_as": "s2"},
        ],
    },
    {
        "name": "factuurscherm: samengesteld artikel verkopen",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"},
                          {"partnumber": "B-1", "qty": "4", "sellprice": "1.25"}]), "save_as": "p1"},
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "P-1", "qty": "2", "sellprice": "25"}]), "save_as": "s1"},
            isu(vc="customer", id="$s1"),
        ],
    },
    {
        "name": "factuurscherm: inkoop verwijderen na verkoop, projecten",
        "config": ADMIN,
        "setup": IS_SETUP + ["INSERT INTO project (id, projectnumber, description) VALUES (700, 'PR-1', 'Markt')"],
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "3", "sellprice": "2"}]), "save_as": "p1"},
            {**isu(vc="vendor", transdate="2026-02-02", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "3", "sellprice": "2.5"}]), "save_as": "p2"},
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5", "project_id": 700}]), "save_as": "s1"},
            isu(vc="vendor", id="$p1", mode="delete"),
        ],
        "expect_diff": {"tabel parts": "F8: gemiddelde inkoopprijs (avgcost) wordt ook bij verwijderen van een inkoop "
                                       "opnieuw berekend; Perl liet hem staan"},
    },
    {
        "name": "factuurscherm: btw inbegrepen in vreemde valuta (inkoop)",
        "config": ADMIN,
        "setup": IS_SETUP + ["UPDATE vendor SET curr = 'USD' WHERE id = 905"],
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905, currency="USD", exchangerate="0.8",
                   taxincluded=True, lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "2.42"}]), "save_as": "p1"},
        ],
        "expect_diff": {"tabel invoice": "F7: nettoprijs bij btw inbegrepen + valuta: Perl trekt de btw in "
                                         "standaardvaluta af van de prijs in vreemde valuta (invoice.sellprice fout)",
                        "tabel parts": "F7: laatste/gemiddelde inkoopprijs volgen uit dezelfde (bij Perl foute) prijs"},
    },
    {
        "name": "factuurscherm: creditnota klant en leverancier (retouren, F5)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"}]), "save_as": "p1"},
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5"}]), "save_as": "s1"},
            {**isu(vc="customer", transdate="2026-03-05", vc_id=900, type="credit_invoice",
                   lines=[{"partnumber": "A-1", "qty": "1", "sellprice": "5"}]), "save_as": "c1"},
            {**isu(vc="vendor", transdate="2026-03-06", vc_id=905, type="debit_invoice",
                   lines=[{"partnumber": "A-1", "qty": "2", "sellprice": "2.5"}]), "save_as": "d1"},
        ],
        "expect_diff": {"tabel acc_trans": "F5: retour klant tegen oorspronkelijke kostprijs (omzet terug + voorraad "
                                           "tegen kostprijs), retour leverancier tegen FIFO-kostprijs (verschil naar "
                                           "kostprijsrekening); Perl boekt beide tegen de verkoop-/retourprijs"},
    },
]
