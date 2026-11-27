"""Databasetests voor gedrag dat de Perl-vergelijking niet dekt."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger import (ClosedPeriodError, GLTransaction, JournalLine, LockedTransactionError,
                       NotFoundError, OutOfBalanceError, Recurring, TransactionSearch,
                       UserConfig, GeneralLedger, ValidationError)


def simple(**kw):
    base = dict(transdate=date(2026, 3, 2), description="Kasopname",
                lines=[JournalLine("1001", debit=D("50")), JournalLine("1100", credit=D("50"))])
    base.update(kw)
    return GLTransaction(**base)


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_post_and_read_back(gl):
    tid = gl.post_transaction(simple())
    view = gl.get_transaction(tid)
    assert view.header["reference"] == "GL-0100"
    assert {(ln["accno"], ln["amount"]) for ln in view.lines} == {("1001", D("-50")), ("1100", D("50"))}
    assert not view.locked


def test_rollback_on_error_midway(conn, gl):
    """Een fout halverwege (ongeldige herhaal-eenheid) mag niets achterlaten,
    ook geen opgehoogd boekingsnummer."""
    before = {t: count(conn, t) for t in ("gl", "acc_trans", "audittrail")}
    with pytest.raises(ValueError):
        gl.post_transaction(simple(recurring=Recurring(date(2026, 1, 1), 1, "fortnights", 2)))
    assert {t: count(conn, t) for t in before} == before
    assert conn.execute("SELECT fldvalue FROM defaults WHERE fldname='glnumber'").fetchone()[0] == "GL-0099"


def test_runs_inside_callers_transaction(conn, gl):
    before = count(conn, "gl")
    with pytest.raises(RuntimeError):
        with conn.transaction():
            gl.post_transaction(simple())
            raise RuntimeError("caller aborts")
    assert count(conn, "gl") == before


@pytest.mark.parametrize("trans,exc", [
    (simple(lines=[JournalLine("1001", debit=D(50)), JournalLine("1100", credit=D("49.99"))]), OutOfBalanceError),
    (simple(transdate=date(2025, 12, 31)), ClosedPeriodError),
    (simple(lines=[JournalLine("1000", debit=D(5)), JournalLine("1100", credit=D(5))]), ValidationError),
    (simple(lines=[JournalLine("1001", debit=D(-5)), JournalLine("1100", debit=D(5))]), ValidationError),
    (simple(lines=[]), ValidationError),
])
def test_validation(gl, conn, trans, exc):
    before = count(conn, "gl")
    with pytest.raises(exc):
        gl.post_transaction(trans)
    assert count(conn, "gl") == before


def test_cannot_move_closed_transaction_to_open_period(conn, gl):
    tid = gl.post_transaction(simple())
    conn.execute("UPDATE defaults SET fldvalue='2026-03-31' WHERE fldname='closedto'")
    with pytest.raises(LockedTransactionError):
        gl.post_transaction(simple(id=tid, transdate=date(2026, 4, 1)))
    with pytest.raises(ClosedPeriodError):
        gl.delete_transaction(tid)
    assert gl.get_transaction(tid).locked


def test_delete_missing(gl):
    with pytest.raises(NotFoundError):
        gl.delete_transaction(123456)


def test_lock_by_other_user(conn):
    gl_admin = GeneralLedger(conn, UserConfig(login="admin"))
    gl_jan = GeneralLedger(conn, UserConfig(login="jan"))
    tid = gl_admin.post_transaction(simple())
    assert not gl_admin.get_transaction(tid, lock=True).readonly
    view = gl_jan.get_transaction(tid, lock=True)
    assert view.readonly and view.haslock == "Beheerder"
    gl_admin.unlock()
    assert not gl_jan.get_transaction(tid, lock=True).readonly


def test_foreign_currency_fx_rows_hidden(conn, gl):
    tid = gl.post_transaction(simple(currency="USD", exchangerate=D("1.1"),
                                     lines=[JournalLine("4100", debit=D("100")),
                                            JournalLine("1100", credit=D("100"))]))
    rows = conn.execute("SELECT amount, fx_transaction FROM acc_trans WHERE trans_id=%s ORDER BY amount",
                        (tid,)).fetchall()
    assert rows == [(-100.0, False), (-10.0, True), (10.0, True), (100.0, False)]
    view = gl.get_transaction(tid)
    assert len(view.lines) == 2 and not view.fxadj
    assert conn.execute("SELECT exchangerate FROM exchangerate WHERE curr='USD' AND transdate='2026-03-02'"
                        ).fetchone()[0] == 1.1


def test_list_sort_is_injection_safe(gl):
    before = len(gl.list_transactions(TransactionSearch(description="kasopname")).rows)
    gl.post_transaction(simple())
    out = gl.list_transactions(TransactionSearch(sort="id; DROP TABLE gl", description="kasopname"))
    assert len(out.rows) == before + 2


def test_account_balances(gl, ca):
    def saldo(accno):
        return {a["accno"]: a for a in ca.all_accounts()}[accno]["amount"] or D(0)
    b1001, b1100 = saldo("1001"), saldo("1100")
    gl.post_transaction(simple())
    assert saldo("1001") - b1001 == D(-50)      # debet
    assert saldo("1100") - b1100 == D(50)       # credit
