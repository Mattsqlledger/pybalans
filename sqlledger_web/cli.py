"""Opdrachtregel: ``pybalans`` (of ``sqlledger-web``, zelfde opdracht) ``init-db | create-admin | serve | translations | backup``.

``init-db`` en ``create-admin`` zijn sinds de eerste-startwizard (``/welkom``) niet meer nodig bij de installatie:
de server maakt de app-database zelf aan. Ze blijven als noodroute (beheerder kwijt, scripts).
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import accounts, appdb
from .settings import Settings


def _conn(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(settings.app_conninfo(), autocommit=True, row_factory=dict_row)


def cmd_init_db(settings: Settings, args) -> int:
    created = appdb.create_database(settings.app_conninfo())
    with _conn(settings) as conn:
        version = appdb.migrate(conn)
    print(f"App-database {'aangemaakt' if created else 'bestond al'}; schemaversie {version}.")
    return 0


def cmd_create_admin(settings: Settings, args) -> int:
    """Systeembeheerder aanmaken (of bestaande een nieuwe code geven)."""
    appdb.create_database(settings.app_conninfo())
    with _conn(settings) as conn:
        appdb.migrate(conn)
        user = accounts.get_user(conn, login=args.login)
        if user is None:
            try:
                user_id = accounts.create_user(conn, args.login, args.name or "", is_sysadmin=True)
            except accounts.AccountError as exc:
                print(exc, file=sys.stderr)
                return 1
            accounts.log_event(conn, "user_created", login=args.login.lower(), via="cli",
                               sysadmin=True)
        else:
            if not user["is_sysadmin"]:
                print(f"'{user['login']}' bestaat al en is geen systeembeheerder.", file=sys.stderr)
                return 1
            user_id = user["id"]
        code = accounts.issue_setup_code(conn, settings, user_id, None)
        accounts.log_event(conn, "code_created", login=args.login.lower(), via="cli")
    print(f"Eenmalige code voor {args.login.lower()}: {code}")
    print(f"Geldig {settings.setup_code_hours} uur. Ga naar /activeren om een wachtwoord te kiezen;")
    print("daarna is tweestapsverificatie verplicht.")
    return 0


def cmd_serve(settings: Settings, args) -> int:
    import uvicorn
    if args.config:  # create_app leest de instellingen zelf opnieuw in
        os.environ["SQLLEDGER_CONFIG"] = os.path.abspath(args.config)
    # adres in de melding met de startcode (eerste start); achter een proxy zelf zetten
    os.environ.setdefault("SQLLEDGER_PUBLIC_URL", f"http://{args.host}:{args.port}")
    uvicorn.run("sqlledger_web.main:create_app", factory=True, host=args.host, port=args.port,
                proxy_headers=args.proxy, forwarded_allow_ips=args.forwarded_allow_ips)
    return 0


def cmd_translations(settings: Settings, args) -> int:
    """Eigen woorden (TL9): ``<data_dir>/vertalingen/<taal>.txt`` controleren, of alle teksten als startpunt tonen."""
    from . import overrides
    if args.starter:
        sys.stdout.write(overrides.starter(args.language))
        return 0
    path = overrides.path_for(settings.data_dir, args.language)
    loaded = overrides.read(settings.data_dir, args.language)
    if loaded is None:
        print(f"{path}: bestaat niet (geen eigen woorden). Startpunt: pybalans translations {args.language} --starter")
        return 0
    for p in loaded.problems:
        print(f"{path}: {p}")
    n = len(loaded.texts) + sum(len(d) for d in loaded.contexts.values())
    print(f"{path}: {n} eigen tekst(en) in gebruik, {len(loaded.problems)} probleem/problemen")
    return 1 if loaded.problems else 0


def cmd_backup(settings: Settings, args) -> int:
    """Back-up van administraties (database + sjablonen) en de app-database in een map (6d-1, I7); om in te plannen.
    Gaat door bij een fout en eindigt dan met code 1."""
    from pathlib import Path

    from sqlledger import dbbackup
    from sqlledger.errors import LedgerError

    from . import backups
    folder = Path(args.dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    failed = 0
    with _conn(settings) as conn:
        appdb.migrate(conn)
        rows = conn.execute("SELECT * FROM company ORDER BY id").fetchall()
        if args.company:
            wanted = {c.lower() for c in args.company}
            rows = [r for r in rows if {str(r["id"]), r["name"].lower(), (r["code"] or "").lower(),
                                        r["dbname"].lower()} & wanted]
            missing = wanted - {v for r in rows for v in (str(r["id"]), r["name"].lower(),
                                                           (r["code"] or "").lower(), r["dbname"].lower())}
            for m in sorted(missing):
                print(f"Administratie niet gevonden: {m}", file=sys.stderr)
                failed += 1
        elif not args.inactive:
            rows = [r for r in rows if r["active"]]
        for r in rows:
            try:
                res = backups.dump_company(settings, r, folder)
            except (LedgerError, psycopg.Error) as exc:
                msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                print(f"{r['name']}: MISLUKT – {msg}", file=sys.stderr)
                accounts.log_event(conn, "backup_failed", login="cli", company_id=r["id"], target=r["name"],
                                   dbname=r["dbname"], error=msg[:300], via="cli")
                failed += 1
                continue
            backups.record(conn, r["id"])
            accounts.log_event(conn, "backup_made", login="cli", company_id=r["id"], target=r["name"],
                               dbname=r["dbname"], kind="database", filename=res.path.name, size=res.size,
                               version=res.version, via="cli")
            print(f"{r['name']}: {res.path} ({res.size:,} bytes)".replace(",", "."))
            data = backups.templates_zip(settings, r["id"])
            if data is not None:
                zpath = folder / dbbackup.filename(r["dbname"], res.made, backups.TEMPLATES_SUFFIX)
                zpath.write_bytes(data)
                print(f"{r['name']}: {zpath}")
            if args.keep:
                for suffix in (".sql.gz", backups.TEMPLATES_SUFFIX):
                    dbbackup.rotate(folder, r["dbname"], args.keep, suffix)
        if not args.no_app_db:
            try:
                res = backups.dump_app(settings, folder)
            except (LedgerError, psycopg.Error) as exc:
                print(f"app-database: MISLUKT – {exc}", file=sys.stderr)
                failed += 1
            else:
                print(f"app-database: {res.path} ({res.size:,} bytes)".replace(",", "."))
                if args.keep:
                    dbbackup.rotate(folder, conninfo_to_dict(settings.app_conninfo())["dbname"], args.keep)
    return 1 if failed else 0


def main(argv=None) -> int:
    called = os.path.basename(sys.argv[0])           # pybalans of de oude naam sqlledger-web (alias)
    p = argparse.ArgumentParser(prog=called if argv is None and called == "sqlledger-web" else "pybalans",
                                description="PyBalans – boekhouding in Python op PostgreSQL")
    p.add_argument("--config", help="TOML-bestand (anders SQLLEDGER_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="app-database aanmaken/bijwerken")
    a = sub.add_parser("create-admin", help="systeembeheerder aanmaken + eenmalige code")
    a.add_argument("login")
    a.add_argument("--name", default="")
    s = sub.add_parser("serve", help="webserver starten")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--proxy", action="store_true", help="X-Forwarded-For vertrouwen (achter proxy)")
    s.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    t = sub.add_parser("translations", help="eigen woorden (<data_dir>/vertalingen/<taal>.txt) controleren")
    t.add_argument("language", choices=["nl", "en", "de", "fr"])
    t.add_argument("--starter", action="store_true", help="alle teksten als commentaarregels afdrukken (startpunt)")
    b = sub.add_parser("backup", help="back-up van administraties en app-database in een map (om in te plannen)")
    b.add_argument("--dir", required=True, help="doelmap (wordt aangemaakt)")
    b.add_argument("--company", action="append", help="alleen deze administratie (id, naam, code of database); "
                                                       "mag vaker; standaard alle actieve")
    b.add_argument("--inactive", action="store_true", help="ook administraties die uit staan")
    b.add_argument("--keep", type=int, default=0, help="alleen de laatste N back-ups per administratie bewaren")
    b.add_argument("--no-app-db", action="store_true", help="de app-database niet meenemen")
    args = p.parse_args(argv)
    settings = Settings.load(args.config)
    return {"init-db": cmd_init_db, "create-admin": cmd_create_admin,
            "serve": cmd_serve, "translations": cmd_translations, "backup": cmd_backup}[args.cmd](settings, args)


if __name__ == "__main__":
    sys.exit(main())
