INSERT INTO curr (rn, curr, prec) VALUES (1,'EUR',2),(2,'USD',2);
INSERT INTO defaults (fldname, fldvalue) VALUES
 ('company','Testbedrijf BV'), ('roundchange','0.01'), ('glnumber','GL-0099'),
 ('vouchernumber','V0001'), ('audittrail','1'), ('closedto','20251231'), ('revtrans','0');
INSERT INTO defaults (fldname, fldvalue) SELECT 'fxgainloss_accno_id', id::text FROM chart WHERE accno = '9220';
INSERT INTO employee (id, login, name) VALUES (500,'admin','Beheerder'),(501,'jan','Jan Jansen');
INSERT INTO department (id, description, role, rn) VALUES (600,'Verkoop','P',1),(601,'Inkoop','C',2);
INSERT INTO project (id, projectnumber, description) VALUES (700,'P-001','Website'),(701,'P-002','Verbouwing');
INSERT INTO translation (trans_id, language_code, description) SELECT id,'en','Bank (EN)' FROM chart WHERE accno='1100';
INSERT INTO gifi (accno, description) VALUES ('G1','Gifi kas');
UPDATE chart SET gifi_accno='G1' WHERE accno='1001';
INSERT INTO br (id, batchnumber, description, batch, transdate, amount) VALUES (800,'B1','Memoriaal batch','general_ledger','2026-02-01',0);
-- AR/AP data om de UNION in de overzichten te testen
INSERT INTO customer (id, name, customernumber) VALUES (900,'Klant A','K001');
INSERT INTO vendor (id, name, vendornumber) VALUES (901,'Leverancier B','L001');
INSERT INTO address (id, trans_id, address1, city, zipcode, country) VALUES (1,900,'Dorpsstraat 1','Utrecht','3511AA','NL'),(2,901,'Kade 2','Rotterdam','3011BB','NL');
INSERT INTO ar (id, invnumber, transdate, customer_id, amount, netamount, invoice, approved, description, department_id) VALUES (950,'F2026-001','2026-03-01',900,121,100,'f','t','Advies',0);
INSERT INTO ap (id, invnumber, transdate, vendor_id, amount, netamount, invoice, approved, description, department_id) VALUES (951,'I-77','2026-03-05',901,60.5,50,'f','t','',0);
INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved) VALUES
 (950,(SELECT id FROM chart WHERE accno='1200'),-121,'2026-03-01','t'),
 (950,(SELECT id FROM chart WHERE accno='8520'),100,'2026-03-01','t'),
 (950,(SELECT id FROM chart WHERE accno='1810'),21,'2026-03-01','t'),
 (950,(SELECT id FROM chart WHERE accno='1200'),121,'2026-03-10','t'),
 (950,(SELECT id FROM chart WHERE accno='1100'),-121,'2026-03-10','t'),
 (951,(SELECT id FROM chart WHERE accno='1600'),60.5,'2026-03-05','t'),
 (951,(SELECT id FROM chart WHERE accno='8010'),-50,'2026-03-05','t'),
 (951,(SELECT id FROM chart WHERE accno='1800'),-10.5,'2026-03-05','t');
UPDATE chart SET link='AR_paid:AP_paid' WHERE accno='1100';
