# Testing PyBalans with a Run my Accounts backup

*Deutsch: [`TESTING-RMA.de.md`](TESTING-RMA.de.md)*

This is a private test version of PyBalans. It reads a **Run my Accounts** (or ledger123) backup into a new
company, checks that the figures are unchanged, and shows a report of what was converted and what was kept.

**Your data stays with you.** Everything runs on your own computer in Docker. PyBalans only talks to its own
database; it sends e-mail only if you set up a mail server in it. Your Run my Accounts installation and the backup
file are not changed. Please do **not** send us the backup; send only the report (see step 6).

## What you need

- Docker: [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Mac or Windows, or Docker Engine
  with the compose plugin on Linux. About 2 GB free disk space.
- `git`, and access to the private repository (you received an invitation).
- A backup of one Run my Accounts company (see step 4). Please tell us its size beforehand: the limit is 512 MB.

## 1. Get the code

```
git clone https://github.com/<repository>.git pybalans
cd pybalans
```

## 2. Start PyBalans

Choose a password for the database (it never leaves your computer) and start:

```
echo 'choose-a-long-password' > db_password.txt
docker compose up -d --build
```

The first time this takes a few minutes. Then show the start code:

```
docker compose logs app
```

Look for `Start code / startcode: XXXX-XXXX-XXXX`.

If port 8000 is already in use on your computer, change `127.0.0.1:8000:8000` in `compose.yaml` to for example
`127.0.0.1:8010:8000` (and `http://localhost:8000` a few lines above it), then run `docker compose up -d` again and
use that port below.

## 3. First start

Open **http://localhost:8000/welkom** in your browser.

1. Choose your language (English, German, French or Dutch).
2. Enter the start code and create the administrator (login, name, password).
3. Set up two-factor authentication with an authenticator app (1Password, Google or Microsoft Authenticator). This
   is required for administrators.
4. Choose **Import backup** (German: "Sicherung einlesen").

## 4. Make the Run my Accounts backup

Any of these works:

- the backup from the Run my Accounts screens (`.sql.gz`);
- the backup made by mojo-admin (`.sql.bz2`);
- a plain `pg_dump -C <database> | gzip > company.sql.gz` made on the database server.

## 5. Read it in

1. Choose the file and click **Upload** (German: "Hochladen"). The check screen should say *Run my Accounts / ledger123 backup* and show
   the version (for example 2.8.52).
2. Check the name, then click **Import** (German: "Einlesen"). Depending on the size this takes from seconds to several minutes; you may
   close the window, the import continues on the server. Long enough for a fika.

What happens:

- Everything the original SQL-Ledger does not know (the banking layer, audit logs, VAT settlements, transfers,
  assembly documents, extra columns) is kept unchanged in a separate part of the database (schema `rma`).
- The company is then upgraded to the SQL-Ledger 3.2 structure that PyBalans uses.
- PyBalans compares the **trial balance per account** and the **open amount per invoice** before and after. If
  anything differs, nothing is created and the differences are shown.

## 6. What we would like to hear back

- The report on the result page (also later in the company menu under *System > Run my Accounts data*, German
  *Verwaltung > Run-my-Accounts-Daten*): a screenshot of the check and of
  the sections *Taken over* and *Kept, but without a place in PyBalans*. These contain counts, no amounts per customer.
- If the import fails: the error message, and on a failed check the list of differing accounts or documents
  (a screenshot is fine; amounts per account are shown there, so leave them out if you prefer).
- Anything that looks wrong when you browse the company: dashboard, trial balance, open items, invoices.

## Known limits of this version

- **Stock per warehouse** is not converted yet: stock per part is correct, but in PyBalans it is all under "no
  warehouse". Run my Accounts' warehouse lines are kept.
- Some Run my Accounts data has no place in PyBalans yet (reminder stop, QR-IBAN, fixed income account per
  customer, early payment discount, the banking layer). It is kept and counted in the report.
- PyBalans has one exchange rate per currency and day; where Run my Accounts had different buy and sell rates, the
  buy rate is used (each document keeps its own rate).
- SQL-Ledger 3.2 has one account for exchange rate gains and losses; if Run my Accounts had two, the report says so.
- There is no way back from PyBalans to Run my Accounts. Your original data is untouched.

## Stop or remove

```
docker compose down        # stop; your data is kept
docker compose up -d       # start again
docker compose down -v     # stop and DELETE everything (database and files)
```

---

PyBalans is made by Matthijs Havinga. Questions and findings: open an issue in this repository. Thank you for testing!
