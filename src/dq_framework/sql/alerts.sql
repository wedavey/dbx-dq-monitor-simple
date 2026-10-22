-- DBSQL alert queries. Not run by the framework: save each as a query in Databricks SQL, then create
-- an Alert on it (condition: first row, column alert_count > 0). Replace ${catalog}.${schema}.
-- Route by audience: 7a, 7d go to data owners. 7b, 7c go to the platform team. 7e goes to governance.

-- 7a. Critical and high failures from the latest run
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(severity, ' | ', table_name, '.', coalesce(column_name, '*'),
                ' | ', metric_id, ' = ', round(metric_value, 4),
                ' (', direction, ' ', threshold, ') | owner: ', owner))) AS details
FROM ${catalog}.${schema}.v_latest_results
WHERE status = 'FAIL'
  AND severity IN ('CRITICAL', 'HIGH')
  AND run_ts >= current_timestamp() - INTERVAL 26 HOURS;

-- 7b. Checks that errored (dropped column, permissions, NULL metric)
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(table_name, '.', coalesce(column_name, '*'), ' | ', metric_id,
                ' | ', error_message))) AS details
FROM ${catalog}.${schema}.v_latest_results
WHERE status = 'ERROR'
  AND run_ts >= current_timestamp() - INTERVAL 26 HOURS;

-- 7c. Runner health: enabled assignments with no recent result
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(table_name, '.', coalesce(column_name, '*'), ' | ', metric_id))) AS details
FROM ${catalog}.${schema}.v_missing_results;

-- 7d. Persistent failures: medium and low severity checks that failed three runs in a row
WITH ranked AS (
  SELECT assignment_id, table_name, column_name, metric_id, status, owner,
         row_number() OVER (PARTITION BY assignment_id ORDER BY run_ts DESC) AS rn
  FROM ${catalog}.${schema}.results
  WHERE severity IN ('MEDIUM', 'LOW')
), persistent AS (
  SELECT assignment_id, any_value(table_name) AS table_name,
         any_value(column_name) AS column_name, any_value(metric_id) AS metric_id,
         any_value(owner) AS owner
  FROM ranked
  WHERE rn <= 3
  GROUP BY assignment_id
  HAVING count(*) = 3 AND sum(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) = 3
)
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(table_name, '.', coalesce(column_name, '*'), ' | ', metric_id,
                ' | owner: ', owner))) AS details
FROM persistent;

-- 7e. Assignment problems (unknown columns, invalid rows, duplicates) for the governance team
SELECT count(*) AS alert_count,
       concat_ws('\n', collect_list(
         concat(severity, ' | ', coalesce(table_name, '*'), '.',
                coalesce(column_name, '*'), ' | ', message))) AS details
FROM ${catalog}.${schema}.assignment_issues;
