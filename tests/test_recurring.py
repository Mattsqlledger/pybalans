"""Terugkerende boekingen (sqlledger.recurring): schema, lijst, verwerken."""

import threading
from datetime import date
from decimal import Decimal as D

import psycopg
import pytest

from sqlledger import Recurring, ValidationError
from sqlledger.recurring import (ProcessItem, RecurringTransactions, add_interval,
                                 end_date, next_after, term)


@pytest.fixture
def rt(conn, config):
    return RecurringTransactions(conn, config)


def sched(start, repeat=1, unit="months", howmany=4, **kw):
    return Recurring(startdate=start, repeat=repeat, unit=unit, howmany=howmany, **kw)


def rec(conn, tid):
    return conn.execute("SELECT nextdate, enddate FROM recurring WHERE id = %s", (tid,)).fetchone()


def balances(conn, tid):
    rows = conn.execute("""SELECT c.accno, round(sum(ac.amount)::numeric, 2) FROM acc_trans ac
                           JOIN chart c ON c.id = ac.chart_id WHERE ac.trans_id = %s
                           GROUP BY c.accno ORDER BY 1""", (tid,)).fetchall()
    return {a: s for a, s in rows}


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --- datums -----------------------------------------------------------------------------

def test_month_end_stays_month_end():
    s = sched(date(2027, 1, 31), howmany=5)
    assert [term(s, i) for i in range(5)] == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31),
                                              date(2027, 4, 30), date(2027, 5, 31)]
    leap = sched(date(2028, 1, 31))
    assert term(leap, 1) == date(2028, 2, 29) and term(leap, 2) == date(2028, 3, 31)
    assert end_date(leap) == date(2028, 4, 30)
    # 30 april blijft de 30e (geen 'maandeinde'-regel, gewoon afkappen vanaf de start)
    assert term(sched(date(2027, 4, 30)), 1) == date(2027, 5, 30)


def test_years_weeks_days():
    y = sched(date(2028, 2, 29), unit="years", howmany=5)
    assert [term(y, i) for i in range(5)] == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28),
                                              date(2031, 2, 28), date(2032, 2, 29)]
    w = sched(date(2026, 12, 28), repeat=2, unit="weeks", howmany=3)
    assert [term(w, i) for i in range(3)] == [date(2026, 12, 28), date(2027, 1, 11), date(2027, 1, 25)]
    d = sched(date(2027, 2, 27), repeat=3, unit="days", howmany=3)
    assert end_date(d) == date(2027, 3, 5)
    assert add_interval(date(2027, 11, 30), 3, "months") == date(2028, 2, 29)
    assert next_after(sched(date(2027, 1, 31)), date(2027, 3, 1)) == date(2027, 3, 31)
    assert next_after(sched(date(2027, 1, 31)), date(2027, 4, 30)) is None


# --- schema -----------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    dict(start=None), dict(repeat=0), dict(howmany=0), dict(howmany=-2), dict(unit="fortnights"),
])
def test_validation(rt, conn, bad):
    kw = dict(start=date(2027, 1, 15), repeat=1, unit="months", howmany=3)
    kw.update(bad)
    with pytest.raises(ValidationError):
        rt.save(20101, sched(kw.pop("start"), **kw))
    assert count(conn, "recurring") == 0


def test_save_nextdate_and_delete(rt, conn):
    # start = boekdatum: de boeking zelf is de eerste termijn
    s = rt.save(20101, sched(date(2026, 1, 31), howmany=4, reference="R", description="Omzet"))
    assert (s.nextdate, s.enddate) == (date(2026, 2, 28), date(2026, 4, 30))
    # start in de toekomst: de start
    s = rt.save(20101, sched(date(2027, 1, 15), howmany=4, print=[("transaction", "pdf", "")]))
    assert s.nextdate == date(2027, 1, 15) and s.print == [("transaction", "pdf", "")]
    # opnieuw opslaan zonder afdrukinstellingen: die blijven staan (fase 4), opgegeven volgende datum telt
    s = rt.save(20101, sched(date(2027, 1, 15), howmany=4, nextdate=date(2027, 3, 1)))
    assert s.nextdate == date(2027, 3, 1) and s.print == [("transaction", "pdf", "")]
    # volgende datum na het einde: afgelopen
    s = rt.save(20101, sched(date(2027, 1, 15), howmany=2, nextdate=date(2027, 6, 1)))
    assert s.nextdate is None
    trail = conn.execute("SELECT action FROM audittrail WHERE trans_id = 20101 AND formname = 'recurring'").fetchall()
    assert [a for a, in trail] == ["saved"] * 4
    rt.delete(20101)
    assert rt.get(20101) is None and count(conn, "recurringprint") == 0
    assert conn.execute("SELECT count(*) FROM audittrail WHERE action = 'deleted' AND formname = 'recurring'"
                        ).fetchone()[0] == 1


