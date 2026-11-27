-- Testgegevens voor ouderdomsanalyse, aanmaningen, btw-rapport en betalingen.
-- Peildatum in de scenario's: 2026-06-30.

-- bestaande seed-boekingen consistent maken (950 is volledig betaald)
UPDATE ar SET paid = 121, datepaid = '2026-03-10', duedate = '2026-03-31', curr = 'EUR', netamount = 100 WHERE id = 950;
UPDATE ap SET duedate = '2026-04-04', curr = 'EUR' WHERE id = 951;

-- btw-tarieven
INSERT INTO tax (chart_id, rate, taxnumber) SELECT id, 0.21, 'NL-HOOG' FROM chart WHERE accno = '1810';
INSERT INTO tax (chart_id, rate, taxnumber) SELECT id, 0.09, 'NL-LAAG' FROM chart WHERE accno = '1811';
INSERT INTO tax (chart_id, rate, taxnumber) SELECT id, 0.21, 'NL-VOOR' FROM chart WHERE accno = '1800';

-- klanten en leveranciers
INSERT INTO customer (id, name, customernumber, terms, taxnumber, phone, contact, language_code, email)
VALUES (902, 'Bakkerij Cees', 'K002', 30, 'NL001B01', '030-1234567', 'Cees', '', 'cees@example.test'),
       (903, 'Overseas Inc', 'K003', 14, 'US-99', '', '', '', ''),
       (904, 'Kassaklant', 'K004', 0, '', '', '', '', '');
UPDATE customer SET terms = 14, taxnumber = 'NL900B01' WHERE id = 900;
INSERT INTO vendor (id, name, vendornumber, terms, taxnumber)
VALUES (905, 'Graanhandel De Molen', 'L002', 30, 'NL905B01');
INSERT INTO address (id, trans_id, address1, address2, city, zipcode, country)
VALUES (3, 902, 'Bakkerstraat 3', 'unit 2', 'Amersfoort', '3811AA', 'NL'),
       (4, 903, '5th Avenue 1', '', 'New York', '10001', 'US'),
       (5, 904, 'Markt 1', '', 'Utrecht', '3511AB', 'NL'),
       (6, 905, 'Molenweg 9', '', 'Zaandam', '1501AA', 'NL');
-- twee contactpersonen bij één klant (rapporten joinen op contact)
INSERT INTO contact (id, trans_id, firstname, lastname, salutation, typeofcontact)
VALUES (10, 902, 'Cees', 'Bakker', 'Dhr.', 'company'), (11, 902, 'Anna', 'Bakker', 'Mevr.', 'company'),
       (12, 900, 'Piet', 'Klant', 'Dhr.', 'company');
INSERT INTO customertax (customer_id, chart_id) SELECT c, ch.id FROM (VALUES (900), (902), (903), (904)) v(c)
  CROSS JOIN chart ch WHERE ch.accno IN ('1810', '1811');
INSERT INTO vendortax (vendor_id, chart_id) SELECT v, ch.id FROM (VALUES (901), (905)) x(v)
  CROSS JOIN chart ch WHERE ch.accno = '1800';
INSERT INTO bank (id, name, iban, bic, rvc, membernumber) VALUES (1, 'Testbank', 'NL00TEST0123456789', 'TESTNL2A', 'RVC1', 'M-1');
INSERT INTO language (code, description) VALUES ('en', 'English');

-- artikelen
INSERT INTO parts (id, partnumber, description, unit, sellprice, income_accno_id)
VALUES (1000, 'KOEK-1', 'Roomboterkoek', 'st', 10, (SELECT id FROM chart WHERE accno = '8520')),
       (1001, 'BOEK-1', 'Receptenboek', 'st', 25, (SELECT id FROM chart WHERE accno = '8530'));
INSERT INTO partstax (parts_id, chart_id) SELECT 1000, id FROM chart WHERE accno = '1810';
INSERT INTO partstax (parts_id, chart_id) SELECT 1001, id FROM chart WHERE accno = '1811';

INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2026-05-20', 1.10);

CREATE FUNCTION pg_temp.acc(tid int, p_accno text, amount numeric, d date, src text DEFAULT NULL,
                            memo text DEFAULT NULL) RETURNS void AS $$
  INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source, memo, approved)
  VALUES (tid, (SELECT id FROM chart c WHERE c.accno = p_accno), amount, d, src, memo, true);
$$ LANGUAGE sql;

