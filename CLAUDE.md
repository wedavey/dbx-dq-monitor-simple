# CLAUDE.md

Databricks Asset Bundle (DAB) for **Databricks Free Edition**, hosting a config-driven
**data quality (DQ) monitoring service** (`dq_framework`).

## Free Edition constraints
- **Serverless only.** Never add `job_clusters`, `new_cluster`, `existing_cluster_id` or
  node types. Jobs declare an `environments:` block and each task sets `environment_key`.
- One workspace. dev/test/prod are separated by **schema**, not by workspace.
- Code ships as a wheel (`python_wheel_task`), built by `uv build --wheel`.

## Layout
- `databricks.yml` – bundle, variables, targets, wheel artifact.
- `resources/*.yml` – job definitions (auto-included).
- `src/dq_framework/` – DQ monitoring service. Pure logic (`engine`, `rendering`, `evaluation`,
  `assignments_sync`, `config_io`) has no Spark imports; `spark_io.py` and
  `cli.py` (one entry point per job task) do the Spark I/O. `config/*.yml` (metric catalog,
  assignments) and `sql/*.sql` ship inside the wheel as package data.
- `resources/dq_jobs.yml` – DQ jobs `dq_deploy_config`, `dq_daily_run`. The dev/test-only
  `dq_synthetic_smoke_test` job is declared under those targets in `databricks.yml`.
- `docs/assignments.md` – how to onboard tables/columns to DQ checks.
- `tests/` – pytest with a local SparkSession (`conftest.py`); `tests/helpers.py` holds DQ
  builders; `tests/integration/` runs the metric SQL on local Spark.

## Targets and variables
| target | DQ schema | mode        |
|--------|-----------|-------------|
| dev (default) | `dq_dev`  | development (names prefixed per user, schedules paused) |
| test   | `dq_test` | -           |
| prod   | `dq_prod` | -           |

Variables: `catalog` (default `workspace`, the Free Edition catalog) and `dq_schema`
(`dq_dev` / `dq_test` / `dq_prod`, holds the DQ tables). Other DQ variables:
`assignment_files`, `schedule_status` (only prod is UNPAUSED), `ops_email`, `max_workers`.
Reference them as `${var.catalog}` / `${var.dq_schema}`. DQ tables in
`<catalog>.<dq_schema>`: `metric_catalog`, `assignments`, `assignment_issues`, `results` plus
`v_*` views. The synthetic test data lives in the fixed schema `dq_synthetic` (shared by dev
and test).

## Conventions
- Keep DQ logic in pure functions where practical; keep Spark I/O in `spark_io.py` and `cli.py`.
- Add pytest coverage for new behavior. Use explicit schemas for Spark test data containing
  all-null columns.
- Never hardcode catalog/schema; pass them via job parameters from bundle variables.
- New jobs go in `resources/`, use `${bundle.target}` in the name, and serverless only.
- Do not commit `dist/`, `.databricks/` or `.venv/`.

## DQ monitoring service
Governance defines metrics (`metric_catalog.yml`); tables/columns are assigned in YAML
(`assignments.yml`, one reviewed PR per change); `dq-run` scans each table once and appends to `results`.
It is monitor/alert only. A failing check never fails the job.
- All DQ entry points (`dq-*`) are in the `dq_monitor` wheel, so DQ tasks use
  `package_name: dq_monitor`.
- `dq-seed-synthetic` / `dq-verify-synthetic` refuse to run outside dev/test; never add the
  smoke-test job to prod.
- Metric SQL: identifiers are validated and back-quoted, parameters only inside quoted literals.
  Keep it that way.
- Changing `metric_catalog.yml` is a governance-reviewed change.
- `dq-validate-config` statically checks config (no Spark); run it after editing config.
- Setup order after deploy: `dq_deploy_config`, then `dq_synthetic_smoke_test` (dev/test).

## Develop and test
```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
.venv/bin/python -m pytest      # needs Java 17+
.venv/bin/dq-validate-config    # static DQ config checks
```

## Deploy
Requires the Databricks CLI (>=0.250) and `uv`. Authenticate once with
`databricks auth login --host <free-edition-workspace-url>`.
```bash
databricks bundle validate -t dev
databricks bundle deploy   -t dev          # or test / prod
databricks bundle run dq_deploy_config -t dev        # DQ tables, views, metrics, assignments
databricks bundle run dq_synthetic_smoke_test -t dev # dev/test only: end-to-end check
databricks bundle destroy  -t dev          # clean up
```
Promote by deploying the same commit to `test`, then `prod`.
