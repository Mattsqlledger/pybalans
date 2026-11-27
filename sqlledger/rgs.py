"""RGS-koppeling: elke rekening aan een code van het Referentie GrootboekSchema (REKENINGSCHEMA_ANALYSE.md RS13).

De RGS-lijst zelf wordt **niet** meegeleverd (keuze A 2026-10-11, zolang SBR geen toestemming heeft gegeven): de
systeembeheerder leest het officiële bestand van referentiegrootboekschema.nl in (``RGS 3.8-def.xlsx``); hier wordt het
omgezet naar een eigen JSON in de datamap (``rgs/rgs-<versie>.json``), voor de hele installatie. Per administratie
staat de gekozen code in ``x_account_rgs`` (rekening, RGS-versie, code; migratie 15), zodat SQL-Ledger niets merkt.

Voorstellen (zoals Exact Online: tot drie, niets automatisch): uit een koppeling met een oudere RGS-versie (via het
blad "RGS3.8-versus-RGS3.7"), uit de koppelingen van de rekening (debiteuren, crediteuren, kas/bank, btw), uit de soort
(balans of W&V, debet of credit) en uit de omschrijving (gedeelde woorden).
"""

from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg

from . import _db
from .errors import LedgerError, ValidationError


class RgsFileError(LedgerError):
    pass


@dataclass
class RgsCode:
    code: str
    short: str
    long: str
    dc: str                     # D / C / ''
    level: int
    sort: str = ""
    number: str = ""
    mkb: bool = False           # in het blad MKB-RGS
    basis: bool = False
    inactive: bool = False


