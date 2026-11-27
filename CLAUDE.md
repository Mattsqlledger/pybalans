# Developing PyBalans with Claude

This file is the working brief for Claude (Claude Code reads it automatically) and for anyone who develops PyBalans
with an AI assistant. Read it completely before changing anything.

## What PyBalans is

PyBalans is a bookkeeping application in Python on PostgreSQL. It reads and enhances SQL-Ledger companies: it works
directly on the SQL-Ledger 3.2 database schema (database version 3.2.4, the schema of every 3.2.x release up to
3.2.12), so an existing SQL-Ledger company can be read from a backup or connected as it is, and everything
SQL-Ledger does keeps working the same way. On top of that it adds what SQL-Ledger lacks: bank statement import
(CAMT.053, MT940) with matching and reconciliation, VAT returns (NL, BE, UK), a dashboard, document layout, two-factor
login, audit trails, upload of purchase invoices, SEPA payment files, imports/exports, and a first-start wizard.
Run my Accounts / ledger123 backups (their own versions 2.8.11–2.8.52) are read as well; what SQL-Ledger does not know
is kept unchanged in schema `rma`.

This Python version is made and maintained by Matthijs Havinga (© 2026). Licence: GPL v2 or later. PyBalans is
based on SQL-Ledger, created by Dieter Simader (© DWS Systems Inc.); all credit for SQL-Ledger and its licence goes
to him — keep that credit intact. See `NOTICE.md`.

## Layout

| Path | What |
|---|---|
| `sqlledger/` | the bookkeeping library, no screens. Ledger logic ported from SQL-Ledger's `SL/*.pm`; `slschema/` holds the original schema and its 70 upgrade scripts (unchanged); `slupgrade.py` runs them; `rmaimport.py` reads Run my Accounts backups; `bankstatement.py` / `bankimport.py` the bank layer |
| `sqlledger_web/` | the web application: FastAPI, server-side HTML (Jinja2), a little HTMX; works without JavaScript. `routes_*.py` per screen group, `templates/`, `menu.py`, `i18n_*.py` (translations), `libmsg.py` (library messages in the user's language), `templates/help/` (help per screen) |
| `tests/` | pytest; `tests/web/` drives the web app with a test client |
| `compare/` | side-by-side comparison with the original Perl code (needs a local SQL-Ledger 3.2.12) |
| `docs/` | technical description (`TECHNISCH.md`) and the design notes per phase (`*_ANALYSE.md`), in Dutch |

The code comments, docstrings, commit messages and design notes are in **Dutch**; keep it that way in existing
files. User-facing text is written in Dutch in the code and translated (see below).

## How to work

1. **Analyse first, then build.** For anything larger than a small fix: read the relevant code and the design note,
   then write down numbered decision points, each with options and a recommendation ("advies A"), and get them
   approved before writing code. Record the choices in the design note (`docs/*_ANALYSE.md` or a new one).
2. **Match SQL-Ledger.** Behaviour that exists in SQL-Ledger 3.2.12 must give the same result (same postings, same
   amounts, same reports). Deliberate deviations are listed in `docs/TECHNISCH.md`. When you find a bug in the Perl
   code, do not copy it: fix it in PyBalans, check whether the original has it too, and document both.
3. **Never touch real bookkeeping data** while developing or testing. Tests and experiments run on a separate
   PostgreSQL server with throw-away databases. Do not put names, account numbers or other data of real companies or
   people in code, tests, commits or documentation; use invented examples.
4. **Every new screen text in all languages at once**: Dutch (source), English, German, French. UI texts go through
   `_()` / `N_()` and the catalogues `sqlledger_web/i18n_en.py`, `i18n_de.py`, `i18n_fr.py`; messages raised by the
   library get a template in `sqlledger_web/libmsg.py` (Dutch + English) and an entry in the `LIBMSG` dictionaries of
   the German and French catalogues. `tests/web/test_i18n.py` and `tests/web/test_libmsg.py` fail on anything
   missing or unused — keep them green. Use literal strings in `N_("…")`, otherwise the test cannot see them.
5. **Update the help.** Each screen has a "How does it work?" block (`templates/help/<screen>.html`) and there are
   task descriptions in `templates/help/tasks.html`. Labels you refer to with `hl("…")` must exist on a screen.
6. **Tests with every change**, and the full suite before you call something done:
   ```bash
   pip install ".[web,test]"
   pytest tests/
   ```
   The database tests need a PostgreSQL server where they may create and drop databases (`SQLLEDGER_TEST_DSN`,
   default `host=127.0.0.1 port=55432 user=postgres`) and a template database (`SQLLEDGER_TEST_TEMPLATE`, default
   `sl_template`). Create it with `compare/make_template.sh <path to sql-ledger-3.2.12>` (original schema, the Dutch
   standard chart and the test data in `compare/seed.sql`, `report_seed.sql`, `arap_seed.sql`). Without a reachable
   database those tests are skipped — that is not a pass.
7. **Keep the library and the screens apart.** All bookkeeping goes through `sqlledger/`; routes only collect input,
   call the library and render. Everything that posts runs in one transaction, locks what it changes, and is logged
   (audit trail / log). A screen must work without JavaScript.
8. **Database changes** go through the migrations in `sqlledger/extensions.py` (`x_…` tables and columns, never
   changing SQL-Ledger's own tables in a way SQL-Ledger itself would not understand). A company must stay readable
   by SQL-Ledger 3.2.12.
9. **Small, clear commits** with a message that says what changed and why. Do not push, open pull requests or change
   anything on GitHub without the maintainer's explicit approval for that action.

## Running it

- With Docker: see `README.md` (`docker compose up -d --build`, then `http://localhost:8000/welkom`).
- Locally for development:
  ```bash
  pip install -e ".[web,test]"
  pybalans --config dev/dev.toml init-db
  pybalans --config dev/dev.toml serve --port 8765
  ```
  `dev/dev.toml` points to a local test PostgreSQL; the first visit to `/welkom` starts the wizard (the start code is
  printed in the server log). `pybalans --help` lists the other commands.

## Good first areas

- A second installation route without Docker (Ubuntu 24.04 / Debian 13: script plus guide, systemd service).
- Swiss VAT (MWST) return and a Swiss payment file (pain.001 CH); the menu already hides the Dutch/EU-only items for
  a Swiss company.
- Stock per warehouse for Run my Accounts companies (the rows are kept in schema `rma`, not yet used).
- Testing the upgrade of very old SQL-Ledger versions (before 2.8) with real backups.

When in doubt about scope or a design choice: stop and ask, with numbered options and a recommendation.
