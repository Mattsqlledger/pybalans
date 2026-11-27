"""Webapp-tests: per test een verse app-database en een kopie van sl_template.

Zelfde omgevingsvariabelen als tests/conftest.py; zonder PostgreSQL overgeslagen.
"""

import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from sqlledger_web import appdb
from sqlledger_web.main import create_app
from sqlledger_web.settings import Settings


DSN = os.environ.get("SQLLEDGER_TEST_DSN", "host=127.0.0.1 port=55432 user=postgres")
TEMPLATE = os.environ.get("SQLLEDGER_TEST_TEMPLATE", "sl_template")


@pytest.fixture
def pg_admin():
    try:
        admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("geen test-PostgreSQL bereikbaar")
    yield admin
    admin.close()


@pytest.fixture
def company_db(pg_admin):
    name = f"wc_{uuid.uuid4().hex[:10]}"
    pg_admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}"')
    yield name
    pg_admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def settings(pg_admin, tmp_path_factory):
    name = f"wa_{uuid.uuid4().hex[:10]}"
    params = conninfo_to_dict(DSN)
    # eigen datamap: nooit sjablonen of eigen woorden uit ~/.local/share/sqlledger meenemen
    s = Settings(app_dsn=f"{DSN} dbname={name}", db_host=params.get("host", "127.0.0.1"),
                 db_port=int(params.get("port", 5432)), db_user=params.get("user", ""),
                 data_dir=str(tmp_path_factory.mktemp("data")))
    appdb.create_database(s.app_dsn)
    yield s
    pg_admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def db(settings):
    """Directe verbinding met de app-database (voor opbouw en controle)."""
    with psycopg.connect(settings.app_dsn, autocommit=True, row_factory=dict_row) as conn:
        appdb.migrate(conn)
        yield conn


@pytest.fixture
def client(settings, db):
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        yield c


@pytest.fixture
def company(db, company_db):
    return db.execute(
        "INSERT INTO company (name, dbname, modules) VALUES ('Testbedrijf', %s, %s) RETURNING *",
        (company_db, list(appdb.MODULES))).fetchone()
