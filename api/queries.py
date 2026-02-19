import os
from typing import Optional
from google.cloud import bigquery

BQ_TABLE = os.environ.get(
    "BQ_TABLE",
    "remote-machine-b7af52b6.ofac_sanctions.sdn_list",
)

SCREEN_SQL = """
WITH all_names AS (
  SELECT
    sdn_entry_id, sdn_type,
    primary_name.full_name AS primary_name,
    all_name, programs, legal_authorities,
    dates_of_birth, nationalities, remarks
  FROM `{table}`,
    UNNEST(ARRAY_CONCAT(
      IF(primary_name.full_name IS NOT NULL, [primary_name.full_name], []),
      ARRAY(SELECT a.full_name FROM UNNEST(aliases) AS a WHERE a.full_name IS NOT NULL)
    )) AS all_name
)
SELECT *,
  CASE
    WHEN LOWER(all_name) = LOWER(@name)                             THEN 1
    WHEN EDIT_DISTANCE(LOWER(all_name), LOWER(@name)) <= 2          THEN 2
    WHEN EDIT_DISTANCE(LOWER(all_name), LOWER(@name)) <= @threshold THEN 3
    WHEN SOUNDEX(all_name) = SOUNDEX(@name)                         THEN 4
    ELSE 5
  END AS match_score,
  EDIT_DISTANCE(LOWER(all_name), LOWER(@name)) AS edit_distance
FROM all_names
WHERE LOWER(all_name) = LOWER(@name)
  OR EDIT_DISTANCE(LOWER(all_name), LOWER(@name)) <= @threshold
  OR SOUNDEX(all_name) = SOUNDEX(@name)
ORDER BY match_score, edit_distance
LIMIT @limit
"""

ENTRY_SQL = """
SELECT *
FROM `{table}`
WHERE sdn_entry_id = @sdn_entry_id
LIMIT 1
"""


def _coerce_row(row) -> dict:
    """Convert a BQ Row to a plain dict, handling repeated fields."""
    d = dict(row)
    for key in ("programs", "legal_authorities", "dates_of_birth", "nationalities"):
        val = d.get(key)
        if val is None:
            d[key] = []
        elif not isinstance(val, list):
            d[key] = list(val)
    return d


def screen_names(
    client: bigquery.Client,
    name: str,
    threshold: int,
    limit: int,
) -> list[dict]:
    sql = SCREEN_SQL.format(table=BQ_TABLE)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", name),
            bigquery.ScalarQueryParameter("threshold", "INT64", threshold),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    rows = client.query(sql, job_config=job_config).result()
    return [_coerce_row(row) for row in rows]


def get_entry(
    client: bigquery.Client,
    sdn_entry_id: int,
) -> Optional[dict]:
    sql = ENTRY_SQL.format(table=BQ_TABLE)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("sdn_entry_id", "INT64", sdn_entry_id),
        ]
    )
    rows = list(client.query(sql, job_config=job_config).result())
    if not rows:
        return None
    return dict(rows[0])
