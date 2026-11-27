"""HTML van documenten wegschrijven om twee versies van de standaardsjablonen te vergelijken (fase 4 stap 3).

Draai met de code van de ene versie (PYTHONPATH) en daarna van de andere, en vergelijk de mappen:

    PYTHONPATH=<checkout-oud> python dev/doc_html_dump.py --db adm_a --out /tmp/oud
    PYTHONPATH=<checkout-nieuw> python dev/doc_html_dump.py --db adm_a --out /tmp/nieuw
    python dev/doc_html_dump.py --diff /tmp/oud /tmp/nieuw

Alleen lezen: de printgegevens worden opgehaald zoals bij de knop PDF, maar er wordt niets gemarkeerd.
Witruimte naast tags en het stijlblok tellen bij het vergelijken niet mee; tekst en opbouw wel.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import tempfile
from pathlib import Path


def dump(db: str, out: Path, per_kind: int, languages: list[str]) -> int:
    from sqlledger import UserConfig, connect
    from sqlledger.printdata import PrintData
    from sqlledger_web import doctypes
    from sqlledger_web.documents import DocumentRenderer

    out.mkdir(parents=True, exist_ok=True)
    config = UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy")
    n = 0
    data_dir = tempfile.mkdtemp()
    with connect(dbname=db, host="127.0.0.1", port=55432, user="postgres") as conn:
        pd = PrintData(conn, config, username="Controle", useremail="controle@example.org")
        for key, kind in doctypes.KINDS.items():
            if kind.source == "order" or key in doctypes.FROM_ORDER:
                quo = kind.key in ("sales_quotation", "request_quotation")
                vc_col = "customer_id" if kind.vc == "customer" else "vendor_id"
                ids = [r[0] for r in conn.execute(
                    f"SELECT id FROM oe WHERE {vc_col} > 0 AND quotation = %s ORDER BY id DESC LIMIT %s",
                    (quo, per_kind)).fetchall()]
            else:
                ids = []
            if kind.source != "order":
                table = "ar" if kind.vc == "customer" else "ap"
                negative = key in ("credit_invoice", "debit_invoice", "credit_note", "debit_note")
                ids += [r[0] for r in conn.execute(
                    f"SELECT id FROM {table} WHERE invoice = %s AND (amount < 0) = %s ORDER BY id DESC LIMIT %s",
                    (kind.source == "invoice", negative, per_kind)).fetchall()]
            for trans_id in ids:
                loc = doctypes.locate(conn, key, trans_id)
                if loc is None:
                    continue
                v = doctypes.print_vars(pd, loc)
                conn.rollback()
                for lang in languages:
                    html = DocumentRenderer(data_dir, None, lang).render_html(key, dict(v, language_code=""))
                    (out / f"{db}-{key}-{trans_id}-{lang}.html").write_text(html, encoding="utf-8")
                    n += 1
    return n


def synthetic(db: str, out: Path) -> int:
    """Echte documenten aangevuld met wat de steekproef mist: groepskop, subtotaal, stuklijstregel, locatie,
    korting, verpakking, gewicht, alle velden per regel; ook offerte(aanvraag) en pakbon/picklijst uit een order."""
    from sqlledger import UserConfig, connect
    from sqlledger.printdata import LINE_LISTS, ORDER_LINE_LISTS, PrintData
    from sqlledger_web import doctypes
    from sqlledger_web.documents import DocumentRenderer

    out.mkdir(parents=True, exist_ok=True)
    config = UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy")
    keys = tuple(dict.fromkeys(LINE_LISTS + ORDER_LINE_LISTS + ("toship",)))

    def extend(v: dict) -> dict:
        v = dict(v)
        n = len(v.get("description") or [])
        for k in keys:
            lst = list(v.get(k) or [""] * n)
            lst += [""] * (n - len(lst))
            v[k] = lst
        extra = [
            {"description": "Groep A"},
            {"runningnumber": "9", "number": "X-9", "description": "Met alles", "qty": "3", "unit": "st",
             "sellprice": "1,50", "discountrate": "10", "linetotal": "4,05", "bin": "B-12", "barcode": "8712345678906",
             "hscode": "19053199", "countryorigin": "NL", "projectnumber": "P1", "deliverydate": "01-02-26",
             "requiredate": "01-02-26", "serialnumber": "THT 2026", "package": "2", "grossweight": "0,5",
             "netweight": "1,5", "ship": "1", "toship": "2", "itemnotes": "regel 1\nregel 2"},
            {"bom": "1", "number": "C-1", "description": "Onderdeel", "qty": "6", "unit": "st", "bin": "B-13"},
            {"description": "Subtotaal groep A", "linetotal": "4,05"},
        ]
        for row in extra:
            for k in keys:
                v[k].append(row.get(k, ""))
        return v

    n = 0
    with connect(dbname=db, host="127.0.0.1", port=55432, user="postgres") as conn:
        pd = PrintData(conn, config, username="Controle", useremail="controle@example.org")
        base = {}
        for src in ("invoice", "vendor_invoice", "sales_order", "purchase_order"):
            loc = doctypes.find_document(conn, src)
            if loc is not None:
                base[src] = doctypes.print_vars(pd, loc)
                conn.rollback()
        plan = [("invoice", "invoice"), ("credit_invoice", "invoice"), ("packing_list", "invoice"),
                ("pick_list", "invoice"), ("vendor_invoice", "vendor_invoice"), ("debit_invoice", "vendor_invoice"),
                ("sales_order", "sales_order"), ("sales_quotation", "sales_order"), ("packing_list", "sales_order"),
                ("pick_list", "sales_order"), ("purchase_order", "purchase_order"),
                ("request_quotation", "purchase_order")]
        data_dir = tempfile.mkdtemp()
        for key, src in plan:
            if src not in base:
                continue
            v = extend(base[src])
            if key.endswith("quotation"):
                v = dict(v, quodate=v.get("orddate", ""), quonumber="Q-1")
            html = DocumentRenderer(data_dir, None, "nl").render_html(key, dict(v, language_code=""))
            (out / f"syn-{db}-{key}-uit-{src}.html").write_text(html, encoding="utf-8")
            n += 1
    return n


def normalize(text: str) -> list[str]:
    """Witruimte naast tags telt niet (Jinja-opmaak); het stijlblok ook niet (alleen nieuwe regels erbij,
    na te gaan met git diff van _base.html)."""
    text = re.sub(r"<style>.*?</style>", "<style/>", text, flags=re.S)
    text = re.sub(r"\s*(<[^>]*>)\s*", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.replace("><", ">\n<").split("\n")


def diff(a: Path, b: Path) -> int:
    names = sorted({p.name for p in a.glob("*.html")} | {p.name for p in b.glob("*.html")})
    bad = 0
    for name in names:
        pa, pb = a / name, b / name
        if not pa.exists() or not pb.exists():
            print(f"ontbreekt: {name} ({'oud' if not pa.exists() else 'nieuw'})")
            bad += 1
            continue
        la, lb = normalize(pa.read_text(encoding="utf-8")), normalize(pb.read_text(encoding="utf-8"))
        if la != lb:
            bad += 1
            if bad <= 5:
                print(f"--- verschil in {name}")
                for line in list(difflib.unified_diff(la, lb, lineterm="", n=1))[:30]:
                    print(line)
    print(f"{len(names)} documenten, {bad} met verschil")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", action="append", default=[])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--per-kind", type=int, default=25)
    ap.add_argument("--lang", action="append", default=[])
    ap.add_argument("--diff", nargs=2, type=Path)
    ap.add_argument("--synthetic", action="store_true", help="aangevulde documenten (zie synthetic)")
    a = ap.parse_args()
    if a.diff:
        return diff(*a.diff)
    if a.synthetic:
        total = sum(synthetic(db, a.out) for db in a.db)
        print(f"{total} documenten geschreven naar {a.out}")
        return 0
    total = sum(dump(db, a.out, a.per_kind, a.lang or ["nl", "en"]) for db in a.db)
    print(f"{total} documenten geschreven naar {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