@dataclass
class RgsList:
    version: str
    codes: Dict[str, RgsCode] = field(default_factory=dict)
    history: Dict[str, Dict[str, str]] = field(default_factory=dict)   # oude versie → {oude code: nieuwe code}
    source: str = ""
    prerelease: List[str] = field(default_factory=list)   # tekenen van een alfa-/bètaversie (``prerelease_signs``)

    def to_json(self) -> str:
        return json.dumps({"version": self.version, "source": self.source, "history": self.history,
                           "prerelease": self.prerelease,
                           "codes": [asdict(c) for c in self.codes.values()]}, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "RgsList":
        d = json.loads(text)
        return cls(version=d["version"], source=d.get("source", ""), history=d.get("history", {}),
                   prerelease=d.get("prerelease", []), codes={c["code"]: RgsCode(**c) for c in d["codes"]})


#: woorden van een voorlopige versie (v1.0.1 PB13): alfa, bèta, consultatie, concept
_PRE = re.compile(r"(?i)(alfa|alpha|b[eèé]ta|consultatie|concept|draft)")


def prerelease_signs(filename: str, total_sheet: str, recap: str = "") -> List[str]:
    """Lijkt het bestand een alfa-, bèta- of consultatieversie (PB13: waarschuwen, niet weigeren)?

    Het officiële bestand ``RGS 3.8-def.xlsx`` heeft het blad ``Totaal-RGS3.8-def`` en een blad ``Recap`` dat begint
    met "Bijgaand de definitieve versie van RGS 3.8 …"; de andere bladen (bv. ``Zorg-RGS3.8-beta``) tellen niet mee.
    Geeft de gevonden tekenen: ``filename`` (naam met alfa/bèta/…), ``sheet`` (het Totaal-blad eindigt niet op
    ``-def`` of noemt alfa/bèta), ``recap`` (de eerste zin van Recap noemt alfa/bèta/consultatie en niet
    "definitieve")."""
    out = []
    if _PRE.search(Path(filename or "").stem):
        out.append("filename")
    if _PRE.search(total_sheet) or not re.search(r"-def\s*$", total_sheet, re.I):
        out.append("sheet")
    if recap and _PRE.search(recap) and "definitie" not in recap.lower():
        out.append("recap")
    return out


# ------------------------------------------------------------------ inlezen

def _header(rows: List[tuple]) -> Tuple[int, Dict[str, int]]:
    for i, r in enumerate(rows[:10]):
        names = [str(c).strip() if c is not None else "" for c in r]
        if "Referentiecode" in names:
            idx: Dict[str, int] = {}
            for j, n in enumerate(names):
                idx.setdefault(n, j)
            return i, idx
    raise RgsFileError("Geen kolom Referentiecode gevonden: is dit het RGS-bestand van referentiegrootboekschema.nl?")


def parse_xlsx(data: bytes, filename: str = "") -> RgsList:
    """Het officiële RGS-bestand (blad ``Totaal-RGS<versie>-def``, ``MKB-RGS…`` en ``RGS<versie>-versus-…``)."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - elk leesprobleem = geen geldig xlsx
        raise RgsFileError("Dit is geen leesbaar Excel-bestand (xlsx).") from exc
    sheets = {ws.title: ws for ws in wb.worksheets}
    total = next((n for n in sheets if re.match(r"Totaal-RGS\s*\d", n)), None)
    if not total:
        raise RgsFileError("Het blad 'Totaal-RGS…' ontbreekt: is dit het RGS-bestand van referentiegrootboekschema.nl?")
    version = re.search(r"RGS\s*(\d+\.\d+)", total).group(1)
    rows = list(sheets[total].iter_rows(values_only=True))
    h, idx = _header(rows)
    need = ("Referentiecode", "Omschrijving (verkort)", "Omschrijving", "D/C", "Nivo")
    if any(n not in idx for n in need):
        columns = ", ".join(n for n in need if n not in idx)
        raise RgsFileError(f"Kolommen ontbreken in het RGS-bestand: {columns}")

    def cell(r, name):
        j = idx.get(name)
        v = r[j] if j is not None and j < len(r) else None
        return "" if v is None else str(v).strip()
    out = RgsList(version=version, source=filename)
    for r in rows[h + 1:]:
        code = cell(r, "Referentiecode")
        if not code or not re.fullmatch(r"[BW][A-Za-z0-9]*", code):
            continue
        lvl = cell(r, "Nivo")
        out.codes[code] = RgsCode(code=code, short=cell(r, "Omschrijving (verkort)"), long=cell(r, "Omschrijving"),
                                  dc=cell(r, "D/C").upper()[:1], level=int(float(lvl)) if lvl else 0,
                                  sort=cell(r, "Sortering"), number=cell(r, "Referentienummer"),
                                  basis=cell(r, "Basis") not in ("", "0"), inactive=cell(r, "Inactief") not in ("", "0"))
    if len(out.codes) < 100:
        raise RgsFileError("Te weinig RGS-codes gevonden; is dit het volledige RGS-bestand?")
    recap = ""
    if "Recap" in sheets:
        for r in sheets["Recap"].iter_rows(max_row=20, values_only=True):
            recap = next((str(c).strip() for c in r if isinstance(c, str) and c.strip()), "")
            if recap:
                break
    out.prerelease = prerelease_signs(filename, total, recap)
    mkb = next((n for n in sheets if n.startswith("MKB-RGS")), None)
    if mkb:
        mrows = list(sheets[mkb].iter_rows(values_only=True))
        mh, midx = _header(mrows)
        j = midx["Referentiecode"]
        for r in mrows[mh + 1:]:
            c = str(r[j]).strip() if j < len(r) and r[j] else ""
            if c in out.codes:
                out.codes[c].mkb = True
    versus = next((n for n in sheets if re.match(r"RGS\s*\d+\.\d+-versus", n)), None)
    if versus:
        vrows = list(sheets[versus].iter_rows(values_only=True))
        top = [str(c).strip() if c else "" for c in vrows[0]]
        cols = [(j, re.sub(r"[^\d.]", "", t)) for j, t in enumerate(top) if re.fullmatch(r"RGS\s*\d+\.\d+", t)]
        cur = next((j for j, v in cols if v == version), None)
        if cur is not None:
            for j, v in cols:
                if v == version:
                    continue
                m: Dict[str, str] = {}
                for r in vrows[2:]:
                    old = str(r[j]).strip() if j < len(r) and r[j] else ""
                    new = str(r[cur]).strip() if cur < len(r) and r[cur] else ""
                    if old and new and new in out.codes:
                        m.setdefault(old, new)
                out.history[v] = m
    wb.close()
    return out


def store_dir(data_dir: str) -> Path:
    return Path(data_dir).expanduser() / "rgs"


def save_list(data_dir: str, lst: RgsList) -> Path:
    d = store_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rgs-{lst.version}.json"
    p.write_text(lst.to_json(), encoding="utf-8")
    return p


def _version_key(p: Path) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", p.stem))


def list_versions(data_dir: str) -> List[RgsList]:
    """Alle ingelezen lijsten, oudste eerst (v1.0.1 keuze 10: per versie verwijderen)."""
    d = store_dir(data_dir)
    if not d.is_dir():
        return []
    return [RgsList.from_json(p.read_text(encoding="utf-8"))
            for p in sorted(d.glob("rgs-*.json"), key=_version_key)]


def delete_list(data_dir: str, version: str) -> bool:
    """Een ingelezen lijst weghalen; daarna geldt de nieuwste overgebleven versie. De koppelingen in de
    administraties (``x_account_rgs``: rekening, versie, code als tekst) blijven staan; ze worden weer gebruikt als
    die versie opnieuw wordt ingelezen, en dienen als voorstel bij een nieuwere versie (``previous_code``)."""
    if not re.fullmatch(r"\d+(\.\d+)*", version or ""):
        return False
    p = store_dir(data_dir) / f"rgs-{version}.json"
    if not p.is_file():
        return False
    p.unlink()
    return True


def mapped_count(conn: psycopg.Connection, version: str) -> int:
    """Aantal rekeningen van deze administratie met een koppeling aan ``version`` (0 zonder tabel)."""
    with _db.cursor(conn) as cur:
        cur.execute("SELECT to_regclass('x_account_rgs') AS t")
        if not cur.fetchone()["t"]:
            return 0
        cur.execute("SELECT count(*) AS n FROM x_account_rgs WHERE version = %s", (version,))
        return int(cur.fetchone()["n"])


def load_list(data_dir: str, version: Optional[str] = None) -> Optional[RgsList]:
    """De ingelezen lijst (de nieuwste, of ``version``); None als er geen is."""
    d = store_dir(data_dir)
    if not d.is_dir():
        return None
    files = sorted(d.glob("rgs-*.json"), key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.stem)))
    if version:
        files = [p for p in files if p.stem == f"rgs-{version}"]
    return RgsList.from_json(files[-1].read_text(encoding="utf-8")) if files else None


# ------------------------------------------------------------------ voorstellen

_STOP = {"van", "de", "het", "een", "en", "op", "te", "voor", "in", "aan", "met", "uit", "of", "overige", "overig",
         "rekening", "rekeningen", "nog", "bij", "naar", "per"}
#: RGS-groepen per soort rekening (balans): activa, schulden, eigen vermogen
_CAT_PREFIX = {"A": ("BIva", "BMva", "BFva", "BVrd", "BVor", "BEff", "BLim", "BOnd"), "L": ("BLas", "BSch", "BVrz", "BEgv"),
               "Q": ("BEiv", "BEgv")}
_SYN = {
    # balans
    "debiteuren": "vorderingen handelsdebiteuren", "crediteuren": "schulden leveranciers handelscrediteuren",
    "kas": "kasmiddelen", "bank": "banktegoeden kredietinstellingen", "lening": "leningen",
    "hypotheek": "hypothecaire hypotheken", "computers": "computerapparatuur automatisering bedrijfsmiddelen",
    "apparatuur": "bedrijfsmiddelen", "auto": "transportmiddelen personenautos autokosten vervoermiddelen",
    "autos": "transportmiddelen personenautos automobielen", "inventaris": "inventarissen", "gebouwen": "bedrijfsgebouwen",
    "verbouwingen": "verbouwing", "dga": "directeur grootaandeelhouder bestuurders",
    "directeur": "bestuurders", "voorraad": "voorraden", "incourante": "voorziening",
    "kruisposten": "kruisposten", "onderweg": "kruisposten", "vraagposten": "tussenrekeningen",
    "vooruitbetaalde": "vooruitbetaalde facturen overlopende", "ontvangen": "overlopende",
    "nettolonen": "netto lonen", "dividend": "uit keren dividend", "aflossingsverplichting": "aflossingsverplichtingen",
    # btw
    "btw": "omzetbelasting", "hoog": "algemeen tarief", "laag": "verlaagd tarief", "vorderen": "voorbelasting",
    "afdragen": "afdracht",
    # resultaat
    "omzet": "opbrengst netto-omzet", "inkoop": "inkoopwaarde kostprijs", "inkoopwaarde": "handelsgoederen",
    "loon": "lonen salarissen", "lonen": "salarissen", "salaris": "lonen salarissen bezoldiging",
    "vakantiegeld": "vakantiebijslag", "reiskostenvergoeding": "reiskostenvergoeding",
    "afschrijving": "afschrijvingen", "kantoor": "kantoorkosten", "huur": "huisvestingskosten",
    "rente": "rentelasten rentebaten", "privé": "prive",
    "vervoerskosten": "autokosten vervoermiddelen", "brandstof": "brandstofkosten", "leasekosten": "leasing",
    "telefoon": "telefoonkosten", "internet": "telefoonkosten", "automatiseringskosten": "kosten automatisering",
    "accountantskosten": "accountants advieskosten", "advieskosten": "overige advieskosten",
    "notaris": "notariskosten", "juridische": "advieskosten", "verzekering": "assurantiepremies assurantiekosten",
    "verzekeringen": "assurantiepremies assurantiekosten", "bedrijfsverzekeringen": "assurantiekosten bedrijfsaansprakelijkheidsverzekering",
    "onroerendezaakbelasting": "onroerende zaakbelasting", "vennootschapsbelasting": "belastingen winst",
    "koersverschillen": "valutakoersverschillen", "afrondingsverschillen": "boekingsverschillen",
    "boetes": "boetes verhogingen", "betalingskorting": "betalingskortingen", "rentebaten": "rentebaten",
    "rentelasten": "rentelasten",
}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t)


def _words(text: str) -> set:
    ws = {w for w in _norm(text.replace("'", "")).split() if len(w) > 2 and w not in _STOP}
    for w in list(ws):
        if w in _SYN:
            ws |= set(_SYN[w].split())
    return ws


def _link_prefixes(acc: Dict[str, Any]) -> List[str]:
    link = set(filter(None, (acc.get("link") or "").split(":")))
    d = _norm(acc.get("description", ""))
    out: List[str] = []
    if "AR" in link:
        out.append("BVorDeb")
    if "AP" in link:
        out.append("BSchCre")
    if link & {"AR_paid", "AP_paid"}:
        out.append("BLimKas" if re.search(r"\bkas\b", d) else "BLimBan")
    if link & {"AR_tax", "AP_tax"}:
        out.append("BSchBep")
    if "IC" in link:
        out.append("BVrd")
    if link & {"IC_cogs"}:
        out.append("WKpr")
    if link & {"IC_sale", "IC_income", "AR_amount"} and acc.get("category") == "I":
        out.append("WOmz")
    return out


def suggest(acc: Dict[str, Any], lst: RgsList, n: int = 3, previous: Optional[Tuple[str, str]] = None) -> List[RgsCode]:
    """Tot ``n`` voorstellen voor een rekening (dict met accno, description, category, charttype, link).
    ``previous``: (versie, code) van een eerdere koppeling."""
    heading = acc.get("charttype") == "H"
    cat = acc.get("category") or ""
    side = "W" if cat in ("I", "E") else "B"
    dc = {"A": "D", "E": "D", "L": "C", "Q": "C", "I": "C"}.get(cat, "")
    words = _words(acc.get("description", ""))
    contra = bool(acc.get("contra"))
    if cat == "L" and (contra or words & {"afschrijving", "afschrijvingen", "cumulatieve"}):
        cat, dc, contra = "A", "C", True        # afschrijving als passiefpost (oude SL-schema's): tegenrekening activa
    prefixes = _link_prefixes(acc)
    scores: Dict[str, float] = {}
    if previous:
        old_version, old_code = previous
        mapped = lst.history.get(old_version, {}).get(old_code) or (old_code if old_code in lst.codes else None)
        if mapped:
            scores[mapped] = 1000.0
    for c in lst.codes.values():
        if c.inactive or not c.code.startswith(side):
            continue
        if heading != (c.level <= 3):
            continue
        if side == "B" and cat in _CAT_PREFIX and not c.code.startswith(_CAT_PREFIX[cat]):
            continue
        s = 0.0
        if _norm(c.short).strip() == _norm(acc.get("description", "")).strip() or \
                _norm(c.long).strip() == _norm(acc.get("description", "")).strip():
            s += 10.0
        if contra or "cumulatieve" in words or "afschrijving" in words and cat == "A":
            s += 3.0 if c.code.endswith(("Cae", "Cuh")) else -3.0
        if dc and c.dc == dc:
            s += 1.0
        for i, p in enumerate(prefixes):
            if c.code.startswith(p):
                s += 8.0 - i
        cw = _words(c.short + " " + c.long)
        common = words & cw
        s += 4.0 * len(common)
        s += sum(2.0 for w in words - common if len(w) > 3 and any(v[:5] == w[:5] for v in cw if len(v) > 3))
        if not common and not any(c.code.startswith(p) for p in prefixes):
            continue
        s += 0.5 if c.mkb else 0.0
        s += 0.3 if c.basis else 0.0
        s -= 0.1 * max(0, c.level - 4)
        scores[c.code] = max(scores.get(c.code, 0.0), s)
    best = sorted(scores.items(), key=lambda kv: (-kv[1], lst.codes[kv[0]].sort or kv[0]))
    return [lst.codes[k] for k, _ in best[:n] if k in lst.codes]


# ------------------------------------------------------------------ koppeling per administratie

class RgsMapping:
    """Gekozen RGS-codes van een administratie (``x_account_rgs``)."""

    def __init__(self, conn: psycopg.Connection, login: str = ""):
        from .extensions import ensure_schema
        ensure_schema(conn)
        self.conn = conn
        self.login = login

    def accounts(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT id, accno, description, category, charttype, link, coalesce(closed, false) AS closed
                           FROM chart ORDER BY accno""")
            return cur.fetchall()

    def codes(self) -> Dict[int, Dict[str, str]]:
        """Per rekening: {versie: code}."""
        out: Dict[int, Dict[str, str]] = {}
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT chart_id, version, code FROM x_account_rgs")
            for r in cur.fetchall():
                out.setdefault(r["chart_id"], {})[r["version"]] = r["code"]
        return out

    def save(self, version: str, choices: Dict[int, Optional[str]], lst: RgsList) -> int:
        """``choices``: rekening → code (None = koppeling weg). Alleen codes uit de lijst. Geeft het aantal wijzigingen."""
        current = self.codes()
        changed = 0
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for chart_id, code in choices.items():
                old = current.get(chart_id, {}).get(version)
                if (code or None) == old:
                    continue
                if code and code not in lst.codes:
                    raise ValidationError(f"RGS-code {code} bestaat niet in RGS {version}.")
                if code:
                    cur.execute("""INSERT INTO x_account_rgs (chart_id, version, code, login) VALUES (%s, %s, %s, %s)
                                   ON CONFLICT (chart_id, version) DO UPDATE SET code = excluded.code,
                                          login = excluded.login, updated = now()""",
                                (chart_id, version, code, self.login))
                else:
                    cur.execute("DELETE FROM x_account_rgs WHERE chart_id = %s AND version = %s", (chart_id, version))
                changed += 1
        return changed


def previous_code(codes: Dict[str, str], version: str) -> Optional[Tuple[str, str]]:
    """De koppeling met de nieuwste oudere versie, als er voor ``version`` nog geen is."""
    older = sorted((v for v in codes if v != version), key=lambda v: tuple(int(x) for x in v.split(".")))
    return (older[-1], codes[older[-1]]) if older else None