-- verkoopfacturen: (id, nr, datum, verval, klant, netto, btw, betaald, betaaldatum, factuur?, valuta, kassa, afdeling)
INSERT INTO ar (id, invnumber, transdate, duedate, customer_id, netamount, amount, paid, datepaid,
                invoice, curr, till, department_id, approved, description, ordnumber, ponumber, notes, bank_id, dcn)
VALUES
 (3001, 'F-3001', '2026-06-25', '2026-07-09', 900, 100.00, 121.00, 0, NULL, true,  'EUR', NULL, 0, true, 'Koek juni', 'O-1', 'PO-1', NULL, 1, 'DCN1'),
 (3002, 'F-3002', '2026-06-10', '2026-06-24', 902, 250.00, 272.50, 50, '2026-06-20', true, 'EUR', NULL, 0, true, 'Boeken', 'O-2', NULL, NULL, NULL, NULL),
 (3003, 'F-3003', '2026-05-20', '2026-06-03', 903, 500.00, 500.00, 0, NULL, false, 'USD', NULL, 0, true, 'Export', NULL, NULL, NULL, NULL, NULL),
 (3004, 'F-3004', '2026-05-05', '2026-06-04', 902, 80.00, 96.80, 0, NULL, false, 'EUR', NULL, 600, true, 'Taarten', NULL, NULL, NULL, NULL, NULL),
 (3005, 'F-3005', '2026-04-15', '2026-04-29', 900, 40.00, 48.40, 48.40, '2026-05-01', false, 'EUR', NULL, 0, true, 'Betaald', NULL, NULL, NULL, NULL, NULL),
 (3006, 'F-3006', '2026-04-01', '2026-04-15', 900, 300.00, 363.00, 100, '2026-04-20', false, 'EUR', NULL, 0, true, 'Catering', NULL, NULL, NULL, NULL, NULL),
 (3007, 'F-3007', '2026-02-01', '2026-02-15', 902, 60.00, 65.40, 0, NULL, true, 'EUR', NULL, 0, true, 'Oud', NULL, NULL, NULL, NULL, NULL),
 (3008, 'K-3008', '2026-06-29', '2026-06-29', 904, 20.00, 24.20, 24.20, '2026-06-29', true, 'EUR', '1', 0, true, 'Kassa', NULL, NULL, NULL, NULL, NULL),
 (3009, 'F-3009', '2026-06-15', '2026-06-29', 900, 10.00, 10.00, 0, NULL, false, 'EUR', NULL, 0, true, 'Vrijgesteld', NULL, NULL, NULL, NULL, NULL),
 (3010, 'F-3010', '2026-06-01', '2026-06-15', 900, 50.00, 60.50, 0, NULL, false, 'EUR', NULL, 0, false, 'Concept', NULL, NULL, NULL, NULL, NULL);

-- journaalregels verkoop (AR debet negatief)
SELECT pg_temp.acc(3001, '1200', -121.00, '2026-06-25'); SELECT pg_temp.acc(3001, '8520', 100, '2026-06-25'); SELECT pg_temp.acc(3001, '1810', 21, '2026-06-25');
SELECT pg_temp.acc(3002, '1200', -272.50, '2026-06-10'); SELECT pg_temp.acc(3002, '8530', 250, '2026-06-10'); SELECT pg_temp.acc(3002, '1811', 22.50, '2026-06-10');
SELECT pg_temp.acc(3002, '1200', 50, '2026-06-20'); SELECT pg_temp.acc(3002, '1100', -50, '2026-06-20', 'BANK-302', 'deelbetaling');
SELECT pg_temp.acc(3003, '1200', -500, '2026-05-20'); SELECT pg_temp.acc(3003, '8540', 500, '2026-05-20');
SELECT pg_temp.acc(3004, '1200', -96.80, '2026-05-05'); SELECT pg_temp.acc(3004, '8520', 80, '2026-05-05'); SELECT pg_temp.acc(3004, '1810', 16.80, '2026-05-05');
SELECT pg_temp.acc(3005, '1200', -48.40, '2026-04-15'); SELECT pg_temp.acc(3005, '8520', 40, '2026-04-15'); SELECT pg_temp.acc(3005, '1810', 8.40, '2026-04-15');
SELECT pg_temp.acc(3005, '1200', 48.40, '2026-05-01'); SELECT pg_temp.acc(3005, '1100', -48.40, '2026-05-01', 'BANK-305', NULL);
SELECT pg_temp.acc(3006, '1200', -363, '2026-04-01'); SELECT pg_temp.acc(3006, '8530', 300, '2026-04-01'); SELECT pg_temp.acc(3006, '1810', 63, '2026-04-01');
SELECT pg_temp.acc(3006, '1200', 100, '2026-04-20'); SELECT pg_temp.acc(3006, '1100', -100, '2026-04-20', 'BANK-306', 'eerste termijn');
SELECT pg_temp.acc(3007, '1200', -65.40, '2026-02-01'); SELECT pg_temp.acc(3007, '8530', 60, '2026-02-01'); SELECT pg_temp.acc(3007, '1811', 5.40, '2026-02-01');
SELECT pg_temp.acc(3008, '1200', -24.20, '2026-06-29'); SELECT pg_temp.acc(3008, '8520', 20, '2026-06-29'); SELECT pg_temp.acc(3008, '1810', 4.20, '2026-06-29');
SELECT pg_temp.acc(3008, '1200', 24.20, '2026-06-29'); SELECT pg_temp.acc(3008, '1100', -24.20, '2026-06-29', 'KASSA', NULL);
SELECT pg_temp.acc(3009, '1200', -10, '2026-06-15'); SELECT pg_temp.acc(3009, '8520', 10, '2026-06-15');
UPDATE acc_trans SET approved = false WHERE trans_id = 3010;
INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved) VALUES
 (3010, (SELECT id FROM chart WHERE accno = '1200'), -60.50, '2026-06-01', false),
 (3010, (SELECT id FROM chart WHERE accno = '8520'), 50, '2026-06-01', false),
 (3010, (SELECT id FROM chart WHERE accno = '1810'), 10.50, '2026-06-01', false);

