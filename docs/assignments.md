# Onboarding tables: assignments in YAML

Governance owns the **metric catalog** (what each metric means). **Assignments** say which metrics apply to which
tables and columns. They live in YAML files shipped in the wheel and change by pull request:

```
 config/assignments.yml (PR) ─> validate ─┬─> assignments  ─> runner ─> results ─> dashboard + alerts
                                          └─> assignment_issues  (what to fix)
```

`dq-sync-assignments` (run by `dq_deploy_config` and at the start of `dq_daily_run`) loads the files named in the
`assignment_files` bundle variable, validates every row and overwrites the `assignments` table. The dev and test
targets also load `dev/synthetic_assignments.yml`.

## Onboard a table

Add a block to `src/dq_framework/config/assignments.yml`:

```yaml
defaults:
  owner: team@example.com        # fallback order for owner/severity: check > table > defaults
  severity: MEDIUM
tables:
  - name: ${catalog}.sales.orders      # ${catalog} is replaced per environment
    data_product: sales_orders
    checks:
      - {column: order_id, metric: duplicate_pct, threshold: 0.0, severity: CRITICAL}
      - {column: email, metric: null_pct, threshold: 0.02}
      - {column: country, metric: regex_fail_pct, threshold: 0.01, params: {regex: '^[A-Z]{2}$'}}
      - {column: loaded_at, metric: hours_since_load, threshold: 24}
      - {metric: row_count, threshold: 1000}            # table-level metrics take no column
      - {column: phone, metric: null_pct, threshold: 0.5, enabled: false}   # keep the row, skip the check
```

Then run `.venv/bin/dq-validate-config`, open the PR, and deploy. Check `v_onboarding_status` for your table and
`assignment_issues` for anything that was dropped.

| Key | Where | Notes |
|---|---|---|
| `name` | table | `catalog.schema.table` |
| `owner`, `severity`, `data_product` | defaults / table (`owner`, `severity` also per check) | routing and grouping |
| `metric`, `threshold` | check | required; `metric` must exist and be active in `metric_catalog.yml` |
| `column` | check | required for column metrics, omitted for table metrics |
| `params` | check | metric parameters (e.g. `regex`) and the optional row `filter` |
| `enabled` | check | `false` keeps the row but skips the check |

## What gets rejected

Unknown keys or malformed YAML fail the sync job. A row that is well-formed but invalid (unknown metric, missing
parameter, bad severity, table or column not found, duplicate table/column/metric) is dropped, reported in
`assignment_issues` (alert 7e) and never fails the job.

## What governance sees

| Question | Where to look |
|---|---|
| Did my change take effect, and how many checks do I have? | `v_onboarding_status` |
| Why was a row ignored? | `assignment_issues` (alert 7e notifies governance) |
| Is the table passing? | dashboard on `v_latest_results` / `v_daily_pass_rate` |
