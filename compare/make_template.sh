#!/usr/bin/env bash
# Maakt de template-database 'sl_template' met het originele SQL-Ledger-schema,
# het Nederlandse standaard-rekeningschema en de testgegevens uit seed.sql.
#
# gebruik: compare/make_template.sh <pad/naar/sql-ledger>
# PostgreSQL-verbinding via PGHOST/PGPORT/PGUSER (standaard 127.0.0.1:55432, postgres).
set -euo pipefail
SLDIR="${1:?pad naar uitgepakte sql-ledger-3.2.12 map}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-55432}" PGUSER="${PGUSER:-postgres}"

psql -q -c 'DROP DATABASE IF EXISTS sl_template'
psql -q -c 'CREATE DATABASE sl_template'
for f in Pg-tables.sql Dutch-Standard-chart.sql Mimetype.sql Pg-indices.sql; do
  # Dutch-Standard-chart.sql bevat 2 foute INSERTs in curr (kolom 'precision' i.p.v. 'prec');
  # seed.sql vult de valuta's daarom zelf.
  psql -q -d sl_template -f "$SLDIR/sql/$f" >/dev/null 2>&1 || true
done
psql -q -v ON_ERROR_STOP=1 -d sl_template -f "$HERE/seed.sql"
psql -q -v ON_ERROR_STOP=1 -d sl_template -f "$HERE/report_seed.sql" >/dev/null
psql -q -v ON_ERROR_STOP=1 -d sl_template -f "$HERE/arap_seed.sql" >/dev/null
# seeds gebruiken vaste id's: tellers bijwerken (anders botst een nieuw adres/contact)
psql -q -v ON_ERROR_STOP=1 -d sl_template -c "SELECT setval('addressid', greatest((SELECT max(id) FROM address), (SELECT coalesce(max(address_id),0) FROM bank))); SELECT setval('contactid', (SELECT max(id) FROM contact)); SELECT setval('invoiceid', (SELECT max(id) FROM invoice));" >/dev/null
echo "sl_template aangemaakt"
