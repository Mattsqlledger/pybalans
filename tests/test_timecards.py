"""Tijdkaarten (sqlledger.timecards, UREN_ANALYSE.md U-1, port van JC.pm)."""

from datetime import date, time
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from sqlledger import ValidationError
from sqlledger.errors import ClosedPeriodError
from sqlledger.parts import Parts
from sqlledger.timecards import TimeCard, TimeCards, TimeCardSearch, clocked


def one(conn, sql, *params):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchone()


@pytest.fixture
def setup(conn, config):
    conn.execute("UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'")
    project = one(conn, "INSERT INTO project (projectnumber, description) VALUES ('P-1', 'Verbouwing') RETURNING id")["id"]
    job_part = one(conn, "SELECT id FROM parts ORDER BY id LIMIT 1")["id"]
    job = one(conn, "INSERT INTO project (projectnumber, description, parts_id, production) "
                    "VALUES ('J-1', 'Productie', %s, 5) RETURNING id", job_part)["id"]
    lib = Parts(conn, config)
    p = lib.new("service")
    p.partnumber, p.description, p.sellprice = "UUR", "Advies per uur", Decimal("85")
    p.income_accno, p.expense_accno = "8520", "8100"
    service = lib.save(p)
    emp = one(conn, "SELECT id FROM employee ORDER BY id LIMIT 1")["id"]
    return {"project": project, "job": job, "service": service, "employee": emp}


def test_clocked_like_jc_pl():
    assert clocked(time(8, 30), time(12, 0)) == Decimal("3.50")
    assert clocked(time(22, 0), time(2, 15)) == Decimal("4.25")          # over middernacht
    assert clocked(time(9, 0), None) is None
    assert clocked(time(9, 0), time(9, 20), 2) == Decimal("0.33")


def test_save_get_search_delete(conn, config, setup):
    tc = TimeCards(conn, config)
    links = tc.links(date(2026, 3, 2))
    numbers = [p["projectnumber"] for p in links["projects"]]
    assert "P-1" in numbers and "J-1" not in numbers                                # geen productie-opdracht
    assert "UUR" in [s["partnumber"] for s in links["services"]]
    card = TimeCard(transdate=date(2026, 3, 2), project_id=setup["project"], parts_id=setup["service"],
                    employee_id=setup["employee"], qty=Decimal("3.5"), sellprice=Decimal("85"),
                    checkedin=time(8, 30), checkedout=time(12, 0), description="Ontwerp", notes="eerste dag")
    cid = tc.save(card)
    row = one(conn, "SELECT * FROM jcitems WHERE id = %s", cid)
    assert row["checkedin"].strftime("%Y-%m-%d %H:%M") == "2026-03-02 08:30"
    assert row["checkedout"].strftime("%H:%M") == "12:00" and row["fxsellprice"] == row["sellprice"] == 85
    got = tc.get(cid)
    assert (got.qty, got.checkedin, got.projectnumber, got.partnumber) == (Decimal("3.5"), time(8, 30), "P-1", "UUR")
    # over middernacht: uit-datum de dag erna
    late = tc.save(TimeCard(transdate=date(2026, 3, 3), project_id=setup["project"], parts_id=setup["service"],
                            employee_id=setup["employee"], qty=Decimal("2"), checkedin=time(23, 0),
                            checkedout=time(1, 0)))
    assert one(conn, "SELECT checkedout::date AS d FROM jcitems WHERE id = %s", late)["d"] == date(2026, 3, 4)
    rows = tc.search(TimeCardSearch(project_id=setup["project"]))
    assert [r["id"] for r in rows] == [cid, late] and rows[0]["amount"] == Decimal("297.5")
    assert tc.search(TimeCardSearch(datefrom=date(2026, 3, 3)))[0]["id"] == late
    # al op een order: niet lager, niet weg
    conn.execute("UPDATE jcitems SET allocated = 3 WHERE id = %s", (cid,))
    got = tc.get(cid)
    got.qty = Decimal("2")
    with pytest.raises(ValidationError, match="verkooporder"):
        tc.save(got)
    with pytest.raises(ValidationError, match="verkooporder"):
        tc.delete(cid)
    assert tc.search(TimeCardSearch(open=False, closed=True)) == []
    tc.delete(late)
    assert one(conn, "SELECT count(*) AS n FROM jcitems")["n"] == 1


