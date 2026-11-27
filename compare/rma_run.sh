#!/usr/bin/env bash
# Maakt een RMA-testadministratie opnieuw aan en vult hem met een scenario.
# gebruik: compare/rma_run.sh <rma-dir> <db> <chart> <versie> <scenario.json> [zonder-custom]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RMA="$1" DB="$2" CHART="$3" VER="$4" SCEN="$5" NOCUSTOM="${6:-}"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-55432}" PGUSER="${PGUSER:-postgres}"
psql -q -c "DROP DATABASE IF EXISTS \"$DB\"" 2>/dev/null
perl "$HERE/rma_create.pl" "$RMA" "$DB" "$CHART" "$VER" $NOCUSTOM 2>&1 | grep -v -E "NOTICE|^createdb:" || true
perl "$HERE/rma_driver.pl" "$RMA" "$DB" "$SCEN"
