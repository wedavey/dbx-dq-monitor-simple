# dbx-dq-monitor

A Databricks Asset Bundle for **Databricks Free Edition** (serverless only; dev/test/prod are
separated by schema in one workspace). It contains a config-driven **DQ monitoring service**
(`src/dq_framework`).

## DQ monitoring service

Governance defines **metrics**; tables and columns are assigned to metrics through
**assignments** (YAML files in the wheel, changed by pull request). A scheduled job runs the
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
are a snapshot including threshold, severity and owner; identifiers validated and back-quoted.

Open points for governance: NULL metric = `ERROR` vs `FAIL`; who reviews assignment PRs in prod;
the run identity needs SELECT on monitored tables.

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
databricks bundle run dq_deploy_config -t dev
databricks bundle run dq_synthetic_smoke_test -t dev    # dev/test only
databricks bundle destroy  -t dev
```

Promote by deploying the same commit to `test`, then `prod`. Set `ops_email` (failure
notifications, default is a placeholder) per target as needed.