def test_checks(conn, config, setup):
    tc = TimeCards(conn, config)
    base = dict(transdate=date(2026, 3, 2), project_id=setup["project"], parts_id=setup["service"], qty=Decimal(1))
    with pytest.raises(ValidationError, match="productie-opdracht"):
        tc.save(TimeCard(**{**base, "project_id": setup["job"]}))
    part = one(conn, "SELECT id FROM parts WHERE inventory_accno_id IS NOT NULL LIMIT 1")
    if part:
        with pytest.raises(ValidationError, match="dienst"):
            tc.save(TimeCard(**{**base, "parts_id": part["id"]}))
    with pytest.raises(ValidationError):
        tc.save(TimeCard(**{**base, "transdate": None}))
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', '2026-03-31') "
                 "ON CONFLICT DO NOTHING")
    conn.execute("UPDATE defaults SET fldvalue = '2026-03-31' WHERE fldname = 'closedto'")
    with pytest.raises(ClosedPeriodError):
        tc.save(TimeCard(**base))
    # zonder medewerker: die van de gebruiker (admin)
    conn.execute("UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'")
    cid = tc.save(TimeCard(**base))
    admin = one(conn, "SELECT id FROM employee WHERE login = 'admin'")
    assert one(conn, "SELECT employee_id FROM jcitems WHERE id = %s", cid)["employee_id"] == (admin["id"] if admin else None)


def test_assignments_and_month_sheet(conn, config, setup):
    from sqlledger.timecards import Assignment, Assignments, month_sheet, save_month
    lib = Assignments(conn, config)
    aid = lib.save(Assignment(employee_id=setup["employee"], project_id=setup["project"], parts_id=setup["service"],
                              validfrom=date(2026, 3, 1), sellprice=Decimal("90")))
    with pytest.raises(ValidationError, match="al in die periode"):
        lib.save(Assignment(employee_id=setup["employee"], project_id=setup["project"], parts_id=setup["service"],
                            validfrom=date(2026, 6, 1)))
    with pytest.raises(ValidationError, match="productie-opdracht"):
        lib.save(Assignment(employee_id=setup["employee"], project_id=setup["job"], parts_id=setup["service"],
                            validfrom=date(2026, 3, 1)))
    a = lib.get(aid)
    a.validto = date(2026, 3, 31)
    lib.save(a)
    lib.save(Assignment(employee_id=setup["employee"], project_id=setup["project"], parts_id=setup["service"],
                        validfrom=date(2026, 4, 1)))                       # aansluitend: mag
    sheet = month_sheet(conn, config, setup["employee"], 2026, 3)
    assert len(sheet) == 1 and sheet[0].sellprice == Decimal("90") and sheet[0].hours == {}
    key = (setup["project"], setup["service"])
    assert save_month(conn, config, setup["employee"], 2026, 3, {(*key, 2): Decimal("8"), (*key, 3): Decimal("7.5")}) == 2
    sheet = month_sheet(conn, config, setup["employee"], 2026, 3)
    assert sheet[0].hours == {2: Decimal("8"), 3: Decimal("7.5")}
    row = one(conn, "SELECT qty, sellprice, checkedin FROM jcitems WHERE checkedin::date = '2026-03-02'")
    assert (row["qty"], row["sellprice"], row["checkedin"].strftime("%H:%M")) == (8, 90, "00:00")
    # ongewijzigd = niets; 0 = weg; dag op een order = vergrendeld
    assert save_month(conn, config, setup["employee"], 2026, 3, {(*key, 2): Decimal("8")}) == 0
    conn.execute("UPDATE jcitems SET allocated = qty WHERE checkedin::date = '2026-03-02'")
    assert month_sheet(conn, config, setup["employee"], 2026, 3)[0].locked == {2: "order"}
    with pytest.raises(ValidationError, match="niet gewijzigd"):
        save_month(conn, config, setup["employee"], 2026, 3, {(*key, 2): Decimal("6")})
    assert save_month(conn, config, setup["employee"], 2026, 3, {(*key, 3): Decimal("0")}) == 1
    assert one(conn, "SELECT count(*) AS n FROM jcitems")["n"] == 1
    # niet toegewezen combinatie: geweigerd
    with pytest.raises(ValidationError, match="niet aan de medewerker toegewezen"):
        save_month(conn, config, setup["employee"], 2026, 3, {(setup["project"], 999999, 4): Decimal("1")})


