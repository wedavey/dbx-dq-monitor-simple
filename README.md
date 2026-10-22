# dbx-dq-monitor

A Databricks Asset Bundle for **Databricks Free Edition** (serverless only; dev/test/prod are
separated by schema in one workspace). It contains:

1. **CRM demo** (`src/crm_demo`): generates fake contacts and cleanses them with PySpark.
2. **DQ monitoring service** (`src/dq_framework`): config-driven data quality checks.

## DQ monitoring service

Governance defines **metrics**; tables and columns are assigned to metrics through
**assignments** (Unity Catalog `dq_` tags, YAML, a CSV or a feed table). A scheduled job runs the
checks and appends to one standard **results** table that views, dashboards and alerts read.
Monitor and alert only: nothing gates a pipeline.

| Job | Targets | Does |
|---|---|---|
| `dq_deploy_config` | all | Create/upgrade tables and views, load the metric catalog, rebuild assignments |
| `dq_daily_run` | all | Rebuild assignments, execute enabled checks (05:30 Sydney; unpaused only in prod) |
| `dq_synthetic_smoke_test` | dev, test | Seed synthetic tables with known defects, run checks, assert outcomes |

Tables live in `<catalog>.<dq_schema>` (`dq_dev`, `dq_test`, `dq_prod`): `metric_catalog`,
`assignments`, `assignment_issues`, `results`, and views `v_latest_results`, `v_daily_pass_rate`,
`v_missing_results`, `v_onboarding_status`. Example DBSQL alerts are in
`src/dq_framework/sql/alerts.sql`. See [docs/assignments.md](docs/assignments.md) for onboarding.

Design notes: one scan per table; per-check error isolation (a NULL metric is `ERROR`); results
are a snapshot including threshold, severity and owner; identifiers validated and back-quoted;
the assignment sync refuses to shrink enabled assignments by more than half.

Open points for governance: NULL metric = `ERROR` vs `FAIL`; who owns assignments in prod;
severity precedence and default thresholds in `tag_rules.yml`; run identity needs SELECT on
monitored tables and BROWSE on tagged catalogs.

## Develop and test

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
.venv/bin/python -m pytest      # needs Java 17+
.venv/bin/dq-validate-config
```

## Deploy

Requires the Databricks CLI (>=0.250) and `uv`.

```bash
databricks auth login --host <free-edition-workspace-url>
databricks bundle validate -t dev
databricks bundle deploy   -t dev                       # or test / prod
databricks bundle run crm_contacts -t dev
databricks bundle run dq_deploy_config -t dev
databricks bundle run dq_synthetic_smoke_test -t dev    # dev/test only
databricks bundle destroy  -t dev
```

Promote by deploying the same commit to `test`, then `prod`. Set `ops_email` (failure
notifications, default is a placeholder) and `tag_catalogs` per target as needed.
