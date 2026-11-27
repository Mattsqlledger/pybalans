"""Beheer (fase 2, beslispunten A1–A4, A7, A8): rekeningschema, btw-tarieven,
instellingen (``defaults``), boekperiode afsluiten, audittrail en locks.

Perl-origineel: ``SL/AM.pm`` (get_account, save_account, delete_account, taxes,
save_taxes, defaultaccounts, save_defaults, closedto, closebooks, audit_log,
remove_locks) en de controles uit ``bin/mozilla/am.pl``.

Bewuste afwijkingen (README 70–84):

* btw-vlag weghalen laat de tariefhistorie staan (Perl: ``DELETE FROM tax``);
* instellingen worden per sleutel opgeslagen en alleen als ze veranderd zijn
  ten opzichte van wat het scherm toonde (Perl gooide de hele tabel ``defaults``
  weg: ``yearend``, ``fxgain_accno_id`` e.d. gingen verloren en een oud scherm
  overschreef een nieuwere afsluitdatum of nummerteller);
* een rekening verwijderen kan alleen zonder boekingen en als nergens naar de
  rekening verwezen wordt — op de server gecontroleerd (Perl: alleen de knop);
* ``IC_taxpart``/``IC_taxservice`` herschrijven ``partstax`` niet (fase 3);
* GIFI (bouwstap 3): een code verwijderen kan alleen als geen rekening hem gebruikt, een gewijzigde code neemt
  ``chart.gifi_accno`` mee en blijft tekst (Perl: ``*= 1``, "020" werd 20).

Alle SQL is geparametriseerd. Foutmeldingen zijn Nederlands.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import psycopg

from . import _db, attachments, support
from .amounts import to_decimal
from .config import UserConfig
from .dates import yearend_month
from .errors import NotFoundError, ValidationError

#: volgorde waarin am.pl ``chart.link`` opbouwt (AM::save_account)
LINK_ORDER = ("AR", "AR_amount", "AR_tax", "AR_paid", "AR_discount",
              "AP", "AP_amount", "AP_tax", "AP_paid", "AP_discount",
              "IC", "IC_income", "IC_sale", "IC_expense", "IC_cogs", "IC_taxpart", "IC_taxservice")
SUMMARY_LINKS = ("AR", "AP", "IC")
#: koppelingen die niet samen met een hoofdrekening (AR/AP/IC) mogen (am.pl save_account r.297)
NOT_WITH_SUMMARY = ("AR_amount", "AR_tax", "AR_paid", "AP_amount", "AP_tax", "AP_paid",
                    "IC_taxpart", "IC_taxservice", "IC_sale", "IC_cogs", "IC_income", "IC_expense")
TAX_LINKS = ("AR_tax", "AP_tax", "IC_taxpart", "IC_taxservice")
CATEGORIES = {"A": "Activa", "L": "Passiva", "Q": "Eigen vermogen", "I": "Opbrengsten", "E": "Kosten"}

#: verwijzingen naar een rekening buiten acc_trans: (tabel, kolom)
ACCOUNT_REFERENCES = (
    ("customer", "arap_accno_id"), ("customer", "payment_accno_id"),
    ("customer", "discount_accno_id"), ("customer", "prepayment_accno_id"),
    ("vendor", "arap_accno_id"), ("vendor", "payment_accno_id"),
    ("vendor", "discount_accno_id"), ("vendor", "prepayment_accno_id"),
    ("parts", "inventory_accno_id"), ("parts", "income_accno_id"), ("parts", "expense_accno_id"),
    ("deduction", "employee_accno_id"), ("deduction", "employer_accno_id"),
    ("wage", "chart_id"),
)

#: instellingen op het scherm "Instellingen" (A3); de rest van ``defaults`` blijft ongemoeid
TEXT_SETTINGS = ("company", "address", "tel", "fax", "companyemail", "companywebsite",
                 "businessnumber", "referenceurl",
                 "glnumber", "sinumber", "vinumber", "customernumber", "vendornumber")
FLAG_SETTINGS = ("cdt", "namesbynumber", "lock_glnumber", "lock_sinumber",
                 "lock_customernumber", "lock_vendornumber")
SETTINGS = TEXT_SETTINGS + FLAG_SETTINGS + ("precision", "method", "typeofcontact", "fxgainloss_accno_id",
                                            "max_upload_size", "yearend")
#: standaardrekeningen voor artikelen: alleen tonen (fase 3)
IC_DEFAULTS = ("inventory_accno_id", "income_accno_id", "expense_accno_id")


def _collapse(text: Optional[str]) -> str:
    """am.pl/AM.pm: ``--`` (scheidingsteken in Perl-keuzelijsten) → ``-``, dubbele spaties weg, trimmen."""
    text = re.sub(r"-(-+)", "-", text or "")
    text = re.sub(r" ( )+", " ", text)
    return text.strip()


def _table_exists(cur: psycopg.Cursor, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table}",))
    return bool(cur.fetchone()["ok"])


def _count(cur: psycopg.Cursor, table: str, column: str, value: Any) -> int:
    """Aantal rijen met ``column = value``; 0 als tabel/kolom niet bestaat (oudere schema's)."""
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = %s AND column_name = %s", (table, column))
    if not cur.fetchone():
        return 0
    cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {column} = %s", (value,))  # vaste namen
    return int(cur.fetchone()["n"])


def usage(cur: psycopg.Cursor, refs: Sequence[Tuple[str, str]], value: Any) -> Dict[str, int]:
    """Waar wordt ``value`` gebruikt: {tabel: aantal} voor de gegeven (tabel, kolom)-paren."""
    out: Dict[str, int] = {}
    for table, column in refs:
        n = _count(cur, table, column, value)
        if n:
            out[table] = out.get(table, 0) + n
    return out


# ============================================================== rekeningschema

@dataclass
class Account:
    """Een rekening uit ``chart`` zoals het scherm (am.pl account_header) hem toont."""
    accno: str
    description: str = ""
    category: str = ""            # A L Q I E
    charttype: str = "A"          # A = rekening, H = kop
    link: Set[str] = field(default_factory=set)
    gifi_accno: str = ""
    contra: bool = False
    closed: bool = False
    id: Optional[int] = None
    # bankgegevens van een betaalrekening (tabel ``bank``, id = chart.id; eerste start ES11)
    bankname: str = ""
    iban: str = ""
    bic: str = ""

    @property
    def is_payment(self) -> bool:
        return self.charttype == "A" and bool({"AR_paid", "AP_paid"} & self.link)

    @property
    def link_string(self) -> str:
        return ":".join(k for k in LINK_ORDER if k in self.link)


@dataclass
class AccountView:
    account: Account
    has_transactions: bool = False
    default_for: List[str] = field(default_factory=list)   # defaults-sleutels die naar deze rekening wijzen
    used_by: Dict[str, int] = field(default_factory=dict)   # relaties/artikelen met deze rekening
    tax_rows: int = 0

    @property
    def can_delete(self) -> bool:
        return not (self.has_transactions or self.default_for or self.used_by)


class Admin:
    """Beheerfuncties op één administratie."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ---------------------------------------------------------------- rekeningen

    def accounts(self) -> List[Dict[str, Any]]:
        """Alle rekeningen (am.pl list_account) met vlaggen voor het overzicht."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id, c.accno, c.description, c.charttype, c.category, c.link,
                                  c.gifi_accno, c.contra, c.closed,
                                  EXISTS (SELECT 1 FROM acc_trans a WHERE a.chart_id = c.id) AS has_transactions,
                                  EXISTS (SELECT 1 FROM tax t WHERE t.chart_id = c.id) AS has_tax
                           FROM chart c ORDER BY c.accno""")
            return cur.fetchall()

    def _default_keys(self, cur: psycopg.Cursor, chart_id: int) -> List[str]:
        cur.execute("SELECT fldname FROM defaults WHERE fldname LIKE %s AND fldvalue = %s ORDER BY 1",
                    ("%accno_id", str(chart_id)))
        return [r["fldname"] for r in cur.fetchall()]

    def get_account(self, chart_id: int) -> AccountView:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT * FROM chart WHERE id = %s", (chart_id,))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f"Rekening {chart_id} niet gevonden.")
            acc = Account(id=r["id"], accno=r["accno"] or "", description=r["description"] or "",
                          category=(r["category"] or "").strip(), charttype=(r["charttype"] or "A").strip(),
                          link=set(filter(None, (r["link"] or "").split(":"))),
                          gifi_accno=r["gifi_accno"] or "", contra=bool(r["contra"]), closed=bool(r["closed"]))
            cur.execute("SELECT name, iban, bic FROM bank WHERE id = %s", (chart_id,))
            b = cur.fetchone()
            if b:
                acc.bankname, acc.iban, acc.bic = b["name"] or "", b["iban"] or "", b["bic"] or ""
            cur.execute("SELECT EXISTS (SELECT 1 FROM acc_trans WHERE chart_id = %s) AS x", (chart_id,))
            has_tx = bool(cur.fetchone()["x"])
            cur.execute("SELECT count(*) AS n FROM tax WHERE chart_id = %s", (chart_id,))
            ntax = int(cur.fetchone()["n"])
            return AccountView(acc, has_transactions=has_tx, default_for=self._default_keys(cur, chart_id),
                               used_by=usage(cur, ACCOUNT_REFERENCES, chart_id), tax_rows=ntax)

    @staticmethod
    def check_account(acc: Account) -> None:
        """Controles van am.pl save_account (r.286–304)."""
        if not acc.accno.strip():
            raise ValidationError("Rekeningnummer ontbreekt.")
        if acc.category not in CATEGORIES:
            raise ValidationError("Soort rekening ontbreekt.")
        if acc.charttype not in ("A", "H"):
            raise ValidationError("Kies rekening of kop.")
        unknown = set(acc.link) - set(LINK_ORDER)
        if unknown:
            raise ValidationError(f"Onbekende koppeling: {', '.join(sorted(unknown))}.")
        summary = [k for k in SUMMARY_LINKS if k in acc.link]
        if len(summary) > 1:
            raise ValidationError("Een rekening kan maar voor één van debiteuren, crediteuren of voorraad "
                                  "de hoofdrekening zijn.")
        if summary and any(k in acc.link for k in NOT_WITH_SUMMARY):
            raise ValidationError(f"Een hoofdrekening ({summary[0]}) kan geen andere koppelingen hebben.")
        for side in ("AR", "AP"):
            n = sum(1 for k in ("amount", "paid", "discount", "tax") if f"{side}_{k}" in acc.link)
            if n > 1:
                raise ValidationError(f"Kies bij {'verkoop' if side == 'AR' else 'inkoop'} maar één van "
                                      "omzet/kosten, betaling, korting of btw.")

    def save_account(self, acc: Account) -> int:
        """Rekening toevoegen of wijzigen (AM::save_account). Geeft het id terug.

        Btw-koppeling aan: een tariefregel (0%) als er nog geen is. Btw-koppeling
        uit: de tarieven blijven bewaard (Perl wiste ze; README 70).
        """
        acc.accno = _collapse(re.sub(r"[ ']", "", acc.accno or ""))
        acc.gifi_accno = _collapse(re.sub(r"[ ']", "", acc.gifi_accno or ""))
        acc.description = _collapse(acc.description)
        if acc.charttype == "H":
            acc.link = set()       # het scherm toont bij een kop geen koppelingen
        self.check_account(acc)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT id FROM chart WHERE accno = %s AND id IS DISTINCT FROM %s",
                        (acc.accno, acc.id))
            if cur.fetchone():
                raise ValidationError(f"Rekeningnummer {acc.accno} bestaat al.")
            values = (acc.accno, acc.description, acc.charttype, acc.gifi_accno, acc.category,
                      acc.link_string, acc.contra, acc.closed)
            if acc.id:
                cur.execute("SELECT id FROM chart WHERE id = %s FOR UPDATE", (acc.id,))
                if not cur.fetchone():
                    raise NotFoundError(f"Rekening {acc.id} niet gevonden.")
                cur.execute("""UPDATE chart SET accno = %s, description = %s, charttype = %s, gifi_accno = %s,
                                      category = %s, link = %s, contra = %s, closed = %s WHERE id = %s""",
                            values + (acc.id,))
                chart_id = acc.id
            else:
                cur.execute("""INSERT INTO chart (accno, description, charttype, gifi_accno, category, link,
                                                  contra, closed)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""", values)
                chart_id = cur.fetchone()["id"]
            if acc.is_payment:
                self._save_bank(cur, chart_id, acc)
            if any(k in acc.link for k in TAX_LINKS):
                cur.execute("SELECT 1 FROM tax WHERE chart_id = %s", (chart_id,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO tax (chart_id, rate) VALUES (%s, 0)", (chart_id,))
            # Perl: audittrail met het id uit het formulier (0 bij een nieuwe rekening = geen regel)
            support.audittrail(cur, self.config, trans_id=acc.id or 0, tablename="chart",
                               reference=acc.accno, formname="", action="saved")
        acc.id = chart_id
        return chart_id

    @staticmethod
    def _save_bank(cur: psycopg.Cursor, chart_id: int, acc: Account) -> None:
        """Bankgegevens van een betaalrekening in ``bank`` (zoals bankimport en SL am.pl bank_accounts): alleen
        schrijven als er iets verandert; IBAN (mod 97) en BIC (vorm) gecontroleerd als ze gewijzigd zijn."""
        from .contacts import bic_valid, iban_valid
        name = _collapse(acc.bankname)[:64]
        iban = re.sub(r"\s", "", acc.iban or "").upper()
        bic = (acc.bic or "").strip().upper()
        cur.execute("SELECT name, iban, bic FROM bank WHERE id = %s", (chart_id,))
        old = cur.fetchone()
        before = (old["name"] or "", old["iban"] or "", old["bic"] or "") if old else ("", "", "")
        if (name, iban, bic) == before:
            return
        if iban and iban != before[1] and not iban_valid(iban):
            raise ValidationError(f"IBAN {iban} is niet geldig (controle mislukt).")
        if bic and bic != before[2] and not bic_valid(bic):
            raise ValidationError(f"BIC {bic} heeft niet de juiste vorm (8 of 11 tekens).")
        if old:
            cur.execute("UPDATE bank SET name = %s, iban = %s, bic = %s WHERE id = %s",
                        (name or None, iban or None, bic or None, chart_id))
        else:
            cur.execute("INSERT INTO bank (id, name, iban, bic) VALUES (%s, %s, %s, %s)",
                        (chart_id, name or None, iban or None, bic or None))

    def delete_account(self, chart_id: int) -> None:
        """Rekening verwijderen (AM::delete_account), alleen als dat veilig kan.

        Geweigerd bij boekingen, als standaardrekening (``defaults.*_accno_id``) of
        als een relatie/artikel ernaar verwijst — op de server, niet alleen in het
        scherm (README 71).
        """
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT accno FROM chart WHERE id = %s FOR UPDATE", (chart_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"Rekening {chart_id} niet gevonden.")
            cur.execute("SELECT 1 FROM acc_trans WHERE chart_id = %s LIMIT 1", (chart_id,))
            if cur.fetchone():
                raise ValidationError("Op deze rekening is geboekt; zet hem op 'gesloten' in plaats van verwijderen.")
            keys = self._default_keys(cur, chart_id)
            if keys:
                raise ValidationError(f"Dit is een standaardrekening ({', '.join(keys)}) en kan niet "
                                      "verwijderd worden.")
            used = usage(cur, ACCOUNT_REFERENCES, chart_id)
            if used:
                raise ValidationError("Deze rekening is nog in gebruik bij: "
                                      + ", ".join(f"{t} ({n})" for t, n in sorted(used.items())) + ".")
            cur.execute("DELETE FROM chart WHERE id = %s", (chart_id,))
            cur.execute("DELETE FROM bank WHERE id = %s", (chart_id,))
            cur.execute("DELETE FROM address WHERE trans_id = %s", (chart_id,))
            cur.execute("DELETE FROM translation WHERE trans_id = %s", (chart_id,))
            for table in ("partstax", "customertax", "vendortax", "tax"):
                cur.execute(f"DELETE FROM {table} WHERE chart_id = %s", (chart_id,))
            support.audittrail(cur, self.config, trans_id=chart_id, tablename="chart",
                               reference=row["accno"], formname="", action="deleted")

    # ---------------------------------------------------------------- GIFI (bouwstap 3, GF1–GF3)

    @staticmethod
    def clean_gifi(code: str, description: str = "") -> Tuple[str, str]:
        """Code en omschrijving opschonen zoals AM::save_gifi: geen spaties of ' in de code, '--' wordt '-',
        dubbele spaties één spatie."""
        code = re.sub(r"-(-)+", "-", re.sub(r"[ ']", "", code or ""))
        description = re.sub(r" ( )+", " ", re.sub(r"-(-)+", "-", (description or "").strip()))
        return code, description

    def gifi_codes(self) -> List[Dict[str, Any]]:
        """Alle GIFI-codes met het aantal rekeningen (en kopregels) dat ernaar verwijst."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT g.accno, g.description, count(c.id) AS accounts
                           FROM gifi g LEFT JOIN chart c ON c.gifi_accno = g.accno
                           GROUP BY g.accno, g.description ORDER BY g.accno""")
            return cur.fetchall()

    def gifi_orphans(self) -> List[Dict[str, Any]]:
        """Rekeningen met een GIFI-code die niet in de tabel ``gifi`` staat (dode verwijzing)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id, c.accno, c.description, c.charttype, c.gifi_accno FROM chart c
                           WHERE coalesce(c.gifi_accno, '') <> ''
                             AND NOT EXISTS (SELECT 1 FROM gifi g WHERE g.accno = c.gifi_accno)
                           ORDER BY c.gifi_accno, c.accno""")
            return cur.fetchall()

    def _gifi_accounts(self, cur: psycopg.Cursor, code: str) -> List[Dict[str, Any]]:
        cur.execute("SELECT id, accno, description, charttype FROM chart WHERE gifi_accno = %s ORDER BY accno",
                    (code,))
        return cur.fetchall()

    def get_gifi(self, code: str) -> Dict[str, Any]:
        """Eén GIFI-code met de rekeningen die hem gebruiken (``accounts``)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT accno, description FROM gifi WHERE accno = %s", (code,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError("GIFI-code niet gevonden.")
            row["accounts"] = self._gifi_accounts(cur, code)
            return row

    def save_gifi(self, code: str, description: str, oldcode: Optional[str] = None) -> str:
        """GIFI-code toevoegen of wijzigen (AM::save_gifi). Geeft de code terug.

        Een nieuwe code bij een bestaande (``oldcode``) neemt de rekeningen mee: ``chart.gifi_accno`` wordt in
        dezelfde transactie bijgewerkt. De code blijft tekst; Perl maakte van de oude code een getal
        (``$form->{id} *= 1``), waardoor "020" als 20 niets wijzigde en een code met letters misging.
        """
        code, description = self.clean_gifi(code, description)
        if not code:
            raise ValidationError("Code ontbreekt.")
        if not description:
            raise ValidationError("Omschrijving ontbreekt.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if oldcode:
                cur.execute("SELECT 1 FROM gifi WHERE accno = %s FOR UPDATE", (oldcode,))
                if not cur.fetchone():
                    raise NotFoundError("GIFI-code niet gevonden.")
            if code != oldcode:
                cur.execute("SELECT 1 FROM gifi WHERE accno = %s", (code,))
                if cur.fetchone():
                    raise ValidationError(f"GIFI-code {code} bestaat al.")
            if oldcode:
                cur.execute("UPDATE gifi SET accno = %s, description = %s WHERE accno = %s",
                            (code, description, oldcode))
                if code != oldcode:
                    cur.execute("UPDATE chart SET gifi_accno = %s WHERE gifi_accno = %s", (code, oldcode))
            else:
                cur.execute("INSERT INTO gifi (accno, description) VALUES (%s, %s)", (code, description))
            support.audittrail(cur, self.config, trans_id=1, tablename="gifi", reference=code,
                               formname="", action="saved")
        return code

    def delete_gifi(self, code: str) -> None:
        """GIFI-code verwijderen, alleen als geen rekening hem gebruikt (GF2; Perl keek alleen naar boekingen en
        liet rekeningen met een dode verwijzing achter)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM gifi WHERE accno = %s FOR UPDATE", (code,))
            if not cur.fetchone():
                raise NotFoundError("GIFI-code niet gevonden.")
            accounts = self._gifi_accounts(cur, code)
            if accounts:
                used = ", ".join(a["accno"] for a in accounts)
                raise ValidationError(f"GIFI-code {code} is in gebruik bij rekening {used}.")
            cur.execute("DELETE FROM gifi WHERE accno = %s", (code,))
            support.audittrail(cur, self.config, trans_id=1, tablename="gifi", reference=code,
                               formname="", action="deleted")

    # ---------------------------------------------------------------- btw

    def taxes(self) -> List[Dict[str, Any]]:
        """Tariefregels per btw-rekening (AM::taxes); ``rate`` als percentage (Decimal).

        Volgorde: rekeningnummer, dan geldig-tot (leeg = huidig tarief, achteraan).
        """
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id AS chart_id, c.accno, c.description, c.closed, c.link,
                                  t.rate, t.taxnumber, t.validto
                           FROM chart c JOIN tax t ON t.chart_id = c.id
                           ORDER BY c.accno, t.validto NULLS LAST""")
            rows = cur.fetchall()
        for r in rows:
            r["rate"] = to_decimal(r["rate"]) * 100 if r["rate"] is not None else None
            r["has_tax_link"] = any(k in (r["link"] or "").split(":") for k in TAX_LINKS)
        return rows

    def save_taxes(self, rows: Sequence[Mapping[str, Any]], closed: Optional[Mapping[int, bool]] = None) -> None:
        """Btw-tarieven opslaan (AM::save_taxes).

        ``rows``: dicts met chart_id, rate (percentage, Decimal of None), taxnumber,
        validto (date of None). Een helemaal lege regel telt niet mee. Per rekening:
        tarief verplicht, geldig-tot strikt oplopend, alleen de laatste regel mag
        zonder geldig-tot (README 72). Alleen rekeningen waarvan iets veranderd is,
        worden herschreven; rekeningen die niet in ``rows`` voorkomen blijven ongemoeid.

        BT1: een tarief dat al gebruikt is, blijft staan. Tot en met de laatste boeking met deze
        btw-rekening (``tax_last_used``) moet het tarief op elke datum gelijk blijven; anders volgt
        een fout met het advies een nieuw tarief met een ingangsdatum toe te voegen (``with_new_rate``).
        Het nummer mag wel veranderen.
        """
        per: Dict[int, List[Tuple[Decimal, Optional[str], Optional[date]]]] = {}
        for r in rows:
            cid = int(r["chart_id"])
            rate, number, validto = r.get("rate"), (r.get("taxnumber") or "").strip() or None, r.get("validto")
            per.setdefault(cid, [])
            if rate in (None, "") and not number and not validto:
                continue
            if rate in (None, ""):
                raise ValidationError("Vul bij elke btw-regel een tarief in.")
            rate = to_decimal(rate)
            if not -100 < rate < 100:
                # negatief mag (verlegde btw: een paar −21 %/+21 %, zoals Perl IS/IR/AA met ``rate * ml > 0``)
                raise ValidationError("Een btw-tarief ligt tussen -100% en 100%.")
            per[cid].append((rate.normalize(), number, validto))
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for cid, lst in per.items():
                cur.execute("SELECT rate, taxnumber, validto FROM tax WHERE chart_id = %s "
                            "ORDER BY validto NULLS LAST", (cid,))
                old = [((to_decimal(o["rate"]) * 100).normalize() if o["rate"] is not None else None,
                        (o["taxnumber"] or "").strip() or None, o["validto"]) for o in cur.fetchall()]
                if sorted(old, key=repr) == sorted(lst, key=repr):
                    continue        # ongewijzigd: niet controleren en niet herschrijven
                prev = None
                for i, (_, _, validto) in enumerate(lst):
                    if validto is None and i != len(lst) - 1:
                        raise ValidationError("Alleen de laatste btw-regel van een rekening mag zonder "
                                              "'geldig tot' zijn.")
                    if validto is not None and prev is not None and validto <= prev:
                        raise ValidationError("'Geldig tot' moet per rekening strikt oplopen.")
                    prev = validto
                cur.execute("SELECT accno FROM chart WHERE id = %s", (cid,))
                chart = cur.fetchone()
                if not chart:
                    raise NotFoundError(f"Rekening {cid} niet gevonden.")
                last = self.tax_last_used(cur, cid)
                changed = _rate_changed(old, lst, last) if last else None
                if changed is not None:
                    from .dates import format_date
                    raise ValidationError(
                        f"Het tarief van {_pct(changed)}% van rekening {chart['accno']} is al gebruikt in boekingen "
                        f"tot en met {format_date(last, self.config.dateformat)}. Voeg een nieuw tarief toe met "
                        f"een ingangsdatum.")
                cur.execute("DELETE FROM tax WHERE chart_id = %s", (cid,))
                for rate, number, validto in lst:
                    cur.execute("INSERT INTO tax (chart_id, rate, taxnumber, validto) VALUES (%s, %s, %s, %s)",
                                (cid, rate / 100, number, validto))
            for cid, flag in (closed or {}).items():
                cur.execute("UPDATE chart SET closed = %s WHERE id = %s AND closed IS DISTINCT FROM %s",
                            (bool(flag), int(cid), bool(flag)))
            support.audittrail(cur, self.config, trans_id=1, tablename="tax", reference="",
                               formname="", action="saved")

    @staticmethod
    def tax_last_used(cur, chart_id: int) -> Optional[date]:
        """Laatste datum waarop btw-rekening ``chart_id`` gebruikt is (BT1): een journaalregel op de rekening,
        of een factuur- of orderregel met een artikel dat de rekening heeft (ook bij 0%, dat geen regel boekt)."""
        cur.execute("""SELECT max(d) AS d FROM (
                           SELECT max(transdate) AS d FROM acc_trans WHERE chart_id = %(c)s
                           UNION ALL
                           SELECT max(a.transdate) FROM invoice i JOIN partstax pt ON (pt.parts_id = i.parts_id)
                             JOIN ar a ON (a.id = i.trans_id) WHERE pt.chart_id = %(c)s
                           UNION ALL
                           SELECT max(a.transdate) FROM invoice i JOIN partstax pt ON (pt.parts_id = i.parts_id)
                             JOIN ap a ON (a.id = i.trans_id) WHERE pt.chart_id = %(c)s
                           UNION ALL
                           SELECT max(o.transdate) FROM orderitems i JOIN partstax pt ON (pt.parts_id = i.parts_id)
                             JOIN oe o ON (o.id = i.trans_id) WHERE pt.chart_id = %(c)s) x""", {"c": chart_id})
        row = cur.fetchone()
        return row["d"] if row else None

    # ---------------------------------------------------------------- instellingen

    def settings(self) -> Dict[str, str]:
        """Huidige waarden van de instellingen op het scherm (leeg = niet gezet)."""
        with _db.cursor(self.conn) as cur:
            d = _db.get_defaults(cur)
        out = {k: d.get(k) or "" for k in SETTINGS + IC_DEFAULTS}
        # wat geldt (stap 4f): 0 (SQL-Ledger 4.x) = leeg, breuken/te groot zoals attachments.setting_mb
        out["max_upload_size"] = str(attachments.setting_mb(out["max_upload_size"]) or "")
        out["yearend"] = str(yearend_month(out["yearend"]))     # JA1: leeg = 12
        return out

    def last_yearend(self) -> Optional[date]:
        """Datum van de laatste jaarafsluiting (tabel ``yearend``), voor de waarschuwing bij JA5."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT max(transdate) AS d FROM yearend")
            r = cur.fetchone()
        return r["d"] if r else None

    def setting_accounts(self) -> Dict[str, List[Dict[str, Any]]]:
        """Keuzelijsten standaardrekeningen (AM::defaultaccounts)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT id, accno, description FROM chart
                           WHERE category IN ('I', 'E') AND charttype = 'A' ORDER BY accno""")
            fx = cur.fetchall()
            cur.execute("SELECT id, accno, description, link FROM chart WHERE link LIKE '%%IC%%' ORDER BY accno")
            ic = cur.fetchall()
        def having(*keys):
            return [r for r in ic if any(k in (r["link"] or "").split(":") for k in keys)]
        return {"fxgainloss_accno_id": fx, "inventory_accno_id": having("IC"),
                "income_accno_id": having("IC_income", "IC_sale"),
                "expense_accno_id": having("IC_expense", "IC_cogs")}

    def save_settings(self, values: Mapping[str, Any], shown: Optional[Mapping[str, Any]] = None) -> List[str]:
        """Instellingen opslaan, per sleutel (README 73).

        Alleen sleutels uit ``SETTINGS`` die in ``values`` staan en afwijken van
        ``shown`` (wat het scherm toonde; standaard de huidige waarde) worden
        geschreven. Leeg = sleutel weg (zoals Perl: een uitgevinkte optie staat niet
        in ``defaults``). Andere sleutels blijven altijd staan. Geeft de gewijzigde
        sleutels terug.
        """
        new = {k: self._clean_setting(k, values[k]) for k in SETTINGS if k in values}
        if "precision" in new and new["precision"] == "":
            raise ValidationError("Vul de precisie (aantal decimalen) in.")
        changed = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            current = _db.get_defaults(cur)
            if new.get("fxgainloss_accno_id"):
                cur.execute("SELECT 1 FROM chart WHERE id = %s AND category IN ('I', 'E') AND charttype = 'A'",
                            (int(new["fxgainloss_accno_id"]),))
                if not cur.fetchone():
                    raise ValidationError("Kies voor koersverschillen een opbrengst- of kostenrekening.")
            for key, value in new.items():
                stored = current.get(key) or ""
                try:
                    stored_clean = self._clean_setting(key, stored)
                except ValidationError:
                    stored_clean = stored
                before = stored_clean
                if shown is not None and key in shown:
                    before = self._clean_setting(key, shown.get(key))
                if value == before or value == stored_clean:
                    continue        # niet gewijzigd op het scherm, of staat al zo
                cur.execute("DELETE FROM defaults WHERE fldname = %s", (key,))
                if value != "":
                    cur.execute("INSERT INTO defaults (fldname, fldvalue) VALUES (%s, %s)", (key, value))
                changed.append(key)
                if key == "yearend":
                    # JA5: wijzigen mag ook na afsluitingen; die blijven staan, wel in de audittrail
                    support.audittrail(cur, self.config, trans_id=1, tablename="defaults",
                                       reference=f"{yearend_month(stored)} -> {value}", formname="yearend",
                                       action="saved")
        return changed

    @staticmethod
    def _clean_setting(key: str, value: Any) -> str:
        if value is None or value is False:
            return ""
        if value is True:
            value = "1"
        value = str(value).replace("\r\n", "\n").replace("\r", "\n")
        if key in FLAG_SETTINGS:
            return "1" if value not in ("", "0") else ""
        if key == "method":
            return "cash" if value == "cash" else ""
        if key == "typeofcontact":
            return "person" if value == "person" else ""
        if key == "precision":
            value = value.strip()
            if value == "":
                return ""
            if not value.isdigit() or int(value) > 10:
                raise ValidationError("Precisie: een heel getal van 0 tot en met 10.")
            return str(int(value))
        if key == "max_upload_size":
            value = value.strip()
            if value == "":
                return ""
            if not value.isdigit() or not attachments.MIN_MB <= int(value) <= attachments.MAX_MB:
                raise ValidationError("Maximale uploadgrootte: een heel getal van 1 tot en met 25 MB (leeg = 10 MB).")
            return str(int(value))
        if key == "yearend":
            value = value.strip()
            if value == "":
                return "12"
            if not value.isdigit() or not 1 <= int(value) <= 12:
                raise ValidationError("Boekjaar eindigt in: kies een maand (1 tot en met 12).")
            return str(int(value))
        if key == "fxgainloss_accno_id":
            value = value.strip()
            if value and not value.isdigit():
                raise ValidationError("Ongeldige rekening voor koersverschillen.")
            return value
        return value

    # ---------------------------------------------------------------- afsluiten (A4)

    def closing(self) -> Dict[str, Any]:
        """closedto (ruw, zoals opgeslagen: jjjjmmdd), revtrans, audittrail (AM::closedto)."""
        with _db.cursor(self.conn) as cur:
            d = _db.get_defaults(cur, "closedto", "revtrans", "audittrail")
        raw = (d.get("closedto") or "").strip()
        parsed = None
        if raw.isdigit() and len(raw) == 8:
            try:
                parsed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                parsed = None       # bv. 20173112 in echte data
        return {"closedto": raw, "closedto_date": parsed,
                "revtrans": (d.get("revtrans") or "") not in ("", "0"),
                "audittrail": (d.get("audittrail") or "") not in ("", "0")}

    def save_closing(self, closedto: Any, revtrans: bool, audittrail: bool) -> None:
        """Boekperiode afsluiten (AM::closebooks), zonder audittrail te wissen.

        ``closedto``: date, None (open) of de ongewijzigde ruwe waarde (str) — die
        blijft dan precies staan (ook een ongeldige als 20173112).
        """
        if isinstance(closedto, date):
            value = closedto.strftime("%Y%m%d")
        elif closedto in (None, ""):
            value = ""
        else:
            value = str(closedto).strip()
            if not value.isdigit():
                raise ValidationError("Ongeldige afsluitdatum.")
        new = {"closedto": value, "revtrans": "1" if revtrans else "", "audittrail": "1" if audittrail else ""}
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            current = _db.get_defaults(cur, "closedto", "revtrans", "audittrail")
            for key, v in new.items():
                old = current.get(key) or ""
                if key != "closedto" and (old not in ("", "0")) == bool(v):
                    continue
                if key == "closedto" and old.strip() == v:
                    continue
                cur.execute("DELETE FROM defaults WHERE fldname = %s", (key,))
                if v:
                    cur.execute("INSERT INTO defaults (fldname, fldvalue) VALUES (%s, %s)", (key, v))

    # ---------------------------------------------------------------- audittrail (A7)

    def audit_actions(self) -> Dict[str, List[Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT DISTINCT action FROM audittrail WHERE action IS NOT NULL ORDER BY 1")
            actions = [r["action"] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT tablename FROM audittrail WHERE tablename IS NOT NULL ORDER BY 1")
            tables = [r["tablename"] for r in cur.fetchall()]
            cur.execute("SELECT id, name, login FROM employee ORDER BY name")
            employees = cur.fetchall()
        return {"actions": actions, "tables": tables, "employees": employees}

    def audit_log(self, *, employee_id: Optional[int] = None, datefrom: Optional[date] = None,
                  dateto: Optional[date] = None, action: Optional[str] = None,
                  reference: Optional[str] = None, tablename: Optional[str] = None,
                  limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        """Audittrail (AM::audit_log), nieuwste eerst; ``reference`` = deel van het kenmerk."""
        where, params = ["TRUE"], []
        if employee_id is not None:
            where.append("a.employee_id = %s")
            params.append(employee_id)
        if datefrom:
            where.append("a.transdate >= %s")
            params.append(datefrom)
        if dateto:
            where.append("a.transdate < %s::date + 1")
            params.append(dateto)
        if action:
            where.append("a.action = %s")
            params.append(action)
        if tablename:
            where.append("a.tablename = %s")
            params.append(tablename)
        if reference:
            where.append("lower(a.reference) LIKE %s")
            params.append("%" + reference.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT a.transdate, a.tablename, a.reference, a.formname, a.action, a.trans_id,
                                   a.employee_id, e.name, e.login
                            FROM audittrail a LEFT JOIN employee e ON e.id = a.employee_id
                            WHERE {' AND '.join(where)}
                            ORDER BY a.transdate DESC, a.trans_id DESC NULLS LAST
                            LIMIT %s OFFSET %s""", params + [limit, offset])
            rows = cur.fetchall()
        for r in rows:
            if not r["employee_id"]:
                r["login"] = r["login"] or "admin"
        return rows

    # ---------------------------------------------------------------- locks (A7)

    def locks(self) -> List[Dict[str, Any]]:
        """Openstaande record-locks (semaphore) met wie en waarop."""
        now = int(time.time())
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT s.id, s.login, s.module, s.expires, e.name,
                                  COALESCE(g.reference, ar.invnumber, ap.invnumber) AS reference,
                                  CASE WHEN g.id IS NOT NULL THEN 'gl' WHEN ar.id IS NOT NULL THEN 'ar'
                                       WHEN ap.id IS NOT NULL THEN 'ap' END AS kind
                           FROM semaphore s
                           LEFT JOIN employee e ON e.login = split_part(s.login, '@', 1)
                           LEFT JOIN gl g ON g.id = s.id
                           LEFT JOIN ar ON ar.id = s.id
                           LEFT JOIN ap ON ap.id = s.id
                           ORDER BY s.login, s.id""")
            rows = cur.fetchall()
        for r in rows:
            try:
                exp = int(str(r["expires"] or "0"))
            except ValueError:
                exp = 0
            r["expires_at"] = datetime.fromtimestamp(exp) if exp else None
            r["expired"] = exp < now
        return rows

    def release_lock(self, trans_id: int, login: Optional[str] = None) -> int:
        """Eén lock vrijgeven; geeft het aantal verwijderde rijen."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if login is None:
                cur.execute("DELETE FROM semaphore WHERE id = %s", (trans_id,))
            else:
                cur.execute("DELETE FROM semaphore WHERE id = %s AND login = %s", (trans_id, login))
            return cur.rowcount

    def release_all_locks(self) -> int:
        """Alle locks vrijgeven (AM::remove_locks)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("DELETE FROM semaphore")
            return cur.rowcount


# ============================================================== A8

def fix_fxgainloss(conn: psycopg.Connection) -> Optional[str]:
    """Zet ``fxgainloss_accno_id`` als die ontbreekt (A8).

    Een administratie die met het SQL-Ledger-sjabloon is aangemaakt heeft
    ``fxgain_accno_id``/``fxloss_accno_id``, maar de code leest alleen
    ``fxgainloss_accno_id`` (README 84). Als de twee oude sleutels gelijk zijn of
    er maar één gevuld is, wordt die rekening de koersverschilrekening. Zijn ze
    verschillend, dan verandert er niets en komt er een melding terug.
    Geeft None (niets te doen of rechtgezet) of een melding.
    """
    with conn.transaction(), _db.cursor(conn) as cur:
        d = _db.get_defaults(cur, "fxgainloss_accno_id", "fxgain_accno_id", "fxloss_accno_id")
        if (d.get("fxgainloss_accno_id") or "").strip():
            return None
        gain, loss = (d.get("fxgain_accno_id") or "").strip(), (d.get("fxloss_accno_id") or "").strip()
        if not gain and not loss:
            return None
        if gain and loss and gain != loss:
            return ("Er zijn twee verschillende rekeningen voor koerswinst en koersverlies; kies bij "
                    "Beheer > Instellingen één rekening voor koersverschillen.")
        value = gain or loss
        cur.execute("SELECT 1 FROM chart WHERE id = %s", (int(value) if value.isdigit() else 0,))
        if not cur.fetchone():
            return "De rekening voor koersverschillen bestaat niet meer; kies er een bij Beheer > Instellingen."
        cur.execute("DELETE FROM defaults WHERE fldname = 'fxgainloss_accno_id'")
        cur.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('fxgainloss_accno_id', %s)", (value,))
    return None


# ================================================================== btw-tarieven (BT1)

def _effective(rows: Sequence[Tuple[Any, Any, Optional[date]]], day: date):
    """Tarief op ``day``: de eerste regel (op geldig-tot, leeg achteraan) die dan nog geldt, zoals de facturen
    het tarief zoeken (``validto >= datum OR validto IS NULL``)."""
    for rate, _number, validto in sorted(rows, key=lambda r: (r[2] is None, r[2] or date.max)):
        if validto is None or validto >= day:
            return rate
    return None


def _rate_changed(old, new, last: date):
    """Het oude tarief dat tot en met ``last`` ergens anders wordt, of None. Gecontroleerd op elke datum waar een
    van beide reeksen van tarief wisselt (plus de vroegste en de laatste datum)."""
    days = {date.min, last}
    for _r, _n, v in list(old) + list(new):
        if v is not None and v <= last:
            days.add(v)
            if v < last:
                days.add(v + timedelta(days=1))
    for day in sorted(days):
        o, n = _effective(old, day), _effective(new, day)
        if o is not None and (n is None or Decimal(o) != Decimal(n)):
            return o
    return None


def _pct(rate) -> str:
    text = f"{Decimal(rate).normalize():f}"
    return text


def with_new_rate(rows: Sequence[Mapping[str, Any]], chart_id: int, rate, start: date) -> List[Dict[str, Any]]:
    """"Nieuw tarief per datum" (BT1): het lopende tarief van ``chart_id`` krijgt geldig tot = de dag vóór
    ``start`` en er komt een regel met ``rate`` zonder einddatum bij (zelfde nummer). Andere rekeningen
    ongewijzigd."""
    out = [dict(r) for r in rows]
    mine = [r for r in out if int(r["chart_id"]) == int(chart_id) and r.get("rate") not in (None, "")]
    number = None
    for r in mine:
        if r.get("validto") is None:
            r["validto"] = start - timedelta(days=1)
            number = r.get("taxnumber")
    if not number and mine:
        number = mine[-1].get("taxnumber")
    last = max((i for i, r in enumerate(out) if int(r["chart_id"]) == int(chart_id)), default=len(out) - 1)
    out.insert(last + 1, {"chart_id": int(chart_id), "rate": rate, "taxnumber": number, "validto": None})
    return out