-- factuurregels (alleen bij invoice = true)
INSERT INTO invoice (id, trans_id, parts_id, description, qty, sellprice, fxsellprice) VALUES
 (1, 3001, 1000, 'Roomboterkoek', 10, 10, 10),
 (2, 3002, 1001, 'Receptenboek', 10, 25, 25),
 (3, 3007, 1001, 'Receptenboek', 2, 25, 25),
 (4, 3007, 1000, 'Roomboterkoek (vrijgesteld?)', 1, 10, 10),
 (5, 3008, 1000, 'Roomboterkoek', 2, 10, 10);

-- aanmaanniveau en meerdere statusregels
INSERT INTO status (trans_id, formname) VALUES (3006, 'reminder1'), (3007, 'reminder2'), (3001, 'invoice');

-- inkoopfacturen (AP credit positief)
INSERT INTO ap (id, invnumber, transdate, duedate, vendor_id, netamount, amount, paid, datepaid,
                invoice, curr, department_id, approved, description)
VALUES
 (4001, 'I-4001', '2026-06-20', '2026-07-20', 905, 1000, 1210, 0, NULL, false, 'EUR', 0, true, 'Meel'),
 (4002, 'I-4002', '2026-04-10', '2026-05-10', 905, 500, 605, 300, '2026-05-15', false, 'EUR', 0, true, 'Suiker'),
 (4003, 'I-4003', '2026-01-10', '2026-02-10', 901, 100, 121, 0, NULL, false, 'EUR', 601, true, 'Verpakking');
SELECT pg_temp.acc(4001, '1600', 1210, '2026-06-20'); SELECT pg_temp.acc(4001, '8010', -1000, '2026-06-20'); SELECT pg_temp.acc(4001, '1800', -210, '2026-06-20');
SELECT pg_temp.acc(4002, '1600', 605, '2026-04-10'); SELECT pg_temp.acc(4002, '8010', -500, '2026-04-10'); SELECT pg_temp.acc(4002, '1800', -105, '2026-04-10');
SELECT pg_temp.acc(4002, '1600', -300, '2026-05-15'); SELECT pg_temp.acc(4002, '1100', 300, '2026-05-15', 'BANK-402', 'deel');
SELECT pg_temp.acc(4003, '1600', 121, '2026-01-10'); SELECT pg_temp.acc(4003, '8010', -100, '2026-01-10'); SELECT pg_temp.acc(4003, '1800', -21, '2026-01-10');
INSERT INTO dpt_trans (trans_id, department_id) VALUES (3004, 600), (4003, 601);

-- memoriaalboeking op de bank (verschijnt in het betalingenoverzicht)
INSERT INTO gl (id, reference, description, transdate, approved, curr, employee_id)
VALUES (5001, 'MEM-BANK', 'Rente', '2026-06-30', true, 'EUR', 500);
SELECT pg_temp.acc(5001, '1100', -2.50, '2026-06-30', 'RENTE', NULL); SELECT pg_temp.acc(5001, '8520', 2.50, '2026-06-30');