# ------------------------------------------------------------------ v1.1 interne uren (UREN_ANALYSE.md IN1–IN6)

def _internal_project(conn, config, number="INT-1"):
    from sqlledger.masterdata import MasterData, Project
    md = MasterData(conn, config)
    return md.save_project(Project(projectnumber=number, description="Intern", kind="internal",
                                   new_department="Kantoor")), md


def test_project_kind_and_cost_centre(conn, config, setup):
    from sqlledger.masterdata import MasterData, Project
    md = MasterData(conn, config)
    cust = one(conn, "SELECT min(id) AS id FROM customer")["id"]
    # bestaand project zonder klant en zonder x_project: niet ingedeeld; met klant: klantproject
    assert md.get_project(setup["project"])["kind"] == "none"
    conn.execute("UPDATE project SET customer_id = %s WHERE id = %s", (cust, setup["project"]))
    assert md.get_project(setup["project"])["kind"] == "customer"
    # controles
    with pytest.raises(ValidationError, match="klant bij een klantproject"):
        md.save_project(Project(projectnumber="K-1", kind="customer"))
    with pytest.raises(ValidationError, match="kostenplaats"):
        md.save_project(Project(projectnumber="I-1", kind="internal"))
    with pytest.raises(ValidationError, match="geen klant"):
        md.save_project(Project(projectnumber="I-1", kind="internal", customer_id=cust, new_department="X"))
    profit = md.save_department("Verkoop", "P")
    with pytest.raises(ValidationError, match="soort kostenplaats"):
        md.save_project(Project(projectnumber="I-1", kind="internal", department_id=profit))
    assert one(conn, "SELECT count(*) AS n FROM project WHERE projectnumber = 'I-1'")["n"] == 0   # alles of niets
    # intern project met een nieuwe kostenplaats
    pid, _ = _internal_project(conn, config)
    p = md.get_project(pid)
    dep = one(conn, "SELECT id, role FROM department WHERE description = 'Kantoor'")
    assert (p["kind"], p["department_id"], p["department"], dep["role"].strip()) == ("internal", dep["id"], "Kantoor", "C")
    names = [c["description"] for c in md.cost_centres()]
    assert "Kantoor" in names and "Verkoop" not in names
    # kostenplaats in gebruik: niet weg
    assert next(d for d in md.departments() if d["id"] == dep["id"])["used"] == {"project": 1}
    with pytest.raises(ValidationError, match="in gebruik"):
        md.delete_department(dep["id"])
    # opslaan zonder soort laat de soort staan (import, Perl-vergelijking); project weg = x_project weg
    md.save_project(Project(id=pid, projectnumber="INT-1", description="Intern 2"))
    assert md.get_project(pid)["kind"] == "internal"
    md.delete_project(pid)
    assert one(conn, "SELECT count(*) AS n FROM x_project")["n"] == 0
    md.delete_department(dep["id"])


