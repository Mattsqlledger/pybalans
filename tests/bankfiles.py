"""Testafschriften maken (CAMT.053 en MT940) en geldige IBAN's/kenmerken uitrekenen.

Gebruikt door tests/test_bankimport.py, tests/web/test_web_bank.py en
compare/bank_realdata.py. Alle data is verzonnen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional
from xml.sax.saxutils import escape


def _mod97(s: str) -> int:
    return int("".join(str(int(c, 36)) for c in s)) % 97


def make_iban(country: str, bban: str) -> str:
    check = 98 - _mod97(bban + country + "00")
    return f"{country}{check:02d}{bban}"


def nl_reference(body15: str) -> str:
    """Geldig NL-betalingskenmerk (16 cijfers) bij 15 cijfers."""
    weights = (2, 4, 8, 5, 10, 9, 7, 3, 6, 1)
    total = sum(int(d) * weights[i % 10] for i, d in enumerate(reversed(body15)))
    check = 11 - total % 11
    check = {10: 1, 11: 0}.get(check, check)
    return f"{check}{body15}"


def rf_reference(body: str) -> str:
    check = 98 - _mod97(body + "RF00")
    return f"RF{check:02d}{body}"


@dataclass
class Entry:
    amount: Decimal                 # + bij, - af
    bookdate: date
    name: Optional[str] = None
    iban: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None  # gestructureerd kenmerk
    e2e: Optional[str] = None
    bank_ref: Optional[str] = None
    reversal: bool = False


def _x(s) -> str:
    return escape(str(s))


def camt(account: str, entries: List[Entry], opening: Decimal, *, ccy: str = "EUR", stmt_id: str = "STMT-1",
         seq: str = "1", opening_date: Optional[date] = None, closing: Optional[Decimal] = None,
         version: str = "02") -> bytes:
    opening = Decimal(opening)
    if closing is None:
        closing = opening + sum((Decimal(e.amount) for e in entries), Decimal(0))
    first = min((e.bookdate for e in entries), default=opening_date or date(2026, 1, 1))
    last = max((e.bookdate for e in entries), default=first)
    od = opening_date or first

    def bal(code, amt, d):
        ind = "CRDT" if amt >= 0 else "DBIT"
        return (f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp><Amt Ccy=\"{ccy}\">{abs(amt):.2f}</Amt>"
                f"<CdtDbtInd>{ind}</CdtDbtInd><Dt><Dt>{d.isoformat()}</Dt></Dt></Bal>")

    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<Document xmlns="urn:iso:std:iso:20022:tech:xsd:'
           f'camt.053.001.{version}"><BkToCstmrStmt><GrpHdr><MsgId>M-{_x(stmt_id)}</MsgId>'
           f'<CreDtTm>{last.isoformat()}T06:00:00</CreDtTm></GrpHdr><Stmt><Id>{_x(stmt_id)}</Id>'
           f'<ElctrncSeqNb>{_x(seq)}</ElctrncSeqNb><CreDtTm>{last.isoformat()}T06:00:00</CreDtTm>'
           f'<Acct><Id><IBAN>{account}</IBAN></Id><Ccy>{ccy}</Ccy></Acct>',
           bal("OPBD", opening, od), bal("CLBD", closing, last)]
    for i, e in enumerate(entries, 1):
        amt = Decimal(e.amount)
        credit = amt > 0
        ind = "CRDT" if credit else "DBIT"
        party, acct = ("Dbtr", "DbtrAcct") if credit != e.reversal else ("Cdtr", "CdtrAcct")
        rltd = ""
        if e.name or e.iban:
            rltd = "<RltdPties>"
            if e.name:
                rltd += f"<{party}><Nm>{_x(e.name)}</Nm></{party}>"
            if e.iban:
                rltd += f"<{acct}><Id><IBAN>{e.iban}</IBAN></Id></{acct}>"
            rltd += "</RltdPties>"
        rmt = ""
        if e.description or e.reference:
            rmt = "<RmtInf>"
            if e.description:
                rmt += f"<Ustrd>{_x(e.description)}</Ustrd>"
            if e.reference:
                rmt += f"<Strd><CdtrRefInf><Ref>{_x(e.reference)}</Ref></CdtrRefInf></Strd>"
            rmt += "</RmtInf>"
        refs = f"<Refs><EndToEndId>{_x(e.e2e) if e.e2e else 'NOTPROVIDED'}</EndToEndId></Refs>"
        out.append(f'<Ntry><Amt Ccy="{ccy}">{abs(amt):.2f}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
                   + ("<RvslInd>true</RvslInd>" if e.reversal else "")
                   + f"<Sts>BOOK</Sts><BookgDt><Dt>{e.bookdate.isoformat()}</Dt></BookgDt>"
                   f"<ValDt><Dt>{e.bookdate.isoformat()}</Dt></ValDt>"
                   f"<AcctSvcrRef>{_x(e.bank_ref or f'{stmt_id}-{i}')}</AcctSvcrRef>"
                   f"<NtryDtls><TxDtls>{refs}{rltd}{rmt}</TxDtls></NtryDtls></Ntry>")
    out.append("</Stmt></BkToCstmrStmt></Document>")
    return "".join(out).encode("utf-8")


def mt940(account: str, entries: List[Entry], opening: Decimal, *, ccy: str = "EUR", stmt_id: str = "P1",
          closing: Optional[Decimal] = None) -> bytes:
    """MT940 in ING-opmaak met gestructureerde :86:-velden."""
    opening = Decimal(opening)
    if closing is None:
        closing = opening + sum((Decimal(e.amount) for e in entries), Decimal(0))
    first = min((e.bookdate for e in entries), default=date(2026, 1, 1))
    last = max((e.bookdate for e in entries), default=first)

    def amt(v):
        return f"{abs(v):.2f}".replace(".", ",")

    def d6(d):
        return d.strftime("%y%m%d")

    lines = ["{1:F01INGBNL2ABXXX0000000000}{2:I940INGBNL2AXN}{4:", f":20:{stmt_id}", f":25:{account}{ccy}",
             ":28C:00001", f":60F:{'C' if opening >= 0 else 'D'}{d6(first)}{ccy}{amt(opening)}"]
    for i, e in enumerate(entries, 1):
        a = Decimal(e.amount)
        mark = ("RD" if a > 0 else "RC") if e.reversal else ("C" if a > 0 else "D")
        lines.append(f":61:{d6(e.bookdate)}{e.bookdate.strftime('%m%d')}{mark}{amt(a)}NTRFEREF//"
                     f"{e.bank_ref or f'{stmt_id}{i:04d}'}")
        info = f"/EREF/{e.e2e or 'NOTPROVIDED'}/"
        if e.iban or e.name:
            info += f"/CNTP/{e.iban or ''}//{e.name or ''}///"
        if e.reference:
            info += f"/REMI/STRD/CUR/{e.reference}/"
        elif e.description:
            info += f"/REMI/USTD//{e.description}/"
        lines.append(":86:" + info)
    lines += [f":62F:{'C' if closing >= 0 else 'D'}{d6(last)}{ccy}{amt(closing)}", "-}"]
    return "\r\n".join(lines).encode("latin-1")
