"""Scenario's op een tweede echte SQL-Ledger-administratie (UK, GBP/EUR/USD,
afdelingen, projecten, openstaande posten). Gebruik met
run_compare.py --scenarios c --template <db>. Bevat geen bedrijfsgegevens:
alleen rekeningnummers, jaren en interne ids van afdelingen/projecten.
"""

CFG = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}
YEARS = range(2010, 2017)
DEPTS = [19160, 19161]
PROJECTS = [14118, 14811, 18894]
TAX = ["1502", "1503", "1510", "1511", "1512", "1520", "1530", "3800", "48", "49"]
PAID = ["1100", "1101", "1103", "1105", "1110", "1120", "1140", "1150", "1151", "1152", "1155",
        "1199", "1290", "2955", "55003"]
ACCOUNTS = "0330 0335 0340 0345 0350 0355 1100 1101 1103 1105 1110 1120 1140 1150 1151 1152 1155 1290 1300 1305 1310 1321 1399 1400 1401 1402 1403 1404 1405 1406 1407 1408 1409 1410 1420 1430 1450 1490 1502 1503 1510 1511 1512 1520 2010 2020 2030 2099 2110 2120 2125 2130 2131 2140 2600 2605 2620 2635 2640 2701 2740 2750 2755 2759 2760 2770 2790 2801 2810 2811 2820 2821 2840 2870 2875 2885 2890 2895 2900 2905 2950 2955 2999 3801 3810 3820 3855 3870 3875 3890 3891 3895 40100.000 40110 40111 40112 40113 40114 40120 40121 40122 40123 40124 40125 40130 40131 40132 40133 40134 40135 40136 40140 40141 40150 40151 40152 40153 40154 40155 40160 40161 40162 40175 40190 40201 40202 40203 40204 40205 40206 40300 40340 40350 40400 40501 40502 40503 40504 40505 40509 40510 40511 50101 50102 50103 50104 50109 50201 50202 50203 50301 50302 50303 50304 50305 50400 50500 51910 51920 51930 51940 51990 52110 52115 52120 52130 52140 52400 53101 53110 53120 53130 53200 53210 53215 53220 53230 53300 53350 53360 53370 53380 53400 53410 53500 53600 53601 53700 53800 53901 53902 54001 54100 54200 54300 54400 55001 55002 55003 55010 55020 56001 56003 57001 57002 57003 57100 58001 58002 58004".split()


def year_ops(y):
    f, t = f"{y}-01-01", f"{y}-12-31"
    return [
        {"op": "trial_balance", "args": {"fromdate": f, "todate": t}},
        {"op": "trial_balance", "args": {"todate": t, "all_accounts": 1}},
        {"op": "trial_balance", "args": {"fromdate": f, "todate": t, "department_id": DEPTS[0]}},
        {"op": "trial_balance", "args": {"fromdate": f, "todate": t, "project_id": PROJECTS[0]}},
        {"op": "income_statement", "args": {"fromdate": f, "todate": t, "decimalplaces": 2,
                                            "includeperiod": "month", "previousyear": 1}},
        {"op": "income_statement", "args": {"fromdate": f, "todate": t, "decimalplaces": 2, "currency": "USD"}},
        {"op": "income_statement", "args": {"fromdate": f, "todate": t, "decimalplaces": 2, "method": "cash"}},
        {"op": "income_statement", "args": {"fromdate": f, "todate": t, "decimalplaces": 2,
                                            "department_id": DEPTS[1]}},
        {"op": "balance_sheet", "args": {"todate": t, "decimalplaces": 2, "includeperiod": "quarter",
                                         "previousyear": 1}},
        {"op": "balance_sheet", "args": {"todate": f"{y}-06-30", "decimalplaces": 2, "currency": "EUR"}},
        {"op": "list", "search": {"datefrom": f"{y}-05-01", "dateto": f"{y}-05-31"}},
        {"op": "tax_report", "args": {"db": "ar", "accounts": TAX, "fromdate": f, "todate": t, "summary": 1}},
        {"op": "tax_report", "args": {"db": "ap", "accounts": TAX, "fromdate": f, "todate": t, "summary": 0}},
        {"op": "tax_report", "args": {"db": "ar", "accounts": TAX, "fromdate": f, "todate": t, "summary": 1,
                                      "method": "cash"}},
        {"op": "payments", "args": {"db": "ar", "accounts": PAID, "fromdate": f, "todate": t}},
        {"op": "payments", "args": {"db": "ap", "accounts": PAID, "fromdate": f, "todate": t,
                                    "fx_transaction": 1, "l_subtotal": 1, "sort": "transdate"}},
        {"op": "aging", "args": {"arap": "ar", "todate": t}},
        {"op": "aging", "args": {"arap": "ap", "todate": t, "overdue": 1}},
    ]


