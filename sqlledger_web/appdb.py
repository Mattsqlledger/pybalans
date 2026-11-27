"""Eigen app-database: gebruikers, administraties, rechten, sessies en logboek.

Losstaand van de SQL-Ledger-administraties; het schema wordt bij het starten
bijgewerkt (``migrate``).
"""

from __future__ import annotations

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import ConnectionPool

from .i18n import N_

#: modules die per administratie aan/uit kunnen (volgorde = menuvolgorde)
MODULES = {
    "ar": N_("Verkoop"),
    "ap": N_("Inkoop"),
    "cash": N_("Kas en bank"),
    "oe": N_("Orders"),
    "quotations": N_("Offertes"),
    "ic": N_("Artikelen"),
    "gl": N_("Grootboek"),
    "reports": N_("Rapporten"),
    "recurring": N_("Terugkerend"),
    "projects": N_("Afdelingen en projecten"),
}

#: rollen per administratie, van weinig naar veel rechten
#: rollen van laag naar hoog; urenschrijver (UREN_ANALYSE.md UR4) ziet alleen zijn eigen maandstaat
ROLES = {"hours": N_("Urenschrijver"), "readonly": N_("Alleen lezen"), "bookkeeper": N_("Boekhouder"),
         "admin": N_("Beheerder")}
ROLE_LEVEL = {name: i for i, name in enumerate(ROLES)}

#: zoveel recent gebruikte administraties in het keuzemenu (AW1/AW4), zoekveld vanaf zoveel administraties
RECENT_COMPANIES = 5
SEARCH_FROM = 8

