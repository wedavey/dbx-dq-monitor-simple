-- Framework tables. ${catalog} and ${schema} are substituted by `dq-setup`.
-- Statements are idempotent (IF NOT EXISTS). Avoid semicolons inside comments and strings:
-- the setup task splits this file on them.

CREATE SCHEMA IF NOT EXISTS ${catalog}.${schema};

-- Metric catalog: governance-owned definitions, loaded from config/metric_catalog.yml on deploy.
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.metric_catalog (
  metric_id      STRING  NOT NULL COMMENT 'Stable key, e.g. null_pct',
  metric_name    STRING  NOT NULL COMMENT 'Friendly name shown on dashboards',
  dimension      STRING  NOT NULL COMMENT 'completeness, uniqueness, validity, freshness, volume or consistency',
  scope          STRING  NOT NULL COMMENT 'COLUMN or TABLE',
  sql_template   STRING  NOT NULL COMMENT 'Aggregate SQL expression. {col} is the column, other {names} are params',
  direction      STRING  NOT NULL COMMENT 'MAX fails when value > threshold. MIN fails when value < threshold',
  description    STRING  COMMENT 'Governance definition in plain English',
  version        INT     NOT NULL COMMENT 'Increment when the definition changes',
  is_active      BOOLEAN NOT NULL,
  updated_at     TIMESTAMP,
  updated_by     STRING,
  CONSTRAINT metric_catalog_pk PRIMARY KEY (metric_id)
)
COMMENT 'Governance-owned metric definitions for the DQ framework';

-- Assignments: the validated set the runner executes. Rebuilt by `dq-sync-assignments` from the
-- YAML assignment files. Do not edit by hand: edit the YAML instead.
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.assignments (
  assignment_id  STRING  NOT NULL COMMENT 'table|column|metric, lower-case',
  table_name     STRING  NOT NULL COMMENT 'catalog.schema.table',
  column_name    STRING  COMMENT 'NULL for table-level metrics',
  metric_id      STRING  NOT NULL COMMENT 'FK to metric_catalog',
  threshold      DOUBLE  NOT NULL,
  severity       STRING  NOT NULL COMMENT 'LOW, MEDIUM, HIGH or CRITICAL',
  owner          STRING  NOT NULL COMMENT 'Email or group the alert is routed to',
  params         MAP<STRING, STRING> COMMENT 'Metric parameters, e.g. regex, and the optional row filter',
  data_product   STRING  COMMENT 'Grouping for dashboards',
  is_enabled     BOOLEAN NOT NULL,
  updated_at     TIMESTAMP,
  updated_by     STRING,
  CONSTRAINT assignments_pk PRIMARY KEY (assignment_id),
  CONSTRAINT assignments_metric_fk FOREIGN KEY (metric_id)
    REFERENCES ${catalog}.${schema}.metric_catalog (metric_id)
)
COMMENT 'Which metrics apply to which tables and columns, with thresholds and owners';

-- Problems found while building assignments (unknown columns, invalid rows, duplicates).
-- Overwritten on every sync: it always shows the current state, so fixed problems disappear.
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.assignment_issues (
  run_id         STRING    NOT NULL,
  run_ts         TIMESTAMP NOT NULL,
  severity       STRING    NOT NULL COMMENT 'ERROR means the assignment was dropped',
  table_name     STRING,
  column_name    STRING,
  message        STRING    NOT NULL
)
COMMENT 'Current problems with the assignment YAML, shown back to the people who own it';

-- Results: append-only, one row per assignment per run. Threshold, severity and owner are
-- snapshotted so history stays correct when an assignment changes later.
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.results (
  run_id         STRING    NOT NULL,
  run_ts         TIMESTAMP NOT NULL,
  assignment_id  STRING    NOT NULL,
  table_name     STRING    NOT NULL,
  column_name    STRING,
  metric_id      STRING    NOT NULL,
  metric_version INT,
  dimension      STRING,
  metric_value   DOUBLE    COMMENT 'NULL when the check errored',
  threshold      DOUBLE,
  direction      STRING,
  status         STRING    NOT NULL COMMENT 'PASS, FAIL or ERROR',
  severity       STRING,
  owner          STRING,
  data_product   STRING,
  error_message  STRING,
  run_date       DATE      GENERATED ALWAYS AS (CAST(run_ts AS DATE))
)
COMMENT 'Standardised DQ results, one row per assignment per run';
