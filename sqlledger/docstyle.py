"""Huisstijl van de documenten per administratie (fase 4, HUISSTIJL_ANALYSE.md HS1–HS11).

Logo-maat en -ligging, accentkleur, lettertype, ligging van het adresblok en kop/voet aan of uit; zonder HTML in te
stellen (Beheer > Huisstijl). Opslag in ``x_doc_setting`` naast de documentinstellingen, met sleutels ``style.*``:
alleen wat afwijkt van de standaard, plus ``style.saved`` zodra de huisstijl één keer is opgeslagen (HS10: ook
ongewijzigd). Zonder ``style.*``-regels is elk document precies gelijk aan de standaardset van vóór de huisstijl;
met ``style.saved`` staat het adresblok vast op de vensterplek (keuze 3c).

Het logo zelf blijft een bestand bij de sjablonen (``<data_dir>/templates/<id>/logo.*``, HS4); dat beheert de
webapp. Het overnemen van een andere administratie (HS7) leest hier de regels en schrijft ze met ``source``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Dict, List, Optional, Tuple

import psycopg

from . import _db
from .errors import ValidationError
from .extensions import ensure_schema

PREFIX = "style."

#: logo: maximale hoogte × breedte in mm (keuze 3a; middel = de standaardopmaak)
LOGO_SIZES: Dict[str, Tuple[int, int]] = {"small": (14, 45), "medium": (22, 70), "large": (24, 95)}
POSITIONS = ("left", "center", "right")
ADDRESS_POSITIONS = ("left", "right")
#: lettertypen (keuze 1): "" = standaard (systeem-Helvetica, niet ingesloten); de bestanden levert de webapp mee
FONTS = ("", "liberation-sans", "inter", "source-sans-3", "source-serif-4", "lora")
#: accentkleur van de standaardopmaak (keuze 2b) en de snelkeuzes (keuze 2c)
DEFAULT_COLOR = "#111111"
SWATCHES = (("#111111", "zwart"), ("#1f3a5f", "donkerblauw"), ("#0f5560", "petrol"), ("#1e5631", "donkergroen"),
            ("#7a1f2b", "bordeaux"), ("#3c4043", "antraciet"))
#: minimaal contrast tegen wit (WCAG 2, gewone tekst; keuze 2d)
MIN_CONTRAST = 4.5

_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def normalize_color(value: str) -> Optional[str]:
    """``#abc``/``abc``/``#AABBCC`` → ``#aabbcc``; None als het geen hexcode is."""
    m = _HEX.match((value or "").strip())
    if not m:
        return None
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def contrast_with_white(color: str) -> float:
    """Contrastverhouding van een kleur tegen wit volgens WCAG 2 (1 … 21)."""
    h = normalize_color(color)
    if h is None:
        raise ValueError("geen kleur")

    def channel(i: int) -> float:
        c = int(h[1 + 2 * i:3 + 2 * i], 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
    return 1.05 / (lum + 0.05)


@dataclass
class DocStyle:
    logo_size: str = "medium"
    logo_pos: str = "left"
    color: str = DEFAULT_COLOR
    font: str = ""
    address_pos: str = "left"
    show_head: bool = True
    show_footer: bool = True
    saved: bool = False            # ooit opgeslagen (startlijst; adresblok vast op de vensterplek)

    @property
    def logo_box(self) -> Tuple[int, int]:
        return LOGO_SIZES.get(self.logo_size, LOGO_SIZES["medium"])

    def same_as(self, other: "DocStyle") -> bool:
        return self.to_rows() == other.to_rows()

    # ------------------------------------------------------------ opslag

    def to_rows(self) -> Dict[str, str]:
        """``style.*``-sleutels: alleen wat afwijkt, plus ``style.saved``."""
        out: Dict[str, str] = {}
        default = DocStyle()
        for f in fields(self):
            if f.name == "saved":
                continue
            value = getattr(self, f.name)
            if value != getattr(default, f.name):
                out[PREFIX + f.name] = ("1" if value else "0") if isinstance(value, bool) else str(value)
        if self.saved:
            out[PREFIX + "saved"] = "1"
        return out

    @classmethod
    def from_rows(cls, rows: Dict[str, str]) -> "DocStyle":
        """Lezen uit alle regels van ``x_doc_setting``; onbekende of ongeldige waarden = standaard."""
        def get(name: str) -> Optional[str]:
            return rows.get(PREFIX + name)

        s = cls()
        if get("logo_size") in LOGO_SIZES:
            s.logo_size = get("logo_size")
        if get("logo_pos") in POSITIONS:
            s.logo_pos = get("logo_pos")
        color = normalize_color(get("color") or "")
        if color:
            s.color = color
        if get("font") in FONTS:
            s.font = get("font")
        if get("address_pos") in ADDRESS_POSITIONS:
            s.address_pos = get("address_pos")
        s.show_head = get("show_head") != "0"
        s.show_footer = get("show_footer") != "0"
        s.saved = any(k.startswith(PREFIX) for k in rows)
        return s


def validate(s: DocStyle, *, contrast: bool = True) -> List[str]:
    """Meldingen (Nederlands; de webapp vertaalt ze); leeg = in orde. ``contrast=False``: een te lichte kleur
    mag (het voorbeeld toont die wel, keuze 2d)."""
    errors: List[str] = []
    if s.logo_size not in LOGO_SIZES or s.logo_pos not in POSITIONS:
        errors.append("Ongeldige keuze voor het logo.")
    if s.font not in FONTS:
        errors.append("Onbekend lettertype.")
    if s.address_pos not in ADDRESS_POSITIONS:
        errors.append("Ongeldige ligging van het adresblok.")
    color = normalize_color(s.color)
    if color is None:
        errors.append("Geef de kleur als hexcode, bijvoorbeeld #1F4E79.")
    elif contrast and contrast_with_white(color) < MIN_CONTRAST:
        errors.append("Deze kleur is te licht voor tekst op wit papier; kies een donkerdere tint.")
    return errors


# ================================================================== database

def load_rows(conn: psycopg.Connection) -> Dict[str, str]:
    """De ``style.*``-regels (leeg als de tabel nog niet bestaat)."""
    with _db.cursor(conn) as cur:
        cur.execute("SELECT to_regclass('x_doc_setting') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            return {}
        cur.execute("SELECT key, value FROM x_doc_setting WHERE key LIKE 'style.%%'")
        return {r["key"]: r["value"] for r in cur.fetchall()}


def load(conn: psycopg.Connection) -> DocStyle:
    return DocStyle.from_rows(load_rows(conn))


def save(conn: psycopg.Connection, s: DocStyle, *, login: str = "", source: str = "") -> List[str]:
    """Opslaan (eerst gecontroleerd); zet altijd ``style.saved``. ``source``: naam van de administratie waarvan
    de huisstijl is overgenomen (HS7, voor het logboek). Geeft de gewijzigde sleutels terug."""
    errors = validate(s)
    if errors:
        raise ValidationError(" ".join(errors))
    s.color = normalize_color(s.color) or DEFAULT_COLOR
    s.saved = True
    ensure_schema(conn)
    new = s.to_rows()
    with conn.transaction(), _db.cursor(conn) as cur:
        cur.execute("SELECT key, value FROM x_doc_setting WHERE key LIKE 'style.%%' FOR UPDATE")
        old = {r["key"]: r["value"] for r in cur.fetchall()}
        changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        for key in changed:
            if key in new:
                cur.execute("""INSERT INTO x_doc_setting (key, value, login) VALUES (%s, %s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                               updated = now()""", (key, new[key], login or None))
            else:
                cur.execute("DELETE FROM x_doc_setting WHERE key = %s", (key,))
        if _db.get_defaults(cur, "audittrail").get("audittrail"):
            _, employee_id = _db.get_employee(cur, login.split("@")[0]) if login else (None, None)
            cur.execute("""INSERT INTO audittrail (trans_id, tablename, reference, formname, action, employee_id)
                           VALUES (NULL, 'x_doc_setting', %s, 'docstyle', %s, %s)""",
                        ((source or "style")[:64], "copied" if source else "saved", employee_id))
    return changed