# --- verwerken: memoriaal --------------------------------------------------------------

def test_process_gl_month_end_three_terms(rt, conn):
    rt.save(20101, sched(date(2027, 1, 31), howmany=4, description="Omzet maand"))
    booked = []
    for _ in range(4):
        (r,) = rt.process([20101])
        assert r.ok, r.reason
        booked.append(r)
    assert [r.transdate for r in booked] == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31),
                                            date(2027, 4, 30)]
    assert [r.nextdate for r in booked] == [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), None]
    assert [r.number for r in booked] == ["GL-0100", "GL-0101", "GL-0102", "GL-0103"]
    orig = balances(conn, 20101)
    for r in booked:
        assert balances(conn, r.trans_id) == orig
        desc, td = conn.execute("SELECT description, transdate FROM gl WHERE id = %s", (r.trans_id,)).fetchone()
        assert desc == "OMZ-2026-01" and td == r.transdate        # T5: omschrijving van de boeking
    (r,) = rt.process([20101])
    assert not r.ok and r.reason == "Schedule has ended"
    actions = conn.execute("SELECT count(*) FROM audittrail WHERE tablename = 'gl' AND action = 'posted' "
                           "AND trans_id = ANY(%s)", ([r.trans_id for r in booked],)).fetchone()[0]
    assert actions == 4


def test_leap_year_and_years(rt, conn):
    rt.save(20101, sched(date(2028, 2, 29), unit="years", howmany=3))
    dates = [rt.process([20101])[0].transdate for _ in range(3)]
    assert dates == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28)]
    assert rec(conn, 20101)[0] is None


def test_weeks_and_days(rt, conn):
    rt.save(20101, sched(date(2027, 1, 4), repeat=2, unit="weeks", howmany=3))
    assert [rt.process([20101])[0].transdate for _ in range(2)] == [date(2027, 1, 4), date(2027, 1, 18)]
    assert rec(conn, 20101)[0] == date(2027, 2, 1)
    rt.save(5001, sched(date(2027, 1, 30), repeat=3, unit="days", howmany=2))
    assert rt.process([5001])[0].transdate == date(2027, 1, 30)
    assert rec(conn, 5001)[0] == date(2027, 2, 2)


def test_reference_used_once_then_next_number(rt, conn):
    rt.save(20101, sched(date(2027, 1, 15), reference="HUUR-1"))
    numbers = [rt.process([20101])[0].number for _ in range(2)]
    assert numbers == ["HUUR-1", "GL-0100"]


def test_edited_date_does_not_shift_schedule(rt, conn):
    rt.save(20101, sched(date(2027, 1, 31), howmany=6))
    (r,) = rt.process([ProcessItem(20101, date(2027, 1, 31), transdate=date(2027, 2, 3))])
    assert r.ok and r.transdate == date(2027, 2, 3) and r.nextdate == date(2027, 2, 28)
    # vooruitzetten slaat de termijnen daartussen over
    (r,) = rt.process([ProcessItem(20101, date(2027, 2, 28), transdate=date(2027, 4, 15))])
    assert r.nextdate == date(2027, 4, 30)


def test_double_post_books_once(rt, conn):
    rt.save(20101, sched(date(2027, 1, 15)))
    item = ProcessItem(20101, expected_nextdate=date(2027, 1, 15))
    assert rt.process([item])[0].ok
    n = count(conn, "gl")
    (r,) = rt.process([item])                      # tweede POST van hetzelfde scherm
    assert not r.ok and "changed" in r.reason and count(conn, "gl") == n


def test_concurrent_connections_book_once(rt, conn, config):
    rt.save(20101, sched(date(2027, 1, 15)))
    dsn = conn.info.dsn
    before = count(conn, "gl")
    barrier = threading.Barrier(2)
    results = []

    def run():
        with psycopg.connect(dsn, autocommit=True) as c:
            barrier.wait()
            results.extend(RecurringTransactions(c, config).process([20101]))
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.ok for r in results) == [False, True]
    assert count(conn, "gl") == before + 1
    assert rec(conn, 20101)[0] == date(2027, 2, 15)


