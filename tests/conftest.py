"""Testdatabase: elke test krijgt een verse kopie van de template-database.

Stel in met omgevingsvariabelen:
  SQLLEDGER_TEST_DSN       (standaard: host=127.0.0.1 port=55432 user=postgres)
  SQLLEDGER_TEST_TEMPLATE  (standaard: sl_template)
Zonder bereikbare database worden de databasetests overgeslagen.
"""

import os
import uuid

import psycopg
import pytest

from sqlledger import ChartOfAccounts, GeneralLedger, UserConfig

DSN = os.environ.get("SQLLEDGER_TEST_DSN", "host=127.0.0.1 port=55432 user=postgres")
TEMPLATE = os.environ.get("SQLLEDGER_TEST_TEMPLATE", "sl_template")


@pytest.fixture
def conn():
    try:
        admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("geen test-PostgreSQL bereikbaar")
    name = f"t_{uuid.uuid4().hex[:10]}"
    admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}"')
    c = psycopg.connect(f"{DSN} dbname={name}", autocommit=True)
    yield c
    c.close()
    admin.execute(f'DROP DATABASE "{name}"')
    admin.close()


@pytest.fixture
def config():
    return UserConfig(login="admin")


@pytest.fixture
def gl(conn, config):
    return GeneralLedger(conn, config)


@pytest.fixture
def ca(conn, config):
    return ChartOfAccounts(conn, config)
