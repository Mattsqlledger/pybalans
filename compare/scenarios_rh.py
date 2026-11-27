"""Scenario's herhalen van facturen met artikelen en orders (fase 3 stap 4, R1, RH1–RH6): factuur/order
aanmaken als browser (is.pl/ir.pl/oe.pl), herhaalschema als browser (Schedule → save_schedule) en
am.pl recurring_transactions → process_transactions, tegen de webcode + sqlledger.recurring.

Datums van de schema's liggen in 2027 (zie scenarios_recurring.py: dan schat Perl dezelfde volgende
datum als Python). Waar Python bewust afwijkt maakt een slot-'sql' de afwijking in de Python-database
ongedaan (in de Perl-database raakt die niets) of staat het verschil als verwacht tabelverschil.

Draaien: ``python compare/run_compare.py --sldir $SRC --scenarios rh --dbprefix rh``.
"""

from scenarios_is import ADMIN, IS_SETUP, isu
from scenarios_oe import oeu

# zoals scenarios_recurring.py (niet importeren: die module importeert scenarios.py, dat dit bestand laadt)
NO_SCHEDULE_TRAIL = {"op": "sql", "sql": "DELETE FROM audittrail WHERE formname = 'recurring'"}


def process(*ids):
    return {"op": "recurring_ui", "args": {"mode": "process", "ids": list(ids)}}


def sched(module, tid, **kw):
    return {"op": "recurring_ui", "args": {"mode": "schedule", "module": module, "id": tid, "repeat": 1,
                                            "unit": "months", **kw}}


#: order openen (Schedule): OE.pm retrieve zet locks op oe én ar/ap; Perl ruimt alleen die op oe op
SEMAPHORE = {"tabel semaphore": "Perl laat na het openen van een order (OE.pm retrieve) een lock op ar/ap staan; "
                                "de lock op oe ruimt het verwerken op (zoals scenarios_oe)"}
#: niets geboekt: de lock van het openen (schemascherm) blijft in Python staan (zoals fase 2)
SEMAPHORE_SKIP = {"tabel semaphore": "Perl's boeking ruimt de lock van het openen (schemascherm) op; Python boekt "
                                     "niet, de lock blijft tot de gebruiker de factuur sluit"}

STOCK = isu(vc="vendor", transdate="2026-02-01", vc_id=905,
            lines=[{"partnumber": "A-1", "qty": "20", "sellprice": "2"},
                   {"partnumber": "B-1", "qty": "10", "sellprice": "1.25"}])

