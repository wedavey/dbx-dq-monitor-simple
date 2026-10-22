-- Views the dashboard and alerts read (never the raw results table).
-- ${catalog} and ${schema} are substituted by `dq-setup`.

CREATE OR REPLACE VIEW ${catalog}.${schema}.v_latest_results AS
SELECT * EXCEPT (rn)
FROM (
  SELECT r.*,
         row_number() OVER (PARTITION BY assignment_id ORDER BY run_ts DESC) AS rn
  FROM ${catalog}.${schema}.results r
)
WHERE rn = 1;

CREATE OR REPLACE VIEW ${catalog}.${schema}.v_daily_pass_rate AS
SELECT run_date,
       data_product,
       dimension,
       count(*)                                                       AS checks,
       sum(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END)               AS passed,
       round(avg(CASE WHEN status = 'PASS' THEN 1.0 ELSE 0.0 END), 4) AS pass_rate
FROM ${catalog}.${schema}.results
GROUP BY run_date, data_product, dimension;

-- Enabled assignments with no result in the last 26 hours (silent runner failure, new onboarding).
CREATE OR REPLACE VIEW ${catalog}.${schema}.v_missing_results AS
SELECT a.assignment_id, a.table_name, a.column_name, a.metric_id, a.owner
FROM ${catalog}.${schema}.assignments a
LEFT JOIN (
  SELECT DISTINCT assignment_id
  FROM ${catalog}.${schema}.results
  WHERE run_ts >= current_timestamp() - INTERVAL 26 HOURS
) r USING (assignment_id)
WHERE a.is_enabled AND r.assignment_id IS NULL;

-- One row per monitored table: lets governance see that their assignments took effect.
CREATE OR REPLACE VIEW ${catalog}.${schema}.v_onboarding_status AS
SELECT a.table_name,
       any_value(a.data_product)                                        AS data_product,
       any_value(a.owner)                                               AS owner,
       count(*)                                                         AS assignments,
       sum(CASE WHEN a.is_enabled THEN 1 ELSE 0 END)                    AS enabled,
       sum(CASE WHEN r.status = 'FAIL' THEN 1 ELSE 0 END)               AS failing,
       sum(CASE WHEN r.status = 'ERROR' THEN 1 ELSE 0 END)              AS errored,
       max(r.run_ts)                                                    AS last_run
FROM ${catalog}.${schema}.assignments a
LEFT JOIN ${catalog}.${schema}.v_latest_results r ON r.assignment_id = a.assignment_id
GROUP BY a.table_name;
