"""Import (fase 4 stap 7) in de Perl-vergelijking: Python-tegenhanger van ``im_ui`` in perl_driver.pl (blok "im").
Perl speelt im.pl na (bestand inlezen, rijen aanvinken, importeren); hier hetzelfde bestand via
``sqlledger.importer`` (proefrun, dan importeren van de gekozen rijen; kolommen = SL-veldnamen)."""

from __future__ import annotations

from typing import Any, Dict

from sqlledger.importer import ImportFailed, Importer, read_file


def im_ui_py(conn, cfg, a: Dict[str, Any]) -> Dict[str, Any]:
    header, rows = read_file(a["csv"].encode("utf-8"), "import.csv")
    if a["type"] == "payment" and (a.get("extra") or {}).get("paymentaccount") and "accno" not in header:
        # SL kiest de betaalrekening op het scherm; hier als kolom
        acc = a["extra"]["paymentaccount"].split("--")[0]
        header, rows = header + ["accno"], [r + [acc] for r in rows]
    imp = Importer(conn, cfg, a["type"], cfg.numberformat, cfg.dateformat)
    chosen = a.get("rows") or list(range(1, len(rows) + 1))
    # geen proefrun vooraf: die verbruikt volgnummers (sequences), en dan verschillen de id's met Perl; wel één id
    # verbruiken zoals SL's rij in ``report`` (IM.pm delete_import, zelfde reeks ``id``)
    conn.execute("SELECT nextval('id')")
    try:
        done = imp.run(header, rows, chosen)
    except ImportFailed as exc:
        return {"refused": str(exc)}
    return {"added": done.added, "updated": done.updated}