SCENARIOS = [{"name": f"ADM C {y}", "config": CFG, "ops": year_ops(y)} for y in YEARS]

SCENARIOS += [
    {
        "name": "ADM C rekeningen en mutaties vanaf 2015",
        "config": CFG,
        "ops": [{"op": "accounts"}] + [
            {"op": "account_tx", "args": {"accno": a, "fromdate": "2015-01-01"}} for a in ACCOUNTS],
    },
    {
        "name": "ADM C openstaande posten en aanmaningen",
        "config": CFG,
        "ops": [
            {"op": "aging", "args": {"arap": "ar", "todate": "2016-06-30"}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2016-06-30", "summary": 1}},
            {"op": "aging", "args": {"arap": "ar", "todate": "2016-06-30", "overdue": 1,
                                     "buckets": ["c0", "c30", "c60", "c90"]}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2016-06-30"}},
            {"op": "aging", "args": {"arap": "ap", "todate": "2016-06-30", "department_id": DEPTS[0]}},
            {"op": "reminder", "args": {}},
        ],
    },
    {
        "name": "ADM C boeken in vreemde valuta en jaarafsluiting",
        "config": CFG,
        "ops": [
            {"op": "post", "save_as": "t1", "trans": {
                "transdate": "2016-06-15", "description": "Test USD", "currency": "USD", "exchangerate": "0.7031",
                "department_id": DEPTS[0],
                "lines": [{"accno": "53110", "debit": "1000.00", "credit": "", "project_id": PROJECTS[1]},
                          {"accno": "1100", "debit": "", "credit": "1000.00"}]}},
            {"op": "get", "id": "$t1"},
            {"op": "post", "trans": {"transdate": "2014-12-31",
                                     "lines": [{"accno": "53110", "debit": "1", "credit": ""},
                                               {"accno": "1100", "debit": "", "credit": "1"}]}},
            {"op": "yearend", "args": {"todate": "2016-06-30", "accno": "2030", "description": "YE H1 2016"}},
            {"op": "yearend", "args": {"todate": "2015-12-31", "accno": "2030", "reference": "YE-2015"}},
            {"op": "income_statement", "args": {"fromdate": "2015-01-01", "todate": "2016-12-31",
                                                "decimalplaces": 2, "includeperiod": "quarter"}},
            {"op": "balance_sheet", "args": {"todate": "2016-06-30", "decimalplaces": 2, "previousyear": 1}},
            {"op": "trial_balance", "args": {"fromdate": "2015-01-01", "todate": "2016-12-31"}},
        ],
    },
]

# ---------------------------------------------------------------- fase 1a
# Alle bestaande verkoop-/inkoopboekingen zonder artikelen openen en ongewijzigd
# opnieuw boeken (Perl via het scherm, Python via de bibliotheek). Getalnotatie
# 1,000.00 omdat de Perl-driver de HTML-opmaak overslaat. Afsluitdatum uit in de
# testkopie zodat alle jaren meedoen.
CFG_UI = dict(CFG, numberformat="1,000.00")
SCENARIOS += [
    {"name": "ADM C opnieuw boeken verkoop (zonder artikelen)", "config": CFG_UI,
     "setup": ["UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'"],
     "ops": [{"op": "arap_repost_all", "args": {"vc": "customer",
              "sql": "SELECT id FROM ar WHERE NOT invoice ORDER BY id"}}]},
    {"name": "ADM C opnieuw boeken inkoop (zonder artikelen)", "config": CFG_UI,
     "setup": ["UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'"],
     "ops": [{"op": "arap_repost_all", "args": {"vc": "vendor",
              "sql": "SELECT id FROM ap WHERE NOT invoice ORDER BY id"}}]},
]

# alle openstaande posten afbetalen via het betaalscherm (fase 1b)
SCENARIOS += [
    {"name": "ADM C alle openstaande posten afbetalen", "config": CFG_UI,
     "ops": [
         {"op": "cp_pay_all", "args": {"vc": "customer", "datepaid": "2016-06-30", "payment_accno": "1100",
                                        "exchangerates": {"EUR": "0.75", "USD": "0.66"},
                                        "sql": "SELECT a.customer_id, trim(a.curr), string_agg(a.id::text, ',' ORDER BY a.id) FROM ar a WHERE a.amount <> a.paid AND a.approved AND NOT a.onhold GROUP BY 1, 2 ORDER BY 1, 2"}},
         {"op": "cp_pay_all", "args": {"vc": "vendor", "datepaid": "2016-06-30", "payment_accno": "1100",
                                        "exchangerates": {"EUR": "0.75", "USD": "0.66"},
                                        "sql": "SELECT a.vendor_id, trim(a.curr), string_agg(a.id::text, ',' ORDER BY a.id) FROM ap a WHERE a.amount <> a.paid AND a.approved AND NOT a.onhold GROUP BY 1, 2 ORDER BY 1, 2"}},
         {"op": "aging", "args": {"arap": "ar", "todate": "2016-06-30"}},
         {"op": "aging", "args": {"arap": "ap", "todate": "2016-06-30"}},
     ]},
]

# overzicht verkoop/inkoop (AA::transactions + aa.pl), per jaar, met valuta-uitsplitsing
def list_ops(y):
    f, t = f"{y}-01-01", f"{y}-12-31"
    return [
        {"op": "arap_list", "args": {"vc": "customer", "summary": 1, "transdatefrom": f, "transdateto": t,
                                     "l_curr": 1}},
        {"op": "arap_list", "args": {"vc": "vendor", "summary": 1, "transdatefrom": f, "transdateto": t,
                                     "l_curr": 1, "l_subtotal": 1, "sort": "name"}},
        {"op": "arap_list", "args": {"vc": "customer", "outstanding": 1, "transdateto": t, "l_curr": 1}},
        {"op": "arap_list", "args": {"vc": "vendor", "outstanding": 1, "transdateto": t}},
    ]


SCENARIOS += [{"name": f"ADM C overzicht verkoop/inkoop {y}", "config": CFG_UI, "ops": list_ops(y)}
              for y in range(2010, 2017)]

# relaties zoeken (Form::get_name) en keuzelijsten (Form::all_vc)
SCENARIOS += [{"name": "ADM C relaties zoeken", "config": CFG_UI, "ops": [
    {"op": "names", "args": {"vc": vc, **a}} for vc in ("customer", "vendor") for a in (
        {"name": ""}, {"name": "a"}, {"name": "en"}, {"number": "1"}, {"name": "", "transdate": "2015-06-30"},
        {"list": 1}, {"list": 1, "transdate": "2015-06-30"}, {"list": 1, "open_invoices": 1})]}]

# ca.pl list_transactions-scherm (lopend saldo, subtotalen, totaal)
SCENARIOS += [{
    "name": "ADM C mutatiescherm (ca.pl)",
    "config": CFG,
    "ops": [{"op": "account_screen", "args": {"accno": a, "fromdate": "2015-01-01"}} for a in PAID]
    + [{"op": "account_screen", "args": {"accno": a, "fromdate": "2015-01-01", "todate": "2015-12-31",
                                         "sort": "reference", "l_subtotal": 1}} for a in ("1100", "1300", "2010")],
}]

# gl.pl transactions-scherm (journaal: lopend saldo, per rekening, subtotalen)
SCENARIOS += [{
    "name": "ADM C journaalscherm (gl.pl)",
    "config": CFG,
    "ops": [{"op": "gl_screen", "search": {"datefrom": "2015-01-01", "dateto": "2015-03-31"}},
            {"op": "gl_screen", "search": {"datefrom": "2015-01-01", "dateto": "2015-06-30", "l_subtotal": 1}},
            {"op": "gl_screen", "search": {"datefrom": "2015-01-01", "dateto": "2015-12-31", "splitledger": True}},
            {"op": "gl_screen", "search": {"datefrom": "2015-01-01", "dateto": "2015-12-31", "category": "I",
                                           "splitledger": True}}]
    + [{"op": "gl_screen", "search": {"accno": a, "datefrom": "2015-01-01", "dateto": "2015-12-31"}}
       for a in PAID[:6]],
}]

# ================================================================ rc (afletteren, fase 2)
# rc.pl-afletterscherm vs webapp op 1100 (701 afgeletterde regels, laatste ronde 2011-04-30) en een
# nieuwe ronde via de webcode op een kopie (afschriftsaldo = afgeletterd saldo, "auto").
_RC = {"accno": "1100"}
SCENARIOS += [{
    "name": "ADM C afletteren (rc.pl)",
    "config": CFG,
    "setup": ["DELETE FROM defaults WHERE fldname = 'audittrail'"],
    "ops": [
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-01-01", todate="2011-04-30")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-04-01", todate="2011-04-30", summary=0)},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-03-01", todate="2011-03-31")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-04-01", todate="2011-04-30", fx=0)},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-05-01", todate="2011-05-31")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-01-01", todate="2011-04-30", report=1)},
        {"op": "rc_ui", "args": dict(_RC, fromdate="2011-05-01", todate="2011-05-31", select_all=1,
                                     statementbalance="auto")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-06-01", todate="2011-06-30")},
        {"op": "rc_ui", "args": dict(_RC, fromdate="2011-06-01", todate="2011-06-30", summary=0, select_all=1,
                                     statementbalance="auto")},
        {"op": "rc_screen", "args": dict(_RC, fromdate="2011-05-01", todate="2011-06-30", report=1)},
    ],
}]