def test_lock_waits_and_sees_change(rt, conn, config):
    """Deterministisch: een andere verbinding houdt het schema vast en verwerkt het;
    deze aanroep wacht op de lock en ziet daarna dat de datum veranderd is."""
    rt.save(20101, sched(date(2027, 1, 15)))
    other = psycopg.connect(conn.info.dsn)          # niet-autocommit: transactie blijft open
    other.execute("SELECT * FROM recurring WHERE id = 20101 FOR UPDATE")
    out = []
    t = threading.Thread(target=lambda: out.extend(
        rt.process([ProcessItem(20101, expected_nextdate=date(2027, 1, 15))])))
    t.start()
    t.join(0.5)
    assert t.is_alive()                            # wacht op de lock
    other.execute("UPDATE recurring SET nextdate = '2027-02-15' WHERE id = 20101")
    other.commit()
    other.close()
    t.join(10)
    assert not out[0].ok and "changed" in out[0].reason


def test_closed_period_rolls_back(rt, conn):
    rt.save(20101, sched(date(2025, 6, 15), nextdate=date(2025, 6, 15)))
    n = count(conn, "gl")
    (r,) = rt.process([20101])
    assert not r.ok and "closed period" in r.reason
    assert count(conn, "gl") == n and rec(conn, 20101)[0] == date(2025, 6, 15)


# --- verwerken: verkoop / inkoop -----------------------------------------------------

def test_foreign_currency_needs_rate(rt, conn):
    rt.save(3003, sched(date(2027, 1, 20)))
    n = count(conn, "ar")
    (r,) = rt.process([3003])
    assert not r.ok and r.reason == "Exchange rate missing for USD on 2027-01-20"
    assert count(conn, "ar") == n and rec(conn, 3003)[0] == date(2027, 1, 20)
    conn.execute("INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2027-01-20', 1.2)")
    (r,) = rt.process([3003])
    assert r.ok
    amount, rate, curr = conn.execute("SELECT amount, exchangerate, curr FROM ar WHERE id = %s",
                                      (r.trans_id,)).fetchone()
    assert (D(str(amount)), D(str(rate)), curr) == (D(600), D("1.2"), "USD")


def test_ap_duedate_and_payment(rt, conn):
    rt.save(4002, sched(date(2027, 1, 10), payment=True))
    (r,) = rt.process([4002])
    assert r.ok
    row = conn.execute("SELECT transdate, duedate, paid, datepaid, amount, description FROM ap WHERE id = %s",
                       (r.trans_id,)).fetchone()
    # vervaldatum: zelfde 30 dagen als het origineel (Perl: boekdatum); betaling 35 dagen later, niet afgeletterd
    assert row[:4] == (date(2027, 1, 10), date(2027, 2, 9), 300, date(2027, 2, 14))
    pay = conn.execute("""SELECT ac.amount, ac.source, ac.cleared FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                          WHERE ac.trans_id = %s AND c.accno = '1100'""", (r.trans_id,)).fetchone()
    assert pay == (300, "BANK-402", None)
    assert balances(conn, r.trans_id) == balances(conn, 4002)


def test_ar_without_payment(rt, conn):
    rt.save(3006, sched(date(2027, 1, 1)))
    (r,) = rt.process([3006])
    paid, duedate, invnumber = conn.execute("SELECT paid, duedate, invnumber FROM ar WHERE id = %s",
                                            (r.trans_id,)).fetchone()
    assert paid == 0 and duedate == date(2027, 1, 15) and invnumber == r.number
    assert balances(conn, r.trans_id) == {"1200": D("-363.00"), "1810": D("63.00"), "8530": D("300.00")}


def test_ap_reference_per_vendor(rt, conn):
    rt.save(4002, sched(date(2027, 1, 10), reference="I-4003"))     # bestaat bij leverancier 901, niet 905
    assert rt.process([4002])[0].number == "I-4003"
    assert rt.process([4002])[0].number != "I-4003"


# --- lijst ------------------------------------------------------------------------------

