"""Doorlopende nummers uit ``defaults`` (Form::update_defaults).

Een veld als ``glnumber`` bevat het laatst uitgegeven nummer, eventueel met
sjablonen, bijvoorbeeld ``GL<%YYYY%>-0042``. Het eerste getal buiten de
sjablonen wordt opgehoogd (voorloopnullen blijven behouden). Dat ophogen
wordt opgeslagen en de sjablonen worden in de teruggegeven waarde ingevuld.

Afwijking van Perl: Perl doet halverwege een ``COMMIT`` als het veld nog niet
bestaat, waardoor een half geboekte transactie zichtbaar kan worden. Hier
blijft alles binnen de lopende transactie.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Mapping, Optional

import psycopg

from .dates import first_day_of_month, format_date, last_day_of_month, split_date

_TEMPLATE = re.compile(r"<%.*?%>")


def increment(value: str) -> str:
    """Hoog het eerste getal buiten de sjablonen op, zoals Perl dat doet."""
    stripped = re.sub(r".*?<%.*?%>", "", value)
    m = re.search(r"\d+", stripped)
    if not m:
        return value
    num = m.group(0)
    inc = str(int(num) + 1)
    if num.startswith("0"):
        inc = "0" * max(len(num) - len(inc), 0) + inc
    return value.replace(num, inc, 1)


def expand(template: str, *, dateformat: str, transdate: Optional[date],
           currency: Optional[str] = None,
           fields: Optional[Mapping[str, Optional[str]]] = None) -> str:
    """Vul de ``<%...%>``-sjablonen in."""
    fields = {k.lower(): v for k, v in (fields or {}).items()}
    tdate = transdate or date.today()
    var = template
    for param in _TEMPLATE.findall(template):
        inner = re.sub(r"[<>%]", "", param)
        low = param.lower()
        replacement: Optional[str] = None

        if low == "<%date%>":
            replacement = split_date(dateformat, tdate)[0]

        m = re.match(r"<%(name|business|description|item|partsgroup|phone|custom)", param, re.I)
        if m:
            fld = m.group(1).lower()
            p = inner.split(" ")
            value = fields.get(fld) or ""
            if len(p) > 1:
                n = value.upper().split(" ")
                replacement = "".join(
                    (n[i - 1] if i - 1 < len(n) else "")[: int(p[i]) if p[i].isdigit() else 0]
                    for i in range(1, len(p))
                )
            else:
                replacement = value.split("--")[0]
            var = _replace_first(var, param, replacement, ignore_case=False)
            if fld == "phone":
                var = re.sub(r"\W", "", var)
            continue

        if re.match(r"<%(yy|mm|dd)", param, re.I):
            spc = re.sub(r"\w", "", inner)[:1]
            _, yy, mm, dd = split_date(dateformat, tdate)
            year = str(int(yy) + 2000) if re.search("yyyy", inner, re.I) else yy
            parts = {"dd": dd, "mm": mm, "yy": year}
            # Perl loopt over 'sort keys' => altijd volgorde dd, mm, yy
            replacement = spc.join(parts[k] for k in sorted(parts) if re.search(k, inner, re.I))

        m = re.match(r"<%(fdm|ldm)%>", param, re.I)
        if m:
            d = first_day_of_month(tdate) if m.group(1).lower() == "fdm" else last_day_of_month(tdate)
            replacement = format_date(d, dateformat)

        if re.match(r"<%curr", param, re.I):
            replacement = currency or ""

        if replacement is not None:
            var = _replace_first(var, param, replacement, ignore_case=True)
    return var


def _replace_first(text: str, old: str, new: str, ignore_case: bool) -> str:
    flags = re.I if ignore_case else 0
    return re.sub(re.escape(old), lambda _m: new, text, count=1, flags=flags)


def preview(value: Optional[str], *, dateformat: str, transdate: Optional[date] = None,
            currency: Optional[str] = None) -> str:
    """Het nummer dat ``next_number`` als volgende zou geven, zonder iets op te slaan (overstaplijst, K2).
    Sjablonen die een relatie of artikel nodig hebben (``<%name%>`` e.d.) blijven leeg."""
    stored = increment(value or "0")
    return expand(stored, dateformat=dateformat, transdate=transdate, currency=currency) if "<%" in stored else stored


def next_number(cur: psycopg.Cursor, field: str, *, dateformat: str,
                transdate: Optional[date] = None, currency: Optional[str] = None,
                fields: Optional[Mapping[str, Optional[str]]] = None,
                ini: Optional[str] = None) -> str:
    """Geef het volgende nummer voor ``defaults.field`` en sla het op.

    ini: zet het veld eerst op deze waarde (Form::update_defaults met $ini),
    bv. het zojuist gebruikte betaalkenmerk; opgeslagen wordt dan het volgende.
    """
    ini = re.sub(r";.*", "", ini) if ini else ini
    cur.execute("SELECT fldname FROM defaults WHERE fldname = %s", (field,))
    if not cur.fetchone():
        cur.execute("INSERT INTO defaults (fldname, fldvalue) VALUES (%s, %s)", (field, ini or None))
    elif ini:
        cur.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = %s", (ini, field))

    cur.execute("SELECT fldvalue FROM defaults WHERE fldname = %s FOR UPDATE", (field,))
    row = cur.fetchone()
    current = (row["fldvalue"] if row else None) or "0"

    stored = increment(current)
    cur.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = %s", (stored, field))

    if "<%" not in stored:
        return stored
    return expand(stored, dateformat=dateformat, transdate=transdate,
                  currency=currency, fields=fields)