# blok "oe" (fase 3 stap 3): orders die met de huidige btw-instellingen anders uitrekenen dan opgeslagen
# (510 van de 5.116, oe_keeptest): openen en ongewijzigd opslaan in oe.pl en via het webscherm moet hetzelfde
# geven. Gesloten/gefactureerde orders slaat Python alleen op met force (alleen deze toets; scherm weigert, O6).
OE_DRIFT = [(14824, "purchase_order"), (14872, "sales_order"), (15306, "purchase_order"), (15686, "purchase_order"),
            (15710, "sales_order"), (16715, "sales_order"), (16856, "purchase_order"), (16927, "purchase_order"),
            (17545, "purchase_order"), (18938, "purchase_order"), (19152, "purchase_order"), (20152, "purchase_order"),
            (21214, "purchase_order"), (21769, "purchase_order"), (21995, "purchase_order"),
            (22328, "purchase_order"), (22346, "purchase_order"), (23455, "purchase_order"),
            (23843, "purchase_order"), (23901, "sales_order"), (24399, "purchase_order"), (25215, "purchase_order"),
            (29034, "sales_order"), (31227, "purchase_order"), (32511, "purchase_order")]
SCENARIOS += [{"name": "ADM C orders opnieuw opslaan (btw-instellingen gewijzigd, steekproef 25)", "config": CFG_UI,
               "ops": [{"op": "oe_ui", "args": {"type": t, "id": i, "force": True}} for i, t in OE_DRIFT],
               "expect_diff": {"tabel semaphore": "Perl laat na opslaan van een geopende order een lock op ar/ap "
                                                  "staan (OE.pm retrieve zet locks op oe én ar/ap, save verwijdert "
                                                  "alleen oe)",
                               "tabel oe": "alleen employee_id: Perl verliest de medewerker als die niet meer in de "
                                           "keuzelijst staat (vertrokken; wordt 0), Python houdt hem (gecontroleerd: "
                                           "bedragen en overige kolommen gelijk)"}}]
