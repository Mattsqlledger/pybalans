"""Voorbeeld-PDF's van rapporten maken en de tijd meten (fase 4 stap 5; uitvoer niet in git).

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/voorbeeld_rapport_pdf.py <db> <uitmap> naam:rapport?param=waarde&... [...]
           [--taal en] [--numberformat 1.000,00] [--dateformat dd-mm-yy]

Zelfde weg als de knop PDF: ``REPORTS[rapport].run`` met de zoekvelden uit de url, daarna
``reportpdf.render_pdf``. Per rapport: regels, pagina's, staand/liggend, lettergrootte en tijd.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from starlette.datastructures import QueryParams  # noqa: E402

from sqlledger import UserConfig, connect  # noqa: E402
from sqlledger._db import cursor, get_defaults  # noqa: E402
from sqlledger_web import i18n, reportpdf  # noqa: E402
from sqlledger_web.reportdefs import REPORTS, Fmt, Lib, default_values, parse_values  # noqa: E402
from sqlledger_web.tables import cell_text  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("out")
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--taal", default="nl")
    ap.add_argument("--numberformat", default="1.000,00")
    ap.add_argument("--dateformat", default="dd-mm-yy")
    a = ap.parse_args()
    i18n.current_language.set(a.taal)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = UserConfig(login="admin", numberformat=a.numberformat, dateformat=a.dateformat, countrycode=a.taal)
    fmt = Fmt(a.numberformat, a.dateformat)
    with connect(dbname=a.db, host="127.0.0.1", port=55432, user="postgres") as conn:
        with cursor(conn) as cur:
            company = get_defaults(cur, "company").get("company") or a.db
        lib = Lib(conn, cfg)
        for spec in a.reports:
            name, _, rest = spec.partition(":")
            key, _, query = rest.partition("?")
            rep = REPORTS[key]
            q = QueryParams(parse_qsl(query, keep_blank_values=True))
            values, errors = parse_values(rep.fields, q, default_values(rep.fields, date.today()), True)
            if errors:
                print(f"{name}: {errors}")
                continue
            t0 = time.time()
            result = rep.run(lib, values, fmt)
            t1 = time.time()
            rows = sum(len(t.rows) for t in result.tables)
            texts = [[[cell_text(c, r, fmt.nf, fmt.df) for c in t.columns] for r in t.rows] for t in result.tables]
            lay = reportpdf.choose_layout(result.tables, texts)
            try:
                pdf = reportpdf.render_pdf(result.tables, i18n.gettext(rep.title), [result.subtitle] + result.info,
                                           company, fmt.nf, fmt.df)
            except reportpdf.PdfTooLarge as exc:
                print(f"{name}: {rows} regels → melding: {exc.message()}")
                continue
            t2 = time.time()
            (out / f"{name}.pdf").write_bytes(pdf)
            from io import BytesIO
            from pypdf import PdfReader
            pages = len(PdfReader(BytesIO(pdf)).pages)
            print(f"{name}: {rows} regels, {pages} blz., {'liggend' if lay.landscape else 'staand'} "
                  f"{lay.font_pt} pt, rapport {t1 - t0:.1f} s + pdf {t2 - t1:.1f} s, {len(pdf) // 1024} kB")


if __name__ == "__main__":
    main()
