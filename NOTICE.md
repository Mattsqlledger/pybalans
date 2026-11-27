# Notice

PyBalans — bookkeeping in Python on PostgreSQL · reads and enhances SQL-Ledger companies

Copyright (C) 2026 Matthijs Havinga — for this Python version (PyBalans). SQL-Ledger itself is the work of
Dieter Simader (DWS Systems Inc.); see below.

This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
version. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License in
[`COPYING`](COPYING) for more details.

## Derived from SQL-Ledger

PyBalans is a port of **SQL-Ledger 3.2.12**, created by **Dieter Simader**, Copyright (c) DWS Systems Inc.
(<https://www.sql-ledger.com>), licensed under the GNU General Public License, version 2 or later. All credit for
SQL-Ledger — its design, its bookkeeping logic, its database schema and its licence — goes to Dieter Simader; PyBalans
only rewrites that work in Python and adds to it. SQL-Ledger is a registered trademark of DWS Systems Inc.;
PyBalans is not affiliated with or endorsed by DWS Systems Inc. It reads SQL-Ledger databases and backups.

Parts derived from SQL-Ledger:

| Part in PyBalans | Derived from (SQL-Ledger 3.2.12) |
|---|---|
| `sqlledger/` — general ledger, receivables/payables, invoices, orders, payments, reports, year-end, reconciliation, recurring transactions | the Perl modules in `SL/*.pm` and the screen logic in `bin/mozilla/*.pl` (rewritten in Python; the Perl origin is listed per function in `docs/TECHNISCH.md`) |
| `sqlledger/slschema/` — database schema and upgrade scripts | `sql/Pg-tables.sql`, `sql/Pg-indices.sql`, `sql/Mimetype.sql`, `sql/Pg-upgrade-*.sql` (copied) |
| `sqlledger_web/doctemplates/standard/` — invoice, order and statement layouts | the layout of the LaTeX/HTML templates in `doc/templates/` |
| `sqlledger_web/i18n_en.py` and amount-in-words (`sqlledger/num2text.py`) | terminology of the English translation and `locale/*/Num2text` |
| `compare/` — comparison tests | drive the original Perl code side by side with the Python code (SQL-Ledger itself is not included) |

## Other sources

- Swiss chart of accounts (`sqlledger/charts/ch-kmu.csv`): based on a KMU/PME/PMI chart from 2012 (GPL; author
  named in the file header), renumbered; checked against the RMACH chart (GPL-2.0).
- Dutch charts can be linked to the Referentie Grootboekschema (RGS, <https://www.referentiegrootboekschema.nl>);
  the RGS list itself is not included and is read in by the user.
- Fonts in `sqlledger_web/fonts/` (Inter, Source Sans 3, Source Serif 4, Lora, Liberation Sans) are licensed under the
  SIL Open Font License 1.1; see the `OFL.txt` files and `fonts/NOTICE.txt`.

PostgreSQL is a trademark of the PostgreSQL Community Association of Canada; PyBalans is not affiliated with it.
