#!/usr/bin/env bash
# Bouwt de synthetische Run my Accounts-testset (RMA_IMPORT_ANALYSE.md RM7(1), RT1–RT8) opnieuw op:
# drie RMA-administraties, gevuld via RMA's eigen schermcode (rma_driver.pl), en bewaart ze als
# `pg_dump -C | gzip` zoals RMA's eigen back-up (SL/AM.pm) in tests/data/rma/.
#
# gebruik: compare/make_rma_testset.sh <rma-kloon> <rma-kloon-op-83a39e2>
#   rma-kloon:          ledger123/runmyaccounts (master; gebouwd op 1184735 van 2026-10-06)
#   rma-kloon-op-83a39e2: dezelfde repo op 83a39e2 (2024-02-02, laatste stand met schema 2.8.26),
#                       bv. git -C <rma-kloon> worktree add --detach <map> 83a39e2
# Alleen voor het testcluster (PGPORT 55432); nooit echte gegevens.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RMA="${1:?pad naar RMA-kloon (master)}"
RMA_OLD="${2:?pad naar RMA-kloon op 83a39e2}"
OUT="$HERE/../tests/data/rma"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-55432}" PGUSER="${PGUSER:-postgres}"
[ "$PGPORT" = 55432 ] || { echo "alleen op het testcluster 55432" >&2; exit 1; }
mkdir -p "$OUT"

build() {    # db chart versie scenario rma-map
  "$HERE/rma_run.sh" "$5" "$1" "$2" "$3" "$HERE/rma_scenarios/$4" >/dev/null
  pg_dump -C "$1" | gzip -9n > "$OUT/$1.sql.gz"
  echo "$1: $(psql -At -d "$1" -c "SELECT fldvalue FROM defaults WHERE fldname = 'version'") -> tests/data/rma/$1.sql.gz"
}
build rma_test_ch Swiss-German 2.8.52 ch.json "$RMA"
build rma_test_de Germany-SKR03 2.8.52 de.json "$RMA"
build rma_test_oud Swiss-German 2.8.26 oud.json "$RMA_OLD"