def test_internal_rate_fixed_on_save(conn, config, setup):
    from sqlledger.timecards import Assignment, Assignments, InternalRates, save_month
    conn.execute("UPDATE parts SET lastcost = 40 WHERE id = %s", (setup["service"],))
    tc = TimeCards(conn, config)
    base = dict(project_id=setup["project"], parts_id=setup["service"], employee_id=setup["employee"],
                qty=Decimal(2), sellprice=Decimal(85))
    # 3. kostprijs van de dienst
    c1 = tc.save(TimeCard(transdate=date(2026, 3, 2), **base))
    assert (tc.get(c1).costprice, tc.get(c1).costsource) == (Decimal(40), "service")
    # 2. intern uurtarief van de medewerker, met geldig-vanaf
    rates = InternalRates(conn, config)
    rates.save(setup["employee"], date(2026, 3, 1), Decimal("50"))
    rid = rates.save(setup["employee"], date(2026, 4, 1), Decimal("55"))
    assert rates.save(setup["employee"], date(2026, 4, 1), Decimal("56")) == rid          # zelfde datum: overschrijven
    with pytest.raises(ValidationError, match="negatief"):
        rates.save(setup["employee"], date(2026, 5, 1), Decimal("-1"))
    assert rates.rate_on(setup["employee"], date(2026, 3, 31)) == Decimal(50)
    c2 = tc.save(TimeCard(transdate=date(2026, 3, 3), **base))
    c3 = tc.save(TimeCard(transdate=date(2026, 4, 3), **base))
    assert (tc.get(c2).costprice, tc.get(c2).costsource, tc.get(c3).costprice) == (Decimal(50), "employee", Decimal(56))
    # vast bij opslaan: een nieuw tarief verandert bestaande tijdkaarten niet, ook niet bij wijzigen van het aantal
    rates.save(setup["employee"], date(2026, 3, 1), Decimal("60"))
    card = tc.get(c2)
    card.qty = Decimal(3)
    tc.save(card)
    assert tc.get(c2).costprice == Decimal(50)
    # andere datum = opnieuw bepaald
    card = tc.get(c2)
    card.transdate = date(2026, 3, 4)
    tc.save(card)
    assert tc.get(c2).costprice == Decimal(60)
    # 1. intern tarief van de toewijzing; ook via de maandstaat
    Assignments(conn, config).save(Assignment(employee_id=setup["employee"], project_id=setup["project"],
                                              parts_id=setup["service"], validfrom=date(2026, 3, 1),
                                              internalrate=Decimal("70")))
    assert Assignments(conn, config).list()[0].internalrate == Decimal(70)
    save_month(conn, config, setup["employee"], 2026, 3, {(setup["project"], setup["service"], 10): Decimal(4)})
    cid = one(conn, "SELECT id FROM jcitems WHERE checkedin::date = '2026-03-10'")["id"]
    assert (tc.get(cid).costprice, tc.get(cid).costsource) == (Decimal(70), "assignment")
    assert tc.get(c1).costprice == Decimal(40)
    # verwijderen ruimt het tarief op
    tc.delete(c1)
    assert one(conn, "SELECT count(*) AS n FROM x_jcitem_cost WHERE jcitem_id = %s", c1)["n"] == 0
    rates.delete(rid)
    with pytest.raises(Exception):
        rates.delete(rid)


def test_internal_projects_never_to_sales_orders(conn, config, setup):
    from sqlledger.timecards import generate_sales_orders, month_sheet, open_items, Assignment, Assignments
    cust = one(conn, "SELECT min(id) AS id FROM customer")["id"]
    conn.execute("UPDATE project SET customer_id = %s WHERE id = %s", (cust, setup["project"]))
    pid, _ = _internal_project(conn, config)
    tc = TimeCards(conn, config)
    for project in (setup["project"], pid):
        tc.save(TimeCard(transdate=date(2026, 3, 2), project_id=project, parts_id=setup["service"],
                         employee_id=setup["employee"], qty=Decimal(2), sellprice=Decimal(85)))
    items = open_items(conn)
    assert [it.project_id for it in items] == [setup["project"]]
    # ook als het scherm het toch aanbiedt: geweigerd op de server
    from sqlledger.timecards import OpenItem
    bad = OpenItem(project_id=pid, projectnumber="INT-1", customer_id=None, customer="", customernumber="",
                   parts_id=setup["service"], partnumber="UUR", unit="", description="x", transdate=date(2026, 3, 2),
                   qty=Decimal(2), sellprice=Decimal(85), amount=Decimal(170), employee="", itemnotes="", cards=[])
    with pytest.raises(ValidationError, match="intern project"):
        generate_sales_orders(conn, config, items + [bad], {1: cust})
    assert one(conn, "SELECT count(*) AS n FROM oe")["n"] == 0
    assert len(generate_sales_orders(conn, config, items)) == 1
    # Mijn uren / maandstaat: label intern
    Assignments(conn, config).save(Assignment(employee_id=setup["employee"], project_id=pid,
                                              parts_id=setup["service"], validfrom=date(2026, 3, 1)))
    sheet = {r.project_id: r.internal for r in month_sheet(conn, config, setup["employee"], 2026, 3)}
    assert sheet == {setup["project"]: False, pid: True}
    assert {r["project_id"]: r["kind"] for r in tc.search()} == {setup["project"]: "customer", pid: "internal"}


