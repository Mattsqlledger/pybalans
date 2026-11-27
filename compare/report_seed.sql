-- Extra testgegevens voor de rapporten (proefbalans, W&V, balans).
-- Boekingen over 2025 en 2026, met jaarafsluiting, contra-rekening,
-- afdelingen, projecten, een niet-goedgekeurde boeking en GIFI-codes.

-- GIFI-tabel aanvullen voor alle rekeningen die nog geen GIFI-omschrijving hebben
INSERT INTO gifi (accno, description)
  SELECT c.gifi_accno, 'GIFI ' || c.description FROM chart c
  WHERE c.gifi_accno <> '' AND NOT EXISTS (SELECT 1 FROM gifi g WHERE g.accno = c.gifi_accno)
    AND c.accno = (SELECT min(c2.accno) FROM chart c2 WHERE c2.gifi_accno = c.gifi_accno);
UPDATE chart SET contra = 't' WHERE accno = '0235';
INSERT INTO translation (trans_id, language_code, description)
  SELECT id, 'en', 'Revenue A' FROM chart WHERE accno = '8520';
INSERT INTO translation (trans_id, language_code, description)
  SELECT id, 'en', 'Cost accounts' FROM chart WHERE accno = '4000';
INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2026-06-30', 1.25);

CREATE FUNCTION pg_temp.gl(tid int, d date, ref text, dept int, accnos text[], amounts numeric[],
                           projects int[] DEFAULT NULL, appr bool DEFAULT true) RETURNS void AS $$
BEGIN
  INSERT INTO gl (id, reference, description, transdate, department_id, approved, curr, exchangerate)
  VALUES (tid, ref, ref, d, COALESCE(dept, 0), appr, 'EUR', 1);
  IF dept IS NOT NULL THEN
    INSERT INTO dpt_trans (trans_id, department_id) VALUES (tid, dept);
  END IF;
  FOR i IN 1 .. array_length(accnos, 1) LOOP
    INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved, project_id)
    VALUES (tid, (SELECT id FROM chart WHERE accno = accnos[i]), amounts[i], d, appr,
            CASE WHEN projects IS NULL THEN NULL ELSE projects[i] END);
  END LOOP;
END $$ LANGUAGE plpgsql;

-- 2025 (debet negatief, credit positief)
SELECT pg_temp.gl(20001, '2025-01-01', 'OPEN-2025', NULL, ARRAY['1110','0500'], ARRAY[-25000, 25000]);
SELECT pg_temp.gl(20002, '2025-01-15', 'INV-2025', NULL, ARRAY['0230','1110'], ARRAY[-6000, 6000]);
SELECT pg_temp.gl(20003, '2025-03-31', 'OMZ-2025Q1', NULL, ARRAY['1110','8520','8530'], ARRAY[-12000.10, 9000.05, 3000.05]);
SELECT pg_temp.gl(20004, '2025-06-30', 'OMZ-2025Q2', 600, ARRAY['1110','8520'], ARRAY[-15000, 15000]);
SELECT pg_temp.gl(20005, '2025-06-30', 'LOON-2025H1', NULL, ARRAY['4100','4120','1110'], ARRAY[-8000, -1600.33, 9600.33]);
SELECT pg_temp.gl(20006, '2025-12-31', 'AFS-2025', NULL, ARRAY['4330','0235'], ARRAY[-1200, 1200]);
SELECT pg_temp.gl(20007, '2025-12-31', 'RENTE-2025', NULL, ARRAY['1110','9050'], ARRAY[-45.67, 45.67]);
-- jaarafsluiting 2025: resultaat (27000.10+45.67-9600.33-1200 = 16245.44) naar onverdeeld resultaat
SELECT pg_temp.gl(20008, '2025-12-31', 'JAARAFSLUITING-2025', NULL,
       ARRAY['8520','8530','9050','4100','4120','4330','0550'],
       ARRAY[-24000.05, -3000.05, -45.67, 8000, 1600.33, 1200, 16245.44]);
INSERT INTO yearend (trans_id, transdate) VALUES (20008, '2025-12-31');

-- 2026
SELECT pg_temp.gl(20101, '2026-01-31', 'OMZ-2026-01', 600, ARRAY['1110','8520'], ARRAY[-4000, 4000], ARRAY[700, 700]);
SELECT pg_temp.gl(20102, '2026-02-28', 'OMZ-2026-02', 601, ARRAY['1110','8520','8540'], ARRAY[-5100.50, 4100.25, 1000.25]);
SELECT pg_temp.gl(20103, '2026-02-15', 'LOON-2026-02', NULL, ARRAY['4100','4110','1110'], ARRAY[-3000, -333.33, 3333.33], ARRAY[700, 701, NULL]);
SELECT pg_temp.gl(20104, '2026-03-15', 'OMZ-2026-03', NULL, ARRAY['1110','8530'], ARRAY[-2500, 2500]);
SELECT pg_temp.gl(20105, '2026-04-10', 'KOSTEN-2026-04', 600, ARRAY['4220','4210','1110'], ARRAY[-120.40, -80.60, 201], ARRAY[701, 701, NULL]);
SELECT pg_temp.gl(20106, '2026-05-20', 'OMZ-2026-05', NULL, ARRAY['1110','8520'], ARRAY[-7000, 7000]);
SELECT pg_temp.gl(20107, '2026-06-30', 'AFS-2026H1', NULL, ARRAY['4330','0235'], ARRAY[-600, 600]);
SELECT pg_temp.gl(20108, '2026-06-30', 'RENTE-2026H1', NULL, ARRAY['1110','9050'], ARRAY[-0.004, 0.004]);
SELECT pg_temp.gl(20109, '2026-06-15', 'CONCEPT-NIET-GOEDGEKEURD', NULL, ARRAY['4100','1110'], ARRAY[-999, 999], NULL, false);
SELECT pg_temp.gl(20110, '2026-07-05', 'OMZ-2026-07', NULL, ARRAY['1110','8520'], ARRAY[-1500, 1500]);