SCENARIOS = [
    {
        "name": "herhalen: verkoopfactuur met artikelen, korting en dienst (2 termijnen)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            STOCK,
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900, description="Fruit maart",
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5"},
                          {"partnumber": "B-1", "qty": "2", "sellprice": "3.5", "discount": "10"},
                          {"partnumber": "D-1", "qty": "1", "sellprice": "10"}]), "save_as": "s1"},
            # RH1: een nieuwe artikelprijs geldt niet voor de kopie (beide: prijs van het origineel)
            {"op": "sql", "sql": "UPDATE parts SET sellprice = 6 WHERE id = 2001"},
            sched("is", "$s1", startdate="2027-01-15", howmany=3, description="Fruit"),
            process("$s1"),
            process("$s1"),
            NO_SCHEDULE_TRAIL,
            # Perl: omschrijving van het schema op de kopie (README 92); alleen in Python ongedaan maken
            {"op": "sql", "sql": "UPDATE ar SET description = 'Fruit' WHERE transdate >= '2027-01-01'"},
        ],
    },
    {
        "name": "herhalen: inkoopfactuur (voorraad en kostprijs van een open verkoop)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**isu(vc="vendor", transdate="2026-02-01", vc_id=905, invnumber="LF-9",
                   # prijzen met 2 decimalen: ir.pl rondt meer decimalen af (F3, bij facturen al getoetst)
                   lines=[{"partnumber": "A-1", "qty": "5", "sellprice": "2.05"},
                          {"partnumber": "C-1", "qty": "3", "sellprice": "4.33"}]), "save_as": "p1"},
            # verkoop na de termijn: de kopie (2027-01-10) levert de kostprijs voor wat niet gedekt is
            isu(vc="customer", transdate="2027-02-01", vc_id=900,
                lines=[{"partnumber": "A-1", "qty": "8", "sellprice": "5"}]),
            # Perl: inkoopkopie vervalt op de boekdatum (README 93); in beide gelijk maken
            {"op": "sql", "sql": "UPDATE ap SET duedate = transdate"},
            sched("ir", "$p1", startdate="2027-01-10", howmany=2, description="Fruit inkoop"),
            process("$p1"),
            NO_SCHEDULE_TRAIL,
            {"op": "sql", "sql": "UPDATE ap SET description = 'Fruit inkoop' WHERE transdate >= '2027-01-01'"},
        ],
    },
    {
        "name": "herhalen: verkoop samengesteld artikel",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            STOCK,
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "P-1", "qty": "1", "sellprice": "25"}]), "save_as": "s1"},
            sched("is", "$s1", startdate="2027-01-15", howmany=2, description=""),
            process("$s1"),
            NO_SCHEDULE_TRAIL,
        ],
    },
    {
        "name": "herhalen: btw-instelling klant gewijzigd na het origineel (RH2)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            STOCK,
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "2", "sellprice": "5"}]), "save_as": "s1"},
            {"op": "sql", "sql": "DELETE FROM customertax WHERE customer_id = 900 AND chart_id = "
                                 "(SELECT id FROM chart WHERE accno = '1810')"},
            sched("is", "$s1", startdate="2027-01-15", howmany=2, description=""),
            process("$s1"),
            NO_SCHEDULE_TRAIL,
        ],
    },
    {
        "name": "herhalen: leverdatum op de factuurregel (RH4)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            STOCK,
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "2", "sellprice": "5", "deliverydate": "2026-03-10"}]),
             "save_as": "s1"},
            sched("is", "$s1", startdate="2027-01-15", howmany=2, description=""),
            process("$s1"),
            NO_SCHEDULE_TRAIL,
        ],
        "expect_diff": {"tabel invoice": "RH4: Python schuift de leverdatum van de regel evenveel dagen mee "
                                         "(2027-01-24); Perl houdt 2026-03-10"},
    },
    {
        "name": "herhalen: te weinig voorraad (voorraad controleren, RH3)",
        "config": ADMIN,
        "setup": IS_SETUP + ["UPDATE parts SET checkinventory = true WHERE id = 2001"],
        "ops": [
            isu(vc="vendor", transdate="2026-02-01", vc_id=905, lines=[{"partnumber": "A-1", "qty": "2",
                                                                         "sellprice": "2"}]),
            {**isu(vc="customer", transdate="2026-03-01", vc_id=900,
                   lines=[{"partnumber": "A-1", "qty": "2", "sellprice": "5"}]), "save_as": "s1"},
            sched("is", "$s1", startdate="2027-01-15", howmany=2, description=""),
            process("$s1"),
            NO_SCHEDULE_TRAIL,
        ],
        "expect_diff": {**SEMAPHORE_SKIP,
                        "tabel ar": "RH3: Perl (IS::post_invoice) controleert de voorraad niet en boekt de kopie "
                                    "(voorraad -2); Python slaat de termijn over",
                        "tabel acc_trans": "idem (RH3)", "tabel invoice": "idem (RH3)",
                        "tabel parts": "idem: voorraad A-1 -2 in Perl (RH3)",
                        "tabel recurring": "idem: volgende datum blijft staan (RH3)",
                        "tabel audittrail": "idem (RH3)", "tabel defaults": "idem: geen factuurnummer (RH3)",
                        "tabel inventory": "idem (RH3)", "tabel x_cogs_allocation": "idem (RH3)",
                        "tabel x_invoice_cost": "idem (RH3)"},
    },
    {
        "name": "herhalen: verkooporder met leverdatum (kop)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="sales_order", transdate="2026-03-01", vc_id=900, reqdate="2026-03-20", ponumber="K-77",
                   lines=[{"partnumber": "A-1", "qty": "4", "sellprice": "5"},
                          {"partnumber": "C-1", "qty": "3", "sellprice": "12.95", "discount": "10"}]),
             "save_as": "o1"},
            sched("oe", "$o1", type="sales_order", startdate="2027-01-15", howmany=3, description="Weekorder"),
            process("$o1"),
            process("$o1"),
            NO_SCHEDULE_TRAIL,
            {"op": "sql", "sql": "UPDATE oe SET description = 'Weekorder' WHERE transdate >= '2027-01-01'"},
        ],
        "expect_diff": {**SEMAPHORE,
                        "tabel orderitems": "RH4: Python schuift de leverdatum van de orderregels evenveel dagen mee "
                                            "als de kop (2027-02-03, 2027-03-06); Perl houdt 2026-03-20 op de regels"},
    },
    {
        "name": "herhalen: inkooporder deels geleverd (RH5)",
        "config": ADMIN,
        "setup": IS_SETUP,
        "ops": [
            {**oeu(type="purchase_order", transdate="2026-02-01", vc_id=905,
                   lines=[{"partnumber": "A-1", "qty": "10", "sellprice": "2"},
                          {"partnumber": "B-1", "qty": "4", "sellprice": "1.25"}]), "save_as": "po"},
            {**oeu(type="purchase_order", id="$po", mode="invoice", invnumber="LF-1",
                   ship={"1": "6", "2": "4"}), "save_as": "i1"},
            sched("oe", "$po", type="purchase_order", startdate="2027-01-15", howmany=2, description=""),
            process("$po"),
            NO_SCHEDULE_TRAIL,
        ],
        "expect_diff": {**SEMAPHORE,
                        "tabel inventory": "O3: Perl schrijft bij factureren magazijnbewegingen (inventory)",
                        "tabel orderitems": "RH5: Perl neemt het geleverde aantal (ship 6/4) over in de kopie; "
                                            "Python begint de nieuwe order op 0"},
    },
]