MIGRATIONS = [
    # 1: basis
    """
    CREATE TABLE app_user (
        id            serial PRIMARY KEY,
        login         text NOT NULL UNIQUE CHECK (login = lower(login) AND login <> ''),
        name          text NOT NULL DEFAULT '',
        email         text NOT NULL DEFAULT '',
        password_hash text,
        is_sysadmin   boolean NOT NULL DEFAULT false,
        active        boolean NOT NULL DEFAULT true,
        totp_secret   text,
        totp_last_step bigint,
        numberformat  text NOT NULL DEFAULT '1.000,00',
        dateformat    text NOT NULL DEFAULT 'dd-mm-yy',
        language      text NOT NULL DEFAULT 'nl',
        failed_count  integer NOT NULL DEFAULT 0,
        locked_until  timestamptz,
        created       timestamptz NOT NULL DEFAULT now(),
        last_login    timestamptz
    );
    CREATE TABLE setup_code (
        id         serial PRIMARY KEY,
        user_id    integer NOT NULL REFERENCES app_user ON DELETE CASCADE,
        code_hash  text NOT NULL UNIQUE,
        expires    timestamptz NOT NULL,
        used       timestamptz,
        created_by integer REFERENCES app_user ON DELETE SET NULL,
        created    timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE company (
        id      serial PRIMARY KEY,
        name    text NOT NULL UNIQUE,
        dbname  text NOT NULL,
        dbhost  text,
        dbport  integer,
        modules text[] NOT NULL DEFAULT '{}',
        active  boolean NOT NULL DEFAULT true
    );
    CREATE TABLE membership (
        user_id        integer NOT NULL REFERENCES app_user ON DELETE CASCADE,
        company_id     integer NOT NULL REFERENCES company ON DELETE CASCADE,
        role           text NOT NULL CHECK (role IN ('readonly', 'bookkeeper', 'admin')),
        employee_login text NOT NULL,
        PRIMARY KEY (user_id, company_id)
    );
    CREATE TABLE app_session (
        token_hash   text PRIMARY KEY,
        user_id      integer NOT NULL REFERENCES app_user ON DELETE CASCADE,
        stage        text NOT NULL CHECK (stage IN ('totp', 'totp_setup', 'full')),
        company_id   integer REFERENCES company ON DELETE SET NULL,
        csrf         text NOT NULL,
        pending_totp text,
        ip           text,
        user_agent   text,
        created      timestamptz NOT NULL DEFAULT now(),
        last_seen    timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX app_session_user ON app_session (user_id);
    CREATE TABLE audit_log (
        id         bigserial PRIMARY KEY,
        at         timestamptz NOT NULL DEFAULT now(),
        user_id    integer REFERENCES app_user ON DELETE SET NULL,
        login      text,
        company_id integer REFERENCES company ON DELETE SET NULL,
        action     text NOT NULL,
        ip         text,
        detail     jsonb
    );
    CREATE INDEX audit_log_at ON audit_log (at);
    CREATE INDEX audit_log_ip_action ON audit_log (ip, action, at);
    """,
    # 2: laatst gebruikte rapportinstellingen (R10)
    """
    CREATE TABLE report_pref (
        user_id    integer NOT NULL REFERENCES app_user ON DELETE CASCADE,
        company_id integer NOT NULL REFERENCES company ON DELETE CASCADE,
        report     text NOT NULL,
        params     jsonb NOT NULL,
        updated    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, company_id, report)
    );
    """,
    # 3: administratie op slot (beheer A7; Perl: users/<db>.LCK)
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS locked boolean NOT NULL DEFAULT false;
    ALTER TABLE company ADD COLUMN IF NOT EXISTS lock_message text NOT NULL DEFAULT '';
    """,
    # 4: documenten (fase 4, D3/D6): taal van de standaardsjablonen per administratie
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS doc_language text NOT NULL DEFAULT 'nl'
        CHECK (doc_language IN ('nl', 'en'));
    """,
    # 5: mailen (fase 4, D9): SMTP en standaardteksten per administratie, handtekening per gebruiker
    """
    CREATE TABLE company_mail (
        company_id   integer PRIMARY KEY REFERENCES company ON DELETE CASCADE,
        mode         text NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'smtp', 'folder')),
        host         text NOT NULL DEFAULT '',
        port         integer NOT NULL DEFAULT 587 CHECK (port BETWEEN 1 AND 65535),
        security     text NOT NULL DEFAULT 'starttls' CHECK (security IN ('starttls', 'ssl', 'none')),
        username     text NOT NULL DEFAULT '',
        password_enc text NOT NULL DEFAULT '',
        from_name    text NOT NULL DEFAULT '',
        from_address text NOT NULL DEFAULT '',
        reply_to     text NOT NULL DEFAULT '',
        texts        jsonb NOT NULL DEFAULT '{}',
        updated      timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE app_user ADD COLUMN IF NOT EXISTS mail_signature text NOT NULL DEFAULT '';
    """,
    # 6: documenttaal ook Duits en Frans (fase 4, DS10/DT2)
    """
    ALTER TABLE company DROP CONSTRAINT IF EXISTS company_doc_language_check;
    ALTER TABLE company ADD CONSTRAINT company_doc_language_check CHECK (doc_language IN ('nl', 'en', 'de', 'fr'));
    """,
    # 7: btw-aangifte in het menu (BA2/BA8): alleen als er rubrieken zijn ingesteld en het tijdvak niet "geen" is;
    # bijgehouden bij opslaan van de rubrieken en de instellingen (het menu leest de administratie niet)
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS vat_return boolean NOT NULL DEFAULT false;
    """,
    # 8: ICP-opgaaf in het menu (ICP1): zodra een btw-rekening of een klant op rubriek 3B staat; bijgehouden zoals 7
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS icp boolean NOT NULL DEFAULT false;
    """,
    # 9: administratie wisselen (AW4/AW5): korte code per administratie (uniek als ingevuld) en recent gebruikt
    # per gebruiker
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS code text NOT NULL DEFAULT '';
    CREATE UNIQUE INDEX IF NOT EXISTS company_code ON company (lower(code)) WHERE code <> '';
    CREATE TABLE IF NOT EXISTS recent_company (
        user_id    integer NOT NULL REFERENCES app_user ON DELETE CASCADE,
        company_id integer NOT NULL REFERENCES company ON DELETE CASCADE,
        used       timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, company_id)
    );
    """,
    # 10: rol urenschrijver (fase Uren, UR4)
    """
    ALTER TABLE membership DROP CONSTRAINT IF EXISTS membership_role_check;
    ALTER TABLE membership ADD CONSTRAINT membership_role_check
        CHECK (role IN ('hours', 'readonly', 'bookkeeper', 'admin'));
    """,
    # 11: back-up maken (FASE6_ANALYSE.md 6d-1, I7): laatste back-up per administratie (herinnering dashboard)
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS last_backup timestamptz;
    """,
    # 12: ingelezen uit een back-up van Run my Accounts / ledger123 (RMA_IMPORT_ANALYSE.md RI7): menu Beheer >
    # Run my Accounts-gegevens; gezet bij het inlezen (het menu leest de administratie niet)
    """
    ALTER TABLE company ADD COLUMN IF NOT EXISTS rma boolean NOT NULL DEFAULT false;
    """,
]


def migrate(conn: psycopg.Connection) -> int:
    """Breng het schema op de nieuwste versie; geeft de versie terug."""
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key text PRIMARY KEY, value text)")
        conn.execute("LOCK TABLE app_meta IN EXCLUSIVE MODE")
        cur = conn.cursor(row_factory=tuple_row)
        row = cur.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
        version = int(row[0]) if row else 0
        for i, ddl in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.execute(ddl)
            version = i
        conn.execute(
            "INSERT INTO app_meta VALUES ('schema_version', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(version),))
    return version


def create_database(dsn: str) -> bool:
    """Maak de app-database aan als die nog niet bestaat; True als hij nieuw is."""
    params = conninfo_to_dict(dsn)
    dbname = params.get("dbname")
    if not dbname:
        raise ValueError("app_dsn mist dbname")
    with psycopg.connect(make_conninfo(dsn, dbname="postgres"), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        return True


def open_pool(dsn: str) -> ConnectionPool:
    pool = ConnectionPool(dsn, min_size=1, max_size=10, open=False,
                          kwargs={"autocommit": True, "row_factory": dict_row})
    pool.open(wait=True, timeout=10)
    with pool.connection() as conn:
        migrate(conn)
    return pool
