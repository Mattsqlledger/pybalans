"""Scenario's terugkerende boekingen (fase 2, T1–T8): herhaalschema als browser op een
gl/ar/ap-boeking (arap.pl schedule/save_schedule) en am.pl recurring_transactions →
process_transactions, tegen de webcode (routes_recurring) + sqlledger.recurring.

Datums liggen in 2027 (na vandaag), zodat Perl's schatting van de volgende datum bij een
nieuw schema (Form::save_recurring) gelijk is aan de start, net als in Python, en de
lijst niets standaard aanvinkt.

Waar Python bewust afwijkt (README 85–94) maakt een slot-'sql' alleen in de Python-database
de afwijking ongedaan (in de Perl-database raakt die niets): zo blijft zichtbaar dat de
rest van de databases gelijk is. Het verschil zelf staat in de vergelijking van het
scherm of als verwacht tabelverschil.
"""

from scenarios import ADMIN, PAY_SETUP

# schema-audittrail (opslaan/verwijderen) is nieuw in Python (README 93)
NO_SCHEDULE_TRAIL = {"op": "sql", "sql": "DELETE FROM audittrail WHERE formname = 'recurring'"}
# afdeling van een memoriaalkopie: Perl verliest die (GL::transaction geeft department_id, GL::post_transaction
# leest department), Python neemt hem mee (README 92); alleen in Python ongedaan maken
NO_GL_DEPARTMENT = [
    {"op": "sql", "sql": "DELETE FROM dpt_trans WHERE trans_id IN (SELECT id FROM gl WHERE transdate >= '2027-01-01')"},
    {"op": "sql", "sql": "UPDATE gl SET department_id = 0 WHERE transdate >= '2027-01-01'"},
]


def sched(module, tid, **kw):
    return {"op": "recurring_ui", "args": {"mode": "schedule", "module": module, "id": tid, "repeat": 1,
                                            "unit": "months", **kw}}


def process(*ids, **nextdates):
    a = {"mode": "process", "ids": list(ids)}
    if nextdates:
        a["nextdates"] = {k[1:]: v for k, v in nextdates.items()}
    return {"op": "recurring_ui", "args": a}