def test_hours_by_project_report(conn, config, setup):
    from sqlledger.timecards import HoursSearch, InternalRates, hours_by_project
    cust = one(conn, "SELECT min(id) AS id FROM customer")["id"]
    conn.execute("UPDATE project SET customer_id = %s WHERE id = %s", (cust, setup["project"]))
    pid, md = _internal_project(conn, config)
    other = one(conn, "SELECT id FROM employee WHERE id <> %s ORDER BY id LIMIT 1", setup["employee"])
    InternalRates(conn, config).save(setup["employee"], date(2026, 1, 1), Decimal("50"))
    tc = TimeCards(conn, config)
    for project, qty, emp in ((setup["project"], "3", setup["employee"]), (setup["project"], "2", setup["employee"]),
                              (pid, "4", setup["employee"])):
        tc.save(TimeCard(transdate=date(2026, 3, 2), project_id=project, parts_id=setup["service"], employee_id=emp,
                         qty=Decimal(qty), sellprice=Decimal(85)))
    first = one(conn, "SELECT min(id) AS id FROM jcitems")["id"]
    conn.execute("UPDATE jcitems SET allocated = qty WHERE id = %s", (first,))
    # een tijdkaart zonder vastgelegd tarief (zoals uit SL): berekend
    conn.execute("""INSERT INTO jcitems (project_id, parts_id, qty, allocated, sellprice, fxsellprice, checkedin,
                                         checkedout, employee_id)
                    VALUES (%s, %s, 1, 0, 85, 85, '2026-03-05', '2026-03-05', %s)""",
                 (setup["project"], setup["service"], other["id"] if other else setup["employee"]))
    rep = hours_by_project(conn, HoursSearch(datefrom=date(2026, 3, 1), dateto=date(2026, 3, 31)))
    assert rep["computed"] == 1
    rows = {r.projectnumber: r for r in rep["rows"]}
    k, i = rows["P-1"], rows["INT-1"]
    assert (k.kind, k.hours, k.sales, k.ordered, k.open) == ("customer", Decimal(6), Decimal(510), Decimal(3), Decimal(3))
    assert k.cost == Decimal(250) + (Decimal(0) if other else Decimal(50)) and k.margin == k.sales - k.cost
    assert (i.kind, i.hours, i.cost, i.sales, i.margin, i.department) == ("internal", Decimal(4), Decimal(200), None,
                                                                        None, "Kantoor")
    assert i.rate == Decimal(50)
    # filters
    assert [r.projectnumber for r in hours_by_project(conn, HoursSearch(kind="internal"))["rows"]] == ["INT-1"]
    dep = md.get_project(pid)["department_id"]
    assert [r.projectnumber for r in hours_by_project(conn, HoursSearch(department_id=dep))["rows"]] == ["INT-1"]
    assert [r.projectnumber for r in hours_by_project(conn, HoursSearch(customer_id=cust))["rows"]] == ["P-1"]
    assert hours_by_project(conn, HoursSearch(dateto=date(2026, 3, 1)))["rows"] == []
    assert hours_by_project(conn, HoursSearch(kind="none"))["rows"] == []
    per = hours_by_project(conn, HoursSearch(project_id=setup["project"], per_employee=True))["rows"]
    assert sum((r.hours for r in per), Decimal(0)) == Decimal(6) and all(r.employee for r in per)
