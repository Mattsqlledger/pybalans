"""Voorbeeld-PDF's van documenten maken (fase 4, ter beoordeling; niet in git).

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/voorbeeld_pdf.py <db> <uitmap> soort:id [soort:id ...] [--taal en] [--variant X]
           [--instellingen instellingen.json]

``--instellingen``: documentinstellingen uit een bestand in plaats van de database (zoals ``x_doc_setting``:
sleutel -> waarde; een object als waarde wordt als JSON opgeslagen), om een instelling te proberen zonder
iets in de administratie te schrijven.

Gebruikt dezelfde weg als de knop PDF (doctypes.locate + print_vars + standaardsjablonen), zonder de
statustabel te wijzigen.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import json  # noqa: E402

from sqlledger import UserConfig, connect, docsettings  # noqa: E402
from sqlledger.printdata import PrintData  # noqa: E402
from sqlledger_web import doctypes  # noqa: E402
from sqlledger_web.documents import DocumentRenderer  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("out")
    ap.add_argument("docs", nargs="+")
    ap.add_argument("--taal", default="nl")
    ap.add_argument("--variant")
    ap.add_argument("--numberformat", default="1.000,00")
    ap.add_argument("--dateformat", default="dd-mm-yy")
    ap.add_argument("--instellingen")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = UserConfig(login="admin", numberformat=a.numberformat, dateformat=a.dateformat, countrycode="nl")
    with connect(dbname=a.db, host="127.0.0.1", port=55432, user="postgres") as conn:
        if a.instellingen:
            raw = json.loads(Path(a.instellingen).read_text(encoding="utf-8"))
            settings = docsettings.DocSettings.from_rows(
                {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in raw.items()})
        else:
            settings = docsettings.load(conn)
        r = DocumentRenderer(out / "_geen_eigen_sjablonen", None, a.taal, settings=settings,
                             banks=docsettings.bank_accounts(conn))
        pd = PrintData(conn, cfg, username="Voorbeeld", useremail="voorbeeld@example.com")
        for d in a.docs:
            key, _, tid = d.partition(":")
            loc = doctypes.locate(conn, key, int(tid))
            if loc is None:
                print(f"{d}: soort past niet bij dit document")
                continue
            v = doctypes.print_vars(pd, loc, a.variant)
            name = out / f"{a.db}-{key}-{loc.number or tid}-{a.taal}.pdf".replace("/", "_")
            name.write_bytes(r.render_pdf(key, v))
            print(f"{d}: {name.name}")


if __name__ == "__main__":
    main()