SCENARIOS = [
    {
        "name": "terugkerend: memoriaal, schema als browser + 3 termijnen verwerken",
        "config": ADMIN,
        "ops": [
            sched("gl", 20103, startdate="2027-01-15", howmany=3, description="LOON-2026-02"),
            process(20103),
            process(20103),
            process(20103),
            process(20103),           # afgelopen: staat niet meer in de lijst, niets geboekt
            sched("gl", 20105, startdate="2027-02-10", repeat=2, unit="weeks", howmany=2,
                  description="KOSTEN-2026-04"),
            process(20105),
            {"op": "recurring_ui", "args": {"mode": "delete", "module": "gl", "id": 20105}},
            NO_SCHEDULE_TRAIL,
            *NO_GL_DEPARTMENT,
        ],
    },
    {
        "name": "terugkerend: verkoop met betaling, inkoop zonder, valuta met koers",
        "config": ADMIN,
        "setup": PAY_SETUP + [
            "UPDATE ap SET duedate = transdate WHERE id = 4001",     # Perl: inkoop vervalt op boekdatum
            "INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2027-01-20', 1.3)",
        ],
        "ops": [
            sched("ar", 3006, startdate="2027-01-01", howmany=2, payment=1, description="Catering"),
            sched("ap", 4001, startdate="2027-01-05", howmany=2, description="Meel"),
            sched("ar", 3003, startdate="2027-01-20", howmany=2, description="Export"),
            process(3006, 4001),
            process(3003),            # apart: in één run lekt in Perl de betaling van 3006 naar 3003 (README 91)
            process(3006, 4001),
            NO_SCHEDULE_TRAIL,
        ],
    },
    {
        "name": "terugkerend: maandeinde (31-01, 28-02, 31-03)",
        "config": ADMIN,
        "ops": [
            sched("gl", 20101, startdate="2027-01-31", howmany=4, description="OMZ-2026-01"),
            process(20101),
            process(20101),
            process(20101),
            NO_SCHEDULE_TRAIL,
            *NO_GL_DEPARTMENT,
            # alleen in Python: 31-03 → 28-03 en volgende 30-04 → 28-04 (Perl-uitkomst)
            {"op": "sql", "sql": "UPDATE gl SET transdate = '2027-03-28' WHERE transdate = '2027-03-31'"},
            {"op": "sql", "sql": "UPDATE acc_trans SET transdate = '2027-03-28' WHERE transdate = '2027-03-31'"},
            {"op": "sql", "sql": "UPDATE recurring SET nextdate = '2027-04-28' WHERE nextdate = '2027-04-30'"},
        ],
        "expect_diff": {
            "op 4": "README 86: Perl telt een maand op bij de vorige datum (31-01 → 28-02 → 28-03); "
                    "Python rekent vanaf de start (31-03). Scherm: volgende datum 28-03 vs 31-03.",
        },
    },
    {
        "name": "terugkerend: aangepaste volgende datum in de lijst",
        "config": ADMIN,
        "ops": [
            sched("gl", 20103, startdate="2027-01-15", howmany=3, description="LOON-2026-02"),
            process(20103, _20103="2027-01-20"),
            NO_SCHEDULE_TRAIL,
            {"op": "sql", "sql": "UPDATE recurring SET nextdate = '2027-02-20' WHERE nextdate = '2027-02-15'"},
        ],
        "expect_diff": {},
    },
    {
        "name": "terugkerend: kenmerk en omschrijving van het schema",
        "config": ADMIN,
        "ops": [
            sched("gl", 20103, startdate="2027-01-15", howmany=3, reference="HUUR", description="Huur kantoor"),
            process(20103),
            process(20103),
            NO_SCHEDULE_TRAIL,
            # alleen in Python: tweede kopie kreeg het volgende nummer (Perl: weer HUUR) en de
            # omschrijving van de boeking (Perl: van het schema)
            {"op": "sql", "sql": "UPDATE gl SET reference = 'HUUR' WHERE reference = 'GL-0100'"},
            {"op": "sql", "sql": "UPDATE audittrail SET reference = 'HUUR' WHERE reference = 'GL-0100'"},
            {"op": "sql", "sql": "UPDATE defaults SET fldvalue = 'GL-0099' WHERE fldname = 'glnumber' "
                                 "AND fldvalue = 'GL-0100'"},
            {"op": "sql", "sql": "UPDATE gl SET description = 'Huur kantoor' WHERE description = 'LOON-2026-02' "
                                 "AND id <> 20103"},
        ],
    },
    {
        "name": "terugkerend: inkoop vervaldatum en betaling meenemen",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "ops": [
            sched("ap", 4002, startdate="2027-01-10", howmany=2, payment=1, description="Suiker"),
            process(4002),
            NO_SCHEDULE_TRAIL,
            # alleen in Python: vervaldatum 30 dagen en betaling 35 dagen na de boekdatum
            # (zoals het origineel); Perl leest die afstanden uit ar en gebruikt bij inkoop 0
            {"op": "sql", "sql": "UPDATE ap SET duedate = transdate, datepaid = transdate "
                                 "WHERE transdate = '2027-01-10'"},
            {"op": "sql", "sql": "UPDATE acc_trans SET transdate = '2027-01-10' WHERE transdate = '2027-02-14'"},
        ],
    },
    {
        "name": "terugkerend: valuta zonder koers op de nieuwe datum",
        "config": ADMIN,
        "setup": PAY_SETUP,
        "ops": [
            sched("ar", 3003, startdate="2027-01-20", howmany=2, description=""),
            process(3003),
            NO_SCHEDULE_TRAIL,
        ],
        "expect_diff": {
            "tabel ar": "README 90: zonder koers op 2027-01-20 boekt Perl stil met de oude koers; "
                        "Python slaat het schema over (geen boeking)",
            "tabel acc_trans": "idem (README 90)",
            "tabel recurring": "idem: volgende datum blijft staan (README 90)",
            "tabel audittrail": "idem (README 90)",
            "tabel defaults": "idem: geen factuurnummer uitgegeven (README 90)",
            "tabel exchangerate": "idem: Perl schrijft de oude koers op de nieuwe datum (README 90)",
            "tabel semaphore": "idem: Perl's boeking ruimt de lock van het openen (schemascherm) op; "
                               "Python boekt niet, de lock blijft tot de gebruiker de boeking sluit",
        },
    },
    {
        "name": "terugkerend: betaling van het vorige schema in dezelfde run",
        "config": ADMIN,
        "setup": PAY_SETUP + [
            "INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2027-01-20', 1.3)",
        ],
        "ops": [
            sched("ar", 3006, startdate="2027-01-01", howmany=2, payment=1, description="Catering"),
            sched("ar", 3003, startdate="2027-01-20", howmany=2, description="Export"),
            process(3006, 3003),
            NO_SCHEDULE_TRAIL,
        ],
        "expect_diff": {
            "tabel acc_trans": "README 91: am.pl process_transactions wist \\$form tussen twee schema's, maar de "
                               "betaalregels van de vorige kopie blijven hangen: de kopie van 3003 (zonder "
                               "betaling) krijgt de betaling van 3006 (100) op een rekening NULL",
            "tabel payment": "idem (README 91)",
        },
    },
    {
        "name": "terugkerend: memoriaal met notatie 1.000,00 (bedrag x100)",
        "config": dict(ADMIN, numberformat="1.000,00"),
        "ops": [
            sched("gl", 20102, startdate="2027-02-28", howmany=2, description="OMZ-2026-02"),
            process(20102),
            NO_SCHEDULE_TRAIL,
            *NO_GL_DEPARTMENT,
        ],
        "expect_diff": {
            "tabel acc_trans": "README 89: am.pl geeft de bedragen van de memoriaalkopie ongeformatteerd door "
                               "(5100.5) en GL::post_transaction leest ze in notatie 1.000,00: Perl boekt 51005 "
                               "i.p.v. 5.100,50. Python boekt het juiste bedrag.",
        },
    },
]
