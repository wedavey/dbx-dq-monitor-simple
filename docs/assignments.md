# Onboarding tables: no-code and low-code assignment options

Governance owns the **metric catalog** (what each metric means). Assignments say **which metrics apply to which
tables and columns**, and are the part that must be easy for non-engineers. The framework accepts assignments from
several independent *sources* and merges them into one validated `assignments` table:

```
 tags in Unity Catalog ─┐
 governance app table  ─┤                    ┌─> assignments  ─> runner ─> results ─> dashboard + alerts
 CSV/Excel in a volume ─┼─> validate + merge ┤
 assignments.yml (PR)  ─┘   (priority)       └─> assignment_issues  (what to fix, shown to the source's owner)
```

Every source produces the same row shape, so adding or retiring a source never touches the runner.
**Priority when two sources describe the same check: yaml > app > csv > tag.** The winner may be *disabled*, which is
how a YAML row switches off a tag-derived check.

## 1. Tag-driven (recommended primary path)

Governance already tags metadata in Catalog Explorer; this reuses that habit. People tag **business intent**; the
engineer-owned `tag_rules.yml` maps tags to metrics, so nobody writes SQL or remembers metric names.

### Onboard a table in three steps (Catalog Explorer, or `ALTER ... SET TAGS`)

1. **Opt the table in:** `dq_monitor = true`. Optionally `dq_owner = team@example.com`, `dq_data_product = crm_customer`,
   `dq_severity = HIGH` (applies to every check on the table).
2. **Tag the columns** that matter (below). A tag with no value means "on".
3. **Wait for the next sync** (the daily job syncs before it runs). Check `v_onboarding_status` for your table, and
   the `assignment_issues` table for anything that was ignored.

### Tag reference (shipped defaults, editable in `tag_rules.yml`)

| Tag | Where | Value | Becomes |
|---|---|---|---|
| `dq_monitor` | table | `true` / `false` | opt in / explicit opt out |
| `dq_owner`, `dq_data_product`, `dq_severity` | table | text / text / LOW..CRITICAL | routing and grouping |
| `dq_required` | column | none or `true` | `null_pct <= 0`, HIGH |
| `dq_max_null_pct` | column | `0.02` | `null_pct <= 0.02`, HIGH |
| `dq_not_blank` | column | none or `true` | `blank_pct <= 0` |
| `dq_unique` | column | none or `true` | `duplicate_pct <= 0`, CRITICAL |
| `dq_format` | column | `email`, `iso_country_alpha2`, `iso_currency`, `digits_only`, `au_postcode`, `uuid` | `regex_fail_pct <= 0.01` using that pattern |
| `dq_freshness_hours` | column (a load timestamp) | `24` | `hours_since_load <= 24`, HIGH |
| `dq_min_rows` | table | `1000` | `row_count >= 1000`, HIGH |

Severity precedence: table `dq_severity` > the rule's severity > the default (MEDIUM). If two tags give the same check
(for example `dq_required` and `dq_max_null_pct`), the explicit-threshold tag wins and a warning is raised.

### Why this works well
- **Same place as the rest of governance's metadata work.** Tags are visible and searchable next to the column.
- **Typos are caught, not silent.** `dq_requird`, a column tag set on a table, an unknown pattern name, a non-numeric
  threshold, or a tagged table that was never opted in all produce rows in `assignment_issues` (alert 7e).
- **Guard rails.** Tag values never reach SQL except through named patterns, so tags cannot inject SQL. Tags cannot set a row filter.
- **Environment-neutral.** Tags describe intent, so the same tagging works in dev, test and prod.

### Trade-offs to be aware of
- **Latency:** a tag takes effect on the next sync (daily by default, or run `dq_deploy_config` on demand).
- **Expressiveness:** tags carry a name and a short value. Anything richer (custom regex, row filters, cross-column
  rules) goes through `assignments.yml` or a new metric/pattern.
- **Bulk onboarding is clicky in the UI.** Use SQL (`ALTER TABLE ... ALTER COLUMN ... SET TAGS`) or a notebook to tag
  many columns at once.
- **Access:** `information_schema` only returns tags on objects the run identity can see, so the service principal needs
  BROWSE/SELECT on every catalog in `tag_catalogs`. A revoked grant silently returns fewer tags, so the sync refuses to
  overwrite if enabled assignments would drop by more than half (`--allow-shrink` overrides).
- **Who may tag:** check whether Unity Catalog *governed tags* are available in your account. They can restrict who may
  apply `dq_*` tags and constrain allowed values, which gives governance control over the vocabulary.
- **Tag naming:** keep keys to letters, digits and underscores. Unity Catalog restricts some punctuation in tag keys, so
  confirm against the current docs before extending the vocabulary.

## 2. CSV / Excel in a Unity Catalog volume (spreadsheet-friendly)

Governance maintains a spreadsheet, exports CSV to `/Volumes/<catalog>/dq/uploads/assignments.csv`, and the sync reads it
(`--csv-paths`, bundle variable `csv_paths`). Columns:
`table_name, column_name, metric_id, threshold, severity, owner, params, data_product, is_enabled`
(`params` is JSON such as `{"regex": "^[A-Z]{3}$"}`; blank `column_name` for table-level metrics).

- **Pros:** zero new tooling, familiar, bulk-friendly, good for the initial onboarding of dozens of tables.
- **Cons:** manual upload step, easy to hold stale copies, and the spreadsheet needs a data-validation dropdown for
  `metric_id` and `severity` to stop typos. Row problems come back as `assignment_issues`, not as a failed job.

## 3. Governance app over an assignments table (best UX, most effort)

A small Databricks App (form and grid) writes validated rows to `<catalog>.dq.assignments_app`; list it in
`source_tables` and it is merged as the `app` source with no code change. Same row shape as the CSV.

- **Pros:** dropdowns populated from `metric_catalog`, instant validation, audit trail of who changed what, can show
  `v_onboarding_status` and latest results next to the form.
- **Cons:** an application to build, secure and maintain. Not recommended inside a six-week PoC; the table-based design
  means you can add it later without rework.

## 4. YAML via pull request (engineers; exceptions and overrides)

`assignments.yml` is the highest-priority source. Use it for anything tags cannot express (custom regex, row filters to
scope big tables to recent partitions) and to override or disable a tag-derived check. Governance would need an engineer
to author these, so keep it for exceptions rather than the main onboarding route.

## 5. Ideas for later: derive assignments from existing metadata

Zero-touch onboarding: a new source could emit assignments from `PRIMARY KEY` / `NOT NULL` constraints (uniqueness and
completeness for free) or from a schema-level tag such as `dq_profile = standard` that applies a default metric set to
every table in the schema. Each would be a new function that returns assignments, merged at a low priority like `tag`.

## Recommended rollout for the PoC

| Phase | Add | Why |
|---|---|---|
| Weeks 1 to 2 | YAML only, synthetic data in dev | Prove the engine, results and alerts end to end |
| Weeks 3 to 4 | Tag source on the two PoC data products | Governance onboards the simple and the complex product themselves |
| Week 5 | CSV import for bulk onboarding, if tagging many columns proves slow | Cheap and familiar |
| After PoC | App over `assignments_app`, if governance wants it | Best UX, justified by real usage |

## What governance sees (feedback loop)

| Question | Where to look |
|---|---|
| Did my tags take effect, and how many checks do I have? | `v_onboarding_status` |
| Why was a tag ignored? | `assignment_issues` (alert 7e notifies governance) |
| Is the table passing? | dashboard on `v_latest_results` / `v_daily_pass_rate` |
