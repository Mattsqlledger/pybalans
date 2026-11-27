"""Scenario's tijdkaarten (fase Uren U-1, blok "jc"): jc.pl als browser vs ``sqlledger.timecards``.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios jc``. Ook in de standaardset.
"""

ADMIN = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}

#: een dienst (opbrengst 8520 met IC_income) en een project
JC_SETUP = ["UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'",
            "UPDATE employee SET startdate = '2026-01-01'",
            "INSERT INTO parts (id, partnumber, description, unit, sellprice, income_accno_id, expense_accno_id) "
            "VALUES (1200, 'UUR', 'Advies per uur', 'uur', 85, (SELECT id FROM chart WHERE accno = '8520'), "
            "(SELECT id FROM chart WHERE accno = '8100'))",
            "INSERT INTO parts (id, partnumber, description, unit, sellprice, income_accno_id, expense_accno_id) "
            "VALUES (1201, 'REIS', 'Reistijd', 'uur', 42.5, (SELECT id FROM chart WHERE accno = '8520'), "
            "(SELECT id FROM chart WHERE accno = '8100'))",
            "INSERT INTO project (id, projectnumber, description, customer_id) VALUES (1300, 'P-UREN', 'Verbouwing', "
            "(SELECT min(id) FROM customer))"]


def jc(**args):
    return {"op": "jc_ui", "args": args}


SCENARIOS = [
    {
        "name": "tijdkaart (jc.pl) als browser: nieuw met tijden, eigen aantal, wijzigen",
        "config": ADMIN,
        "setup": JC_SETUP,
        "ops": [
            {**jc(fields={"transdate": "2026-03-02", "projectnumber": "P-UREN", "partnumber": "UUR",
                          "employee": "Jan Jansen", "inhour": "8", "inmin": "30", "outhour": "12", "outmin": "0",
                          "description": "Ontwerp", "notes": "eerste dag"}), "save_as": "t1"},
            {**jc(fields={"transdate": "2026-03-03", "projectnumber": "P-UREN", "partnumber": "REIS",
                          "employee": "Beheerder", "inhour": "22", "inmin": "0", "outhour": "1", "outmin": "15",
                          "noncharge": "0.25"}), "save_as": "t2"},
            {**jc(fields={"transdate": "2026-03-04", "projectnumber": "P-UREN", "partnumber": "UUR",
                          "employee": "Jan Jansen", "qty": "2.5", "sellprice": "90"}), "save_as": "t3"},
            jc(id="$t1", fields={"notes": "aangepast", "outhour": "13", "outmin": "15"}),
            jc(id="$t3", fields={"partnumber": "REIS"}),
        ],
    },
    {
        "name": "tijdkaart (jc.pl): verwijderen",
        "config": ADMIN,
        "setup": JC_SETUP,
        "ops": [
            {**jc(fields={"transdate": "2026-03-02", "projectnumber": "P-UREN", "partnumber": "UUR",
                          "employee": "Jan Jansen", "qty": "1"}), "save_as": "t1"},
            jc(id="$t1", mode="delete"),
        ],
    },
]

#: uren klaarzetten voor "verkooporders maken": twee projecten met een klant, twee diensten
SO_OPS = [
    jc(fields={"transdate": "2026-03-02", "projectnumber": "P-UREN", "partnumber": "UUR", "employee": "Jan Jansen",
               "qty": "3", "notes": "maandag"}),
    jc(fields={"transdate": "2026-03-03", "projectnumber": "P-UREN", "partnumber": "UUR", "employee": "Jan Jansen",
               "qty": "2.5", "notes": "dinsdag"}),
    jc(fields={"transdate": "2026-03-03", "projectnumber": "P-UREN", "partnumber": "REIS", "employee": "Beheerder",
               "qty": "1"}),
    jc(fields={"transdate": "2026-03-04", "projectnumber": "P-TWEE", "partnumber": "UUR", "employee": "Jan Jansen",
               "qty": "4", "sellprice": "90"}),
]
SO_SETUP = JC_SETUP + ["INSERT INTO partstax (parts_id, chart_id) SELECT p, (SELECT id FROM chart WHERE accno = '1810') "
                       "FROM unnest(ARRAY[1200, 1201]) AS p","INSERT INTO project (id, projectnumber, description, customer_id) VALUES (1301, 'P-TWEE', "
                       "'Onderhoud', (SELECT max(id) FROM customer))"]

SCENARIOS += [
    {
        "name": "verkooporders uit project (pe.pl): per regel, alle uren",
        "config": ADMIN,
        "setup": SO_SETUP,
        "ops": SO_OPS + [{"op": "pe_so", "args": {}}],
    },
    {
        "name": "verkooporders uit project (pe.pl): samengevat per dienst, deel van de regels",
        "config": ADMIN,
        "setup": SO_SETUP,
        "ops": SO_OPS + [{"op": "pe_so", "args": {"summary": 1, "rows": [1, 3]}}],
    },
]

SCENARIOS += [
    {
        "name": "verkooporders uit project (pe.pl): dienst zonder btw-rekening",
        "config": ADMIN,
        "setup": JC_SETUP + ["INSERT INTO project (id, projectnumber, description, customer_id) VALUES (1301, 'P-TWEE', "
                             "'Onderhoud', (SELECT max(id) FROM customer))"],
        "expect_diff": {"tabel oe": "SL: een dienst zonder btw-rekening krijgt alle btw-rekeningen van de klant (lege "
                                    "regex in generate_sales_orders past op alles); Python: geen btw (UR9)"},
        "ops": SO_OPS + [{"op": "pe_so", "args": {}}],
    },
]
