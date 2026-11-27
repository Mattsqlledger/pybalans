"""Eigen tabellen naast het SQL-Ledger-schema (fase 2, beslispunten L4 en I2).

Alle tabellen hebben het voorvoegsel ``x_`` zodat ze buiten het SQL-Ledger-schema
vallen; SQL-Ledger zelf negeert ze. ``ensure_schema`` maakt ze aan of werkt ze bij
(versie in ``x_meta``) en is veilig om bij elke aanroep te gebruiken.

Koppeling naar ``acc_trans``: die tabel heeft geen sleutel; net als rc.pl wordt een
regel aangewezen met (trans_id, chart_id, transdate, source). ``acc_trans.id`` is
GEEN sleutel (administratie C: 78.715 regels, 20.372 met id, 7.135 verschillend) — niet gebruiken.
"""

from __future__ import annotations

import psycopg

MIGRATIONS = [
    # 1: meta + afletterrondes + bankafschriften + vaste regels
    """
    CREATE TABLE IF NOT EXISTS x_reconciliation (
        id serial PRIMARY KEY,
        chart_id int NOT NULL,
        statementdate date NOT NULL,
        fromdate date,
        beginbalance numeric NOT NULL,
        statementbalance numeric NOT NULL,
        cleared_count int NOT NULL DEFAULT 0,
        employee_id int,
        login text,
        created timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS x_reconciliation_chart ON x_reconciliation (chart_id, statementdate);

    CREATE TABLE IF NOT EXISTS x_bank_statement (
        id serial PRIMARY KEY,
        chart_id int NOT NULL,
        format text NOT NULL,
        filename text,
        iban text,
        curr char(3),
        statement_ref text,
        sequence text,
        createdate date,
        begindate date,
        beginbalance numeric,
        enddate date,
        endbalance numeric,
        lines_total int NOT NULL DEFAULT 0,
        lines_skipped int NOT NULL DEFAULT 0,
        login text,
        imported timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS x_bank_line (
        id serial PRIMARY KEY,
        statement_id int NOT NULL REFERENCES x_bank_statement (id) ON DELETE CASCADE,
        chart_id int NOT NULL,
        dedup_key text NOT NULL,
        seq int NOT NULL,
        bookdate date NOT NULL,
        valuedate date,
        amount numeric NOT NULL,
        curr char(3),
        name text,
        iban text,
        bic text,
        description text,
        reference text,
        end_to_end text,
        bank_ref text,
        txcode text,
        status text NOT NULL DEFAULT 'open',      -- open | booked | matched | ignored
        trans_id int,                             -- boeking (ar/ap/gl) na boeken of koppelen
        source text,                              -- kenmerk waarmee de betaling geboekt is
        note text,
        login text,
        processed timestamptz,
        UNIQUE (chart_id, dedup_key)
    );
    CREATE INDEX IF NOT EXISTS x_bank_line_status ON x_bank_line (chart_id, status);

    CREATE TABLE IF NOT EXISTS x_bank_rule (
        id serial PRIMARY KEY,
        chart_id int,                             -- bankrekening; NULL = alle
        iban text,
        keyword text,
        direction char(1),                        -- '+', '-' of NULL
        accno text NOT NULL,                      -- tegenrekening voor de memoriaalboeking
        tax_accno text,
        description text,
        department_id int,
        project_id int,
        created timestamptz NOT NULL DEFAULT now()
    );
    """,
    # 2: leeg (gereserveerd voor afletteren, bleek niet nodig; blijft staan zodat de
    #    nummering van bestaande administraties klopt)
    "SELECT 1",
    # 3: bankafschrift inlezen, koppelen en boeken (bankimport.py)
    """
    ALTER TABLE x_bank_statement ADD COLUMN IF NOT EXISTS warnings text;
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS is_reversal boolean NOT NULL DEFAULT false;
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS kind text;          -- payment | gl | match | ignore
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS vc text;            -- customer | vendor
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS vc_id int;
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS trans_ids int[];    -- alle boekingen (facturen, vooruitbetaling)
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS transdate date;     -- datum van de gekoppelde regels
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS category char(1);   -- koppelregel a-e van het voorstel
    ALTER TABLE x_bank_line ADD COLUMN IF NOT EXISTS rule_id int;
    CREATE INDEX IF NOT EXISTS x_bank_line_date ON x_bank_line (chart_id, bookdate);
    ALTER TABLE x_bank_rule ADD COLUMN IF NOT EXISTS login text;
    """,
    # 4: voorraadcorrecties (fase 3, AT4): elke wijziging van parts.onhand buiten een factuur
    """
    CREATE TABLE IF NOT EXISTS x_stock_adjustment (
        id serial PRIMARY KEY,
        parts_id int NOT NULL,
        transdate date NOT NULL,
        oldonhand numeric NOT NULL,
        qty numeric NOT NULL,                     -- verschil (nieuw - oud)
        newonhand numeric NOT NULL,
        reason text NOT NULL,
        employee_id int,
        login text,
        created timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS x_stock_adjustment_part ON x_stock_adjustment (parts_id, transdate);
    """,
    # 5: samengestelde artikelen (fase 3, SA3-SA5): eigen verkoopprijs en productie (assembleren/demonteren)
    """
    CREATE TABLE IF NOT EXISTS x_assembly_price (
        parts_id int PRIMARY KEY,                 -- samengesteld artikel
        own_sellprice boolean NOT NULL DEFAULT false
    );
    CREATE TABLE IF NOT EXISTS x_assembly_build (
        id serial PRIMARY KEY,
        parts_id int NOT NULL,                    -- samengesteld artikel
        transdate date NOT NULL,
        qty numeric NOT NULL,                     -- > 0 assembleren, < 0 demonteren
        oldonhand numeric NOT NULL,
        reference text,
        notes text,
        employee_id int,
        login text,
        created timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS x_assembly_build_part ON x_assembly_build (parts_id, transdate);
    CREATE TABLE IF NOT EXISTS x_assembly_build_line (
        id serial PRIMARY KEY,
        build_id int NOT NULL REFERENCES x_assembly_build (id),
        parts_id int NOT NULL,                    -- onderdeel
        perassembly numeric NOT NULL,             -- aantal per stuk volgens de stuklijst
        qty numeric NOT NULL,                     -- voorraadbeweging van het onderdeel
        oldonhand numeric NOT NULL
    );
    CREATE INDEX IF NOT EXISTS x_assembly_build_line_part ON x_assembly_build_line (parts_id);
    """,
    # 6: kostprijs (fase 3, F3/F5): elke FIFO-toewijzing tussen een afnemende factuurregel (verkoop,
    #    onderdeel van een verkocht samengesteld artikel, retour aan leverancier) en een aanvoerregel
    #    (inkoop, retour van klant), en de kostprijs van aanvoerregels in volle precisie (cogs.py)
    """
    CREATE TABLE IF NOT EXISTS x_cogs_allocation (
        id serial PRIMARY KEY,
        parts_id int NOT NULL,
        consumer_id int NOT NULL,                 -- invoice.id van de afnemende regel (qty > 0)
        supply_id int,                            -- invoice.id van de aanvoerregel (qty < 0); NULL = retour
                                                  -- aan leverancier zonder voorraad (tegen de retourprijs)
        qty numeric NOT NULL,                     -- > 0
        amount numeric NOT NULL,                  -- kostprijs in de standaardvaluta
        kind char(1) NOT NULL,                    -- 'c' bij boeken van de afnemer, 'd' later bij de aanvoer
        project_id int,
        created timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS x_cogs_allocation_consumer ON x_cogs_allocation (consumer_id);
    CREATE INDEX IF NOT EXISTS x_cogs_allocation_supply ON x_cogs_allocation (supply_id);
    CREATE INDEX IF NOT EXISTS x_cogs_allocation_part ON x_cogs_allocation (parts_id);
    CREATE TABLE IF NOT EXISTS x_invoice_cost (
        invoice_id int PRIMARY KEY,               -- aanvoerregel
        qty numeric NOT NULL,                     -- > 0
        amount numeric NOT NULL                   -- totale kostprijs in de standaardvaluta
    );
    """,
    # 7: orders/offertes (fase 3, O2/O4, ON3/ON4): koppelingen bij omzetten. kind 'order' = offerte/aanvraag ->
    #    order (to_id = oe.id), 'invoice' = order of offerte -> factuur (to_id = ar/ap.id), 'backorder' = order ->
    #    nabestelling (to_id = oe.id, via_id = de factuur waarbij hij ontstond, fingerprint = inhoud bij ontstaan)
    """
    CREATE TABLE IF NOT EXISTS x_oe_link (
        id serial PRIMARY KEY,
        kind text NOT NULL,
        from_id int NOT NULL,
        to_id int NOT NULL,
        via_id int,
        fingerprint text,
        login text,
        created timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS x_oe_link_from ON x_oe_link (from_id);
    CREATE INDEX IF NOT EXISTS x_oe_link_to ON x_oe_link (to_id);
    CREATE INDEX IF NOT EXISTS x_oe_link_via ON x_oe_link (via_id);
    """,
    # 8: documentinstellingen (fase 4 stap 3, DI3/T2): eigen tabel en niet ``defaults``, want SQL-Ledger
    #    (3.2.12 en 4.x) wist bij opslaan van Systeem > Standaarden alle sleutels die het niet kent
    """
    CREATE TABLE IF NOT EXISTS x_doc_setting (
        key text PRIMARY KEY,
        value text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    """,
    # 9: vaste kostenrekening per leverancier (fase 4 stap 4e, IU3): SQL-Ledger kent geen kolom
    #    daarvoor en negeert onbekende kolommen niet overal, dus een eigen tabel (rekening via chart.id)
    """
    CREATE TABLE IF NOT EXISTS x_vendor_default (
        vendor_id int PRIMARY KEY,
        expense_chart_id int NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    """,
    # 10: dashboard (fase 4 stap 5b, DB5): eigen instellingen (niet ``defaults``, zie 8) en afgevinkte taken
    """
    CREATE TABLE IF NOT EXISTS x_setting (
        key text PRIMARY KEY,
        value text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS x_task_done (
        kind text NOT NULL,
        period_end date NOT NULL,
        login text,
        done timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (kind, period_end)
    );
    """,
    # 11: sha256 van bijlagen (fase 4 stap 6b, RB5) als ``archive.hash`` (SQL-Ledger 4.x) ontbreekt; de waarden worden
    # door ``attachments.ensure_hashes`` ingevuld (uuencode decoderen kan SQL niet)
    """
    CREATE TABLE IF NOT EXISTS x_archive_hash (
        archive_id int PRIMARY KEY REFERENCES archive(id) ON DELETE CASCADE,
        hash char(64) NOT NULL
    );
    CREATE INDEX IF NOT EXISTS x_archive_hash_hash ON x_archive_hash (hash);
    """,
    # 12: wanneer en door wie een archiefstuk is toegevoegd (fase 4 stap 6c, RB3: kolom "toegevoegd" in het overzicht;
    # SQL-Ledger legt dat niet vast)
    """
    CREATE TABLE IF NOT EXISTS x_archive_added (
        archive_id int PRIMARY KEY REFERENCES archive(id) ON DELETE CASCADE,
        added timestamptz NOT NULL DEFAULT now(),
        login text
    );
    """,
    # 13: btw-aangifte (BA3/BA4/BA6): rubriek per btw-rekening, rubriek per klant (omzet zonder btw) en de
    # vastgelegde aangiften met hun btw-boeking (SQL-Ledger kent dit niet; eigen tabellen, zie 8)
    """
    CREATE TABLE IF NOT EXISTS x_vat_box (
        chart_id int PRIMARY KEY,
        box text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS x_vat_customer_box (
        customer_id int PRIMARY KEY,
        box text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS x_vat_return (
        fromdate date NOT NULL,
        todate date NOT NULL,
        gl_id int,
        snapshot jsonb NOT NULL,
        login text,
        created timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (fromdate, todate)
    );
    """,
    # 14: ICP-opgaaf (ICP5/ICP6/ICP8): ICP-soort per klant (leveringen/diensten/ABC) en de vastgelegde opgaven
    """
    CREATE TABLE IF NOT EXISTS x_icp_customer (
        customer_id int PRIMARY KEY,
        kind text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS x_icp_return (
        fromdate date NOT NULL,
        todate date NOT NULL,
        gl_id int,
        snapshot jsonb NOT NULL,
        login text,
        created timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (fromdate, todate)
    );
    """,
    # 15: RGS-referentiecode per rekening (REKENINGSCHEMA_ANALYSE.md RS3/RS13; SQL-Ledger kent dit niet): gevuld bij
    # een nieuwe administratie met een NL-standaardschema; het koppelscherm (RS13) bouwt hierop. Eén code per
    # rekening per RGS-versie.
    """
    CREATE TABLE IF NOT EXISTS x_account_rgs (
        chart_id int NOT NULL,
        version text NOT NULL,
        code text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (chart_id, version)
    );
    """,
    # 16: landcode bij de btw-rubriek (RS9, REKENINGSCHEMA_ANALYSE.md deelstap 3): een Zwitsers schema legt zijn
    # MWST-Ziffern vast met land CH; de Nederlandse aangifte en ICP lezen alleen land NL
    """
    ALTER TABLE x_vat_box ADD COLUMN IF NOT EXISTS country text NOT NULL DEFAULT 'NL';
    ALTER TABLE x_vat_box DROP CONSTRAINT IF EXISTS x_vat_box_pkey;
    ALTER TABLE x_vat_box ADD PRIMARY KEY (chart_id, country);
    """,
    # 17: toewijzing medewerker → project + dienst (UREN_ANALYSE.md UR5): periode, eventueel eigen verkoopprijs en
    # projectleider; nooit gewist maar afgesloten met een einddatum (SQL-Ledger kent dit niet)
    """
    CREATE TABLE IF NOT EXISTS x_assignment (
        id serial PRIMARY KEY,
        employee_id int NOT NULL,
        project_id int NOT NULL,
        parts_id int NOT NULL,
        validfrom date NOT NULL,
        validto date,
        sellprice numeric,
        projectleader_id int,
        notes text,
        login text,
        updated timestamptz NOT NULL DEFAULT now(),
        CHECK (validto IS NULL OR validto >= validfrom)
    );
    """,
    # 18: interne uren (UREN_ANALYSE.md IN1–IN3, v1.1): soort project (klant/intern) met kostenplaats, intern
    # uurtarief per medewerker met geldig-vanaf, intern tarief op de toewijzing en het tarief dat bij het opslaan van
    # een tijdkaart is vastgelegd (``jcitems.sellprice`` blijft de verkoopprijs; SQL-Ledger kent dit niet)
    """
    CREATE TABLE IF NOT EXISTS x_project (
        project_id int PRIMARY KEY,
        kind text NOT NULL CHECK (kind IN ('customer', 'internal')),
        department_id int,                        -- kostenplaats (department.role 'C') van een intern project
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS x_employee_rate (
        id serial PRIMARY KEY,
        employee_id int NOT NULL,
        validfrom date NOT NULL,
        rate numeric NOT NULL CHECK (rate >= 0),
        notes text,
        login text,
        updated timestamptz NOT NULL DEFAULT now(),
        UNIQUE (employee_id, validfrom)
    );
    ALTER TABLE x_assignment ADD COLUMN IF NOT EXISTS internalrate numeric;
    CREATE TABLE IF NOT EXISTS x_jcitem_cost (
        jcitem_id int PRIMARY KEY,
        costprice numeric NOT NULL,
        source text NOT NULL,                     -- assignment | employee | service
        login text,
        updated timestamptz NOT NULL DEFAULT now()
    );
    """,
    # 19: SEPA-betaalbestanden (IM9 A1, SP11): niets geboekt, wel vastgelegd welke facturen in welk bestand zaten
    # (tegen dubbel betalen) en het bestand zelf (opnieuw downloaden)
    """
    CREATE TABLE IF NOT EXISTS x_sepa_export (
        id serial PRIMARY KEY,
        msgid text NOT NULL,
        version text NOT NULL,
        chart_id int,
        execdate date NOT NULL,
        total numeric NOT NULL,
        count int NOT NULL,
        login text,
        xml text NOT NULL,
        created timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS x_sepa_item (
        export_id int NOT NULL REFERENCES x_sepa_export (id) ON DELETE CASCADE,
        trans_id int NOT NULL,
        amount numeric NOT NULL,
        endtoend text NOT NULL,
        PRIMARY KEY (export_id, trans_id)
    );
    CREATE INDEX IF NOT EXISTS x_sepa_item_trans ON x_sepa_item (trans_id);
    """,
    # 20: btw-aangifte per land (BTW_LANDEN_ANALYSE.md BL2): vastgelegde aangifte en rubriek bij de klant met landcode,
    # zodat een administratie van land kan wisselen zonder iets te verliezen (bestaande rijen zijn Nederlands)
    """
    ALTER TABLE x_vat_return ADD COLUMN IF NOT EXISTS country text NOT NULL DEFAULT 'NL';
    ALTER TABLE x_vat_return DROP CONSTRAINT IF EXISTS x_vat_return_pkey;
    ALTER TABLE x_vat_return ADD PRIMARY KEY (country, fromdate, todate);
    ALTER TABLE x_vat_customer_box ADD COLUMN IF NOT EXISTS country text NOT NULL DEFAULT 'NL';
    ALTER TABLE x_vat_customer_box DROP CONSTRAINT IF EXISTS x_vat_customer_box_pkey;
    ALTER TABLE x_vat_customer_box ADD PRIMARY KEY (customer_id, country);
    """,
    # 21: btw-aangifte België (BTW_LANDEN_ANALYSE.md BL3/BE2): rubriek van de inkoop per kosten-/activarekening
    # (81/82/83); de Belgische verkoop-btw heette eerst rooster 54 en krijgt nu het rooster van de grondslag (01/02/03)
    """
    CREATE TABLE IF NOT EXISTS x_vat_account_box (
        chart_id int NOT NULL,
        country text NOT NULL,
        box text NOT NULL,
        login text,
        updated timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (chart_id, country)
    );
    UPDATE x_vat_box b SET box = CASE WHEN r.rate >= 0.17 THEN '03' WHEN r.rate >= 0.09 THEN '02' ELSE '01' END
    FROM (SELECT chart_id, max(rate) AS rate FROM tax GROUP BY chart_id) r
    WHERE b.country = 'BE' AND b.box = '54' AND r.chart_id = b.chart_id;
    """,
]


def ensure_schema(conn: psycopg.Connection) -> int:
    """Maak/werk de ``x_``-tabellen bij; geeft de schemaversie terug."""
    with conn.transaction():
        cur = conn.cursor()
        # eerst het slot: bij de eerste keer openen van een gekoppelde database (dashboardblokken tegelijk) botste
        # CREATE TABLE IF NOT EXISTS anders op pg_type (gevonden bij de eerste start met een teruggezette back-up)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('x_meta'))")
        cur.execute("CREATE TABLE IF NOT EXISTS x_meta (key text PRIMARY KEY, value text)")
        cur.execute("SELECT value FROM x_meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        version = int(row[0]) if row else 0
        for i, ddl in enumerate(MIGRATIONS[version:], start=version + 1):
            cur.execute(ddl)
            version = i
        cur.execute("INSERT INTO x_meta VALUES ('schema_version', %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(version),))
    return version