def test_list(rt, conn):
    rt.save(20101, sched(date(2026, 9, 30), howmany=3, reference="R1"))     # volgende 30-09: te laat
    rt.save(3006, sched(date(2027, 1, 1), howmany=2))                       # toekomst
    rt.save(4002, sched(date(2026, 1, 10), howmany=2, nextdate=date(2026, 3, 1)))
    conn.execute("UPDATE recurring SET nextdate = NULL WHERE id = 4002")    # afgelopen
    rt.save(3001, sched(date(2026, 6, 25), howmany=2))                      # factuur met artikelen
    conn.execute("INSERT INTO oe (id, ordnumber, transdate, customer_id, amount, curr, quotation) "
                 "VALUES (77001, 'SO-1', '2026-05-01', 900, 10, 'EUR', false)")
    rt.save(77001, sched(date(2026, 5, 1), howmany=3))
    rows = {r.id: r for r in rt.list(today=date(2026, 10, 8))}
    assert set(rows) == {20101, 3006, 4002, 3001, 77001}
    g = rows[20101]
    assert (g.module, g.reference, g.amount, g.currency, g.overdue, g.due) == ("gl", "R1", D(4000), "EUR", -8, True)
    a = rows[3006]
    assert (a.name, a.amount, a.due, a.processable, a.number) == ("Klant A", D(363), False, True, "F-3006")
    assert rows[4002].expired and not rows[4002].processable
    assert rows[3001].invoice and rows[3001].reason is None and rows[3001].processable
    assert rows[77001].transaction == "so" and rows[77001].reason is None
    assert [r.id for r in rt.list(today=date(2026, 10, 8))] == [4002, 3001, 3006, 20101, 77001]  # per soort


# --- mailen en PDF na verwerken (fase 4, SB12–SB13) ------------------------------------------

def test_outputs_saved_like_sl_and_passed_on(rt, conn):
    s = sched(date(2027, 1, 15), howmany=3, email=[("transaction", "pdf")], print=[("transaction", "pdf", "")],
              message="Hierbij {nummer}")
    rt.save(3006, s)
    e = conn.execute("SELECT formname, format, message FROM recurringemail WHERE id = 3006").fetchall()
    assert [tuple(x) for x in e] == [("transaction", "pdf", "Hierbij {nummer}")]
    # zonder keuzes: standaard blijven ze staan (oude aanroepen), met keep_output=False worden ze gewist
    rt.save(3006, sched(date(2027, 1, 15), howmany=3))
    assert rt.get(3006).email == [("transaction", "pdf")]
    res = rt.process([ProcessItem(3006, transdate=date(2027, 1, 15))], today=date(2027, 1, 20))[0]
    assert res.ok and res.email == ["transaction"] and res.print == ["transaction"]
    assert res.message == "Hierbij {nummer}"
    rt.save(3006, sched(date(2027, 1, 15), howmany=3), keep_output=False)
    assert rt.get(3006).email == [] and rt.get(3006).print == []


def test_failed_process_passes_no_outputs(rt, conn):
    rt.save(3003, sched(date(2027, 3, 15), howmany=2, email=[("transaction", "pdf")]))   # USD zonder koers
    res = rt.process([3003])[0]
    assert not res.ok and res.email == [] and res.print == []


def test_invalid_formname(rt):
    with pytest.raises(ValidationError):
        rt.save(3006, sched(date(2027, 1, 15), email=[("x; drop", "pdf")]))


def test_terms_due():
    """v1.0.1 PB16: termijnen vanaf de volgende datum t/m vandaag."""
    from datetime import date
    from sqlledger.recurring import terms_due
    d = date
    # een herhaalschema: volgende = einddatum 1-11-2015, maar sindsdien 126 maanden stil
    assert terms_due(d(2013, 12, 1), 1, "months", 24, d(2015, 11, 1), d(2015, 11, 1), d(2026, 4, 16)) == 126
    assert terms_due(d(2026, 1, 1), 1, "months", 24, d(2026, 4, 1), None, d(2026, 4, 16)) == 1
    assert terms_due(d(2026, 1, 1), 1, "months", 24, d(2026, 5, 1), None, d(2026, 4, 16)) == 0
    assert terms_due(d(2026, 1, 1), 1, "months", 24, d(2026, 2, 1), None, d(2026, 4, 16)) == 3
    assert terms_due(d(2026, 1, 1), 1, "months", 24, d(2026, 2, 5), None, d(2026, 4, 16)) == 3   # aangepaste datum
    assert terms_due(None, 1, "months", 24, d(2026, 2, 1), None, d(2026, 4, 16)) == 0
