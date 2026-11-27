"""Datumhulpfuncties (Form.pm datetonum, from_to, split_date, dayofmonth).

De bibliotheek werkt intern met ``datetime.date``. Strings in het
gebruikersformaat zijn alleen nodig voor invoer en voor nummersjablonen.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Any, Optional, Tuple, Union

DateLike = Union[date, str, None]


def separator(dateformat: str) -> str:
    """Scheidingsteken van een datumformaat ('dd-mm-yy' -> '-')."""
    spc = re.sub(r"\w", "", dateformat)
    return spc[:1]


def parse_date(value: DateLike, dateformat: str = "yyyy-mm-dd") -> Optional[date]:
    """Lees een datum in.

    Accepteert ``date``/``datetime``, ISO 'yyyy-mm-dd', 'yyyymmdd' en het
    gebruikersformaat (datetonum). Jaartallen van twee cijfers krijgen 2000+.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if re.fullmatch(r"\d{8}", s):
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    parts = re.split(r"\D", s)
    if len(parts) < 3:
        raise ValueError(f"ongeldige datum: {value!r}")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
        yy, mm, dd = parts[:3]
    elif dateformat.startswith("yy"):
        yy, mm, dd = parts[:3]
    elif dateformat.startswith("mm"):
        mm, dd, yy = parts[:3]
    else:
        dd, mm, yy = parts[:3]
    y = int(yy)
    if len(yy) == 2:
        y += 2000
    return date(y, int(mm), int(dd))


def format_date(d: date, dateformat: str = "yyyy-mm-dd") -> str:
    """Datum in gebruikersformaat met viercijferig jaar (Form::current_date)."""
    spc = separator(dateformat)
    yy, mm, dd = f"{d.year:04d}", f"{d.month:02d}", f"{d.day:02d}"
    if not spc:
        return f"{yy}{mm}{dd}"
    if dateformat.startswith("yy"):
        return spc.join((yy, mm, dd))
    if dateformat.startswith("mm"):
        return spc.join((mm, dd, yy))
    return spc.join((dd, mm, yy))


def split_date(dateformat: str, d: date) -> Tuple[str, str, str, str]:
    """(compact, yy, mm, dd) zoals Form::split_date; yy is tweecijferig."""
    yyyy, mm, dd = f"{d.year:04d}", f"{d.month:02d}", f"{d.day:02d}"
    if dateformat.startswith("yy"):
        rv = f"{yyyy}{mm}{dd}"
    elif dateformat.startswith("mm"):
        rv = f"{mm}{dd}{yyyy}"
    else:
        rv = f"{dd}{mm}{yyyy}"
    return rv, yyyy[-2:], mm, dd


def first_day_of_month(d: date) -> date:
    return d.replace(day=1)


def last_day_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def from_to(year: int, month: int, interval: Optional[int] = None,
            today: Optional[date] = None) -> Tuple[date, date]:
    """Periode vanaf de eerste van ``year``/``month`` (Form::from_to).

    interval None -> 1 maand, 0 -> t/m vandaag, 12 -> een jaar,
    n -> n maanden. De einddatum is de dag vóór het begin van de volgende
    periode.
    """
    start = date(int(year), int(month), 1)
    if interval is not None and int(interval) == 0:
        return start, today or date.today()
    n = 1 if interval is None else int(interval)
    y, m = start.year, start.month + n
    while m > 12:
        m -= 12
        y += 1
    nxt = date(y, m, 1)
    return start, date.fromordinal(nxt.toordinal() - 1)


def add_date(d: date, repeat: int, unit: str) -> date:
    """Datumrekenen zoals Form::add_date.

    'days'/'weeks' tellen dagen op. 'months' telt (zoals Perl) het aantal
    dagen van de opeenvolgende maanden op, waardoor een maandeinde een
    maandeinde blijft (31-03 min 1 maand = 28-02). 'year' verschuift het jaar;
    28/29 februari wordt naar het einde van februari in het doeljaar gezet.
    Schrikkeljaar zoals Perl: jaartal deelbaar door 4.
    """
    from datetime import timedelta

    repeat = int(repeat)
    unit = unit.lower()
    if "day" in unit:
        return d + timedelta(days=repeat)
    if "week" in unit:
        return d + timedelta(days=7 * repeat)
    if "month" in unit:
        days = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        if d.year % 4:
            days[1] = 28
        mm, dd = d.month, d.day
        if repeat < 0:
            cut = mm - 1 if dd < days[mm - 1] else mm
        else:
            cut = mm - 1
        days = days[cut:] + days[:cut]
        if repeat < 0:
            days.reverse()
        diff = sum(days[i] for i in range(abs(repeat)))
        return d + timedelta(days=-diff if repeat < 0 else diff)
    if "year" in unit:
        y = d.year + repeat
        dd = d.day
        if d.month == 2:
            if not y % 4 and dd == 28:
                dd = 29
            if y % 4 and dd == 29:
                dd = 28
        return date(y, d.month, dd)
    raise ValueError(f"onbekende eenheid: {unit!r}")


# ------------------------------------------------------------------ boekjaar (JA1–JA4)

def yearend_month(value: Any) -> int:
    """Laatste maand van het boekjaar uit ``defaults.yearend`` (JA1): 1–12, leeg of ongeldig = 12.

    Oude SQL-Ledger-waarden als ``'1/31'`` (maand/dag, Pg-upgrade-1.2.7) geven de maand.
    """
    m = re.match(r"\s*(\d{1,2})", str(value or ""))
    n = int(m.group(1)) if m else 12
    return n if 1 <= n <= 12 else 12


def fiscal_start_year(d: date, endmonth: int = 12) -> int:
    """Beginjaar van het boekjaar waar ``d`` in valt (JA4: de waarde van een boekjaar is het beginjaar)."""
    if endmonth == 12:
        return d.year
    return d.year if d.month > endmonth else d.year - 1


def fiscal_dates(start_year: int, endmonth: int = 12) -> Tuple[date, date]:
    """Eerste en laatste dag van het boekjaar dat begint in ``start_year``."""
    if endmonth == 12:
        return date(start_year, 1, 1), date(start_year, 12, 31)
    end = date(start_year + 1, endmonth, calendar.monthrange(start_year + 1, endmonth)[1])
    return date(start_year, endmonth + 1, 1), end


def fiscal_year(d: date, endmonth: int = 12) -> Tuple[date, date]:
    """Eerste en laatste dag van het boekjaar waar ``d`` in valt."""
    return fiscal_dates(fiscal_start_year(d, endmonth), endmonth)


def fiscal_label(start_year: int, endmonth: int = 12) -> str:
    """Naam van een boekjaar (JA4): kalenderboekjaar ``2026``, gebroken boekjaar ``2025/2026``."""
    return str(start_year) if endmonth == 12 else f"{start_year}/{start_year + 1}"


def fiscal_label_for(fromdate: Optional[date], todate: Optional[date]) -> Optional[str]:
    """Naam als ``fromdate``–``todate`` precies één (kalender- of gebroken) boekjaar is, anders ``None``."""
    if not fromdate or not todate or fromdate.day != 1:
        return None
    endmonth = 12 if fromdate.month == 1 else fromdate.month - 1
    if fiscal_dates(fromdate.year, endmonth) != (fromdate, todate):
        return None
    return fiscal_label(fromdate.year, endmonth)
