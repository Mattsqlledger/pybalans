#!/usr/bin/env bash
# Maakt sqlledger/slschema/ref-2.8.10.txt: het originele schema van SQL-Ledger 2.8.10 (tabellen, kolommen,
# sequences, functies/triggers) als vaste referentie voor het inlezen van een Run my Accounts-/ledger123-back-up
# (RMA_IMPORT_ANALYSE.md RI1, BK1). Alles in zo'n back-up dat hier niet in staat, is "extra" en gaat naar schema rma.
#
# Bron: sql/Pg-tables.sql uit de eerste commit van Run my Accounts (6d3c94f, 2013-09-27) = het ongewijzigde
# SQL-Ledger-2.8.10-bestand (versie 2.8.10, 54 tabellen; de keten 2.8.10 -> 3.2.4 loopt er foutloos op).
# Pg-functions.sql is in 2.8.10 leeg; functies en triggers komen alleen uit de originele upgradescripts tot en met
# 2.8.10 (bv. del_customer uit 2.3.x) en kunnen in een oudere, bijgewerkte database nog bestaan.
#
# gebruik: compare/make_ref_2810.sh <rma-kloon>
# Alleen op het testcluster (PGPORT 55432); maakt en verwijdert een wegwerpdatabase.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RMA="${1:?pad naar RMA-kloon (ledger123/runmyaccounts)}"
OUT="$HERE/../sqlledger/slschema/ref-2.8.10.txt"
UPG="$HERE/../sqlledger/slschema/upgrade"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-55432}" PGUSER="${PGUSER:-postgres}"
[ "$PGPORT" = 55432 ] || { echo "alleen op het testcluster 55432" >&2; exit 1; }
DB="ref2810_$$"
TMP="$(mktemp)"
trap 'dropdb --if-exists "$DB" >/dev/null 2>&1; rm -f "$TMP"' EXIT

git -C "$RMA" show 6d3c94f:sql/Pg-tables.sql > "$TMP"
grep -q "('version', '2.8.10')" "$TMP" || { echo "geen 2.8.10-schema in 6d3c94f" >&2; exit 1; }
createdb "$DB"
psql -q -v ON_ERROR_STOP=1 -d "$DB" -f "$TMP" >/dev/null

{
  echo "# Origineel SQL-Ledger 2.8.10 (RMA-commit 6d3c94f sql/Pg-tables.sql); gemaakt met compare/make_ref_2810.sh."
  echo "# table <naam> <kolommen…> | sequence <naam> | function <naam> (ook trigger met die naam)"
  psql -At -d "$DB" -c "SELECT 'table ' || table_name || ' ' || string_agg(column_name, ' ' ORDER BY column_name)
                        FROM information_schema.columns WHERE table_schema = 'public'
                        GROUP BY table_name ORDER BY table_name"
  psql -At -d "$DB" -c "SELECT 'sequence ' || sequence_name FROM information_schema.sequences
                        WHERE sequence_schema = 'public' ORDER BY 1"
  # functies uit de originele upgradescripts tot en met 2.8.10 (bestand <= 2.8.10)
  for f in "$UPG"/Pg-upgrade-*.sql; do
    to="$(basename "$f" .sql | sed 's/^Pg-upgrade-[^-]*-//')"
    [ "$(printf '%s\n2.8.10\n' "$to" | sort -V | tail -1)" = 2.8.10 ] || continue
    grep -aioE "create +(or +replace +)?function +[a-z_0-9]+" "$f" | awk '{print tolower($NF)}' || true
  done | sort -u | sed 's/^/function /'
} > "$OUT"
echo "$(grep -c '^table ' "$OUT") tabellen, $(grep -c '^sequence ' "$OUT") sequences, $(grep -c '^function ' "$OUT") functies -> $OUT"
