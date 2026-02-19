-- =============================================================================
-- OFAC SDN Sanctions List — Test Queries
-- Project: remote-machine-b7af52b6
-- Dataset: ofac_sanctions
-- Table:   sdn_list
-- =============================================================================
-- Run these after the first ingestion to validate data completeness
-- and fuzzy name matching capabilities.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. DATA COMPLETENESS CHECKS
-- ─────────────────────────────────────────────────────────────────────────────

-- 1a. Total record count (should be ~12,000-14,000 for the current SDN list)
SELECT
  COUNT(*) AS total_records,
  COUNTIF(primary_name.full_name IS NOT NULL)  AS records_with_name,
  COUNTIF(ARRAY_LENGTH(programs) > 0)           AS records_with_programs,
  COUNTIF(ARRAY_LENGTH(aliases) > 0)            AS records_with_aliases,
  COUNTIF(ARRAY_LENGTH(addresses) > 0)          AS records_with_addresses,
  COUNTIF(ARRAY_LENGTH(id_documents) > 0)       AS records_with_ids,
  COUNTIF(vessel_info IS NOT NULL)              AS vessel_records,
  COUNTIF(aircraft_info IS NOT NULL)            AS aircraft_records,
  COUNTIF(ARRAY_LENGTH(dates_of_birth) > 0)     AS records_with_dob,
  COUNTIF(ARRAY_LENGTH(nationalities) > 0)      AS records_with_nationality,
  MIN(publication_date)                          AS earliest_pub_date,
  MAX(publication_date)                          AS latest_pub_date,
  MAX(ingestion_timestamp)                       AS last_ingested_at
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`;


-- 1b. Records by entity type
SELECT
  sdn_type,
  COUNT(*) AS count
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
GROUP BY sdn_type
ORDER BY count DESC;


-- 1c. Records by top sanctions programs
SELECT
  program,
  COUNT(*) AS entity_count
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
  UNNEST(programs) AS program
GROUP BY program
ORDER BY entity_count DESC
LIMIT 20;


-- 1d. Alias type distribution
SELECT
  a.alias_type,
  a.alias_quality,
  COUNT(*) AS count
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
  UNNEST(aliases) AS a
GROUP BY a.alias_type, a.alias_quality
ORDER BY count DESC;


-- 1e. Countries most represented in addresses
SELECT
  addr.country,
  COUNT(*) AS address_count
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
  UNNEST(addresses) AS addr
WHERE addr.country IS NOT NULL AND addr.country != ''
GROUP BY addr.country
ORDER BY address_count DESC
LIMIT 20;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. EXACT NAME SEARCH
-- ─────────────────────────────────────────────────────────────────────────────

-- 2a. Exact match by primary name (case-insensitive)
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name,
  programs,
  legal_authorities,
  remarks
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE LOWER(primary_name.full_name) = LOWER('SADDAM HUSSEIN')
ORDER BY sdn_entry_id;


-- 2b. Exact match on any name (primary or alias)
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name AS primary_name,
  matched_name,
  programs
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
  UNNEST(
    ARRAY_CONCAT(
      [primary_name.full_name],
      ARRAY(SELECT a.full_name FROM UNNEST(aliases) AS a)
    )
  ) AS matched_name
WHERE LOWER(matched_name) = LOWER('AL-QAIDA')
ORDER BY sdn_entry_id;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. FUZZY NAME SEARCH — TYPO TOLERANCE
-- ─────────────────────────────────────────────────────────────────────────────

-- 3a. Edit distance fuzzy search — primary name only
-- Catches typos like "Sadam Husain" for "Saddam Hussein" (edit distance = 3)
-- Adjust the threshold (<=3) based on desired sensitivity:
--   <=1 = very tight (one transposition or swap)
--   <=2 = moderate (common typos)
--   <=3 = loose (more permissive, some false positives)
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name,
  EDIT_DISTANCE(LOWER(primary_name.full_name), LOWER('Sadam Husain')) AS edit_dist,
  programs,
  remarks
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE EDIT_DISTANCE(LOWER(primary_name.full_name), LOWER('Sadam Husain')) <= 3
ORDER BY edit_dist, primary_name.full_name
LIMIT 20;


-- 3b. Edit distance fuzzy search — primary name OR any alias
-- Best for comprehensive screening — catches all name variants
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name,
  matched_name,
  edit_dist,
  programs,
  legal_authorities
FROM (
  SELECT
    sdn_entry_id,
    sdn_type,
    primary_name,
    programs,
    legal_authorities,
    addresses,
    id_documents,
    all_name,
    EDIT_DISTANCE(LOWER(all_name), LOWER('Osama Bin Ladin')) AS edit_dist
  FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
    UNNEST(
      ARRAY_CONCAT(
        IF(primary_name.full_name IS NOT NULL, [primary_name.full_name], []),
        ARRAY(SELECT a.full_name FROM UNNEST(aliases) AS a WHERE a.full_name IS NOT NULL)
      )
    ) AS all_name
)
WHERE edit_dist <= 3
ORDER BY edit_dist, primary_name.full_name
LIMIT 20;


-- 3c. SOUNDEX phonetic matching — catches phonetic misspellings
-- Good for names where the spelling varies but pronunciation is similar
-- e.g. "Khadhafi" vs "Gaddafi" vs "Qaddafi"
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name,
  programs
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE SOUNDEX(primary_name.full_name) = SOUNDEX('Kadafi')
  OR EXISTS (
    SELECT 1 FROM UNNEST(aliases) AS a
    WHERE SOUNDEX(a.full_name) = SOUNDEX('Kadafi')
  )
ORDER BY primary_name.full_name
LIMIT 20;


-- 3d. Full-text SEARCH — uses BigQuery search index for fast keyword matching
-- Works well for multi-word names and partial matches
-- Requires: CREATE SEARCH INDEX on primary_name.full_name (done in deploy.sh)
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name,
  programs
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE SEARCH(primary_name.full_name, 'hussein')
ORDER BY primary_name.full_name
LIMIT 20;


-- 3e. Combined fuzzy search — tiered scoring for comprehensive matching
-- Returns ranked results with a match_score:
--   score 1 = exact match (highest confidence)
--   score 2 = edit distance ≤ 2 (high confidence)
--   score 3 = edit distance ≤ 4 (medium confidence)
--   score 4 = SOUNDEX match (phonetic)
--   score 5 = keyword present (lowest confidence)
WITH query_name AS (
  SELECT 'USAMA BIN LADIN' AS name  -- ← Change this to your search term
),
all_names AS (
  SELECT
    sdn_entry_id,
    sdn_type,
    primary_name.full_name AS primary_name,
    all_name,
    programs,
    legal_authorities,
    addresses,
    id_documents,
    dates_of_birth,
    nationalities,
    remarks
  FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
    UNNEST(
      ARRAY_CONCAT(
        IF(primary_name.full_name IS NOT NULL, [primary_name.full_name], []),
        ARRAY(SELECT a.full_name FROM UNNEST(aliases) AS a WHERE a.full_name IS NOT NULL)
      )
    ) AS all_name,
    query_name
)
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name,
  all_name          AS matched_name,
  programs,
  legal_authorities,
  dates_of_birth,
  nationalities,
  CASE
    WHEN LOWER(all_name) = LOWER((SELECT name FROM query_name))          THEN 1
    WHEN EDIT_DISTANCE(LOWER(all_name), LOWER((SELECT name FROM query_name))) <= 2 THEN 2
    WHEN EDIT_DISTANCE(LOWER(all_name), LOWER((SELECT name FROM query_name))) <= 4 THEN 3
    WHEN SOUNDEX(all_name) = SOUNDEX((SELECT name FROM query_name))       THEN 4
    ELSE 5
  END AS match_score,
  EDIT_DISTANCE(LOWER(all_name), LOWER((SELECT name FROM query_name))) AS edit_dist
FROM all_names
WHERE
  LOWER(all_name) = LOWER((SELECT name FROM query_name))
  OR EDIT_DISTANCE(LOWER(all_name), LOWER((SELECT name FROM query_name))) <= 4
  OR SOUNDEX(all_name) = SOUNDEX((SELECT name FROM query_name))
ORDER BY match_score, edit_dist
LIMIT 50;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. SPECIFIC KNOWN-ENTITY LOOKUPS (validation tests)
-- ─────────────────────────────────────────────────────────────────────────────

-- 4a. Look up Al-Qaida / SDGT-related entities
SELECT
  sdn_entry_id,
  sdn_type,
  primary_name.full_name,
  programs,
  legal_authorities,
  ARRAY_LENGTH(aliases) AS alias_count,
  ARRAY_LENGTH(addresses) AS address_count
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE 'SDGT' IN UNNEST(programs)
ORDER BY primary_name.full_name
LIMIT 10;


-- 4b. Vessel entities (for maritime sanctions)
SELECT
  sdn_entry_id,
  primary_name.full_name,
  vessel_info.vessel_type,
  vessel_info.vessel_flag,
  vessel_info.vessel_mmsi,
  vessel_info.vessel_imo,
  vessel_info.vessel_owner,
  programs
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE sdn_type = 'Vessel'
  AND vessel_info IS NOT NULL
ORDER BY primary_name.full_name
LIMIT 10;


-- 4c. Individual with passport
SELECT
  sdn_entry_id,
  primary_name.full_name,
  nationalities,
  dates_of_birth,
  doc.id_type,
  doc.id_number,
  doc.country AS doc_country,
  programs
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`,
  UNNEST(id_documents) AS doc
WHERE sdn_type = 'Individual'
  AND doc.id_type = 'Passport'
ORDER BY primary_name.full_name
LIMIT 10;


-- 4d. Full record for a specific entity (by known OFAC FixedRef ID)
-- Example: FixedRef 7771 is USAMA BIN LADIN in the OFAC database
SELECT *
FROM `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
WHERE sdn_entry_id = 7771;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. CREATE SEARCH INDEX (run once after table creation)
-- ─────────────────────────────────────────────────────────────────────────────
-- This DDL is run by deploy.sh. Included here for reference.

/*
CREATE SEARCH INDEX IF NOT EXISTS sdn_name_index
ON `remote-machine-b7af52b6.ofac_sanctions.sdn_list`
(primary_name.full_name);
*/
