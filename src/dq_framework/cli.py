"""Entry points, one per Lakeflow Job task (see [project.scripts] in pyproject.toml).

Each parses sys.argv, so the bundle passes job ``parameters`` as command-line arguments.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from . import assignments_sync, config_io, engine, sources, spark_io, sqlfiles, synthetic, tag_sync
from .naming import TableNames

_EMPTY = {"", "none", "-"}


def _split(value: Optional[str]) -> list[str]:
    """Comma-separated job parameter -> list. 'none' means empty (job parameters cannot be blank)."""
    if value is None or value.strip().lower() in _EMPTY:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse(
    description: str,
    argv: Optional[Sequence[str]],
    extra: Optional[Callable[[argparse.ArgumentParser], None]] = None,
    needs_catalog: bool = True,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    if needs_catalog:
        parser.add_argument("--catalog", required=True, help="Unity Catalog catalog for the DQ schema")
        parser.add_argument("--schema", default="dq", help="schema holding the framework tables")
    if extra:
        extra(parser)
    return parser.parse_args(argv)


def _spark():
    from pyspark.sql import SparkSession  # imported lazily so pure modules need no pyspark

    return SparkSession.builder.getOrCreate()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- deploy-time tasks


def setup_main(argv: Optional[Sequence[str]] = None) -> None:
    """Create or update the framework tables and views (idempotent)."""
    a = _parse("Create or update DQ tables and views", argv)
    spark = _spark()
    for name in ("ddl.sql", "views.sql"):
        for statement in sqlfiles.load_sql(name, a.catalog, a.schema):
            spark.sql(statement)
    print(f"setup complete in {a.catalog}.{a.schema}")


def load_metrics_main(argv: Optional[Sequence[str]] = None) -> None:
    """Load the metric catalog YAML (shipped in the wheel) into the metric_catalog table."""
    a = _parse(
        "Load metric_catalog.yml into the metric_catalog table", argv,
        lambda p: p.add_argument("--metrics-file", default="metric_catalog.yml"),
    )  # fmt: skip
    spark = _spark()
    metrics = config_io.load_metrics(a.metrics_file)
    spark_io.write_metrics(spark, TableNames(a.catalog, a.schema).metric_catalog, metrics,
                           spark_io.current_user(spark), _now())  # fmt: skip
    print(f"loaded {len(metrics)} metrics from {a.metrics_file}")


# --------------------------------------------------------------------------- assignments


def sync_assignments_main(argv: Optional[Sequence[str]] = None) -> None:
    """Rebuild the assignments table from every source (yaml, app table, csv volume, tags)."""

    def extra(p: argparse.ArgumentParser) -> None:
        p.add_argument("--assignment-files", default="assignments.yml",
                       help="comma-separated YAML files shipped in the wheel (or absolute paths)")  # fmt: skip
        p.add_argument("--source-tables", default="none",
                       help="comma-separated catalog.schema.table feeds (e.g. a governance app)")
        p.add_argument("--csv-paths", default="none", help="comma-separated /Volumes/... CSV files")
        p.add_argument("--tag-catalogs", default="none",
                       help="catalogs scanned for dq_ tags (default: --catalog)")
        p.add_argument("--tag-rules", default="tag_rules.yml")
        p.add_argument("--no-tags", action="store_true", help="skip the tag source")
        p.add_argument("--skip-column-check", action="store_true",
                       help="do not verify tables/columns exist in information_schema")
        p.add_argument("--allow-shrink", action="store_true",
                       help="allow enabled assignments to drop by more than half")

    a = _parse("Rebuild the assignments table from all sources", argv, extra)
    names = TableNames(a.catalog, a.schema)
    spark = _spark()
    run_id, now = str(uuid.uuid4()), _now()
    metrics = {m.metric_id: m for m in spark_io.read_metrics(spark, names.metric_catalog)}

    candidates: list = []
    issues: list = []
    for ref in _split(a.assignment_files):
        candidates += config_io.load_assignments(ref, a.catalog, "yaml")  # bad YAML fails the job
    for table in _split(a.source_tables):
        found, bad = sources.assignments_from_rows(spark_io.read_rows(spark, table), "app")
        candidates += found
        issues += bad
    for path in _split(a.csv_paths):
        found, bad = sources.assignments_from_rows(spark_io.read_csv_rows(spark, path), "csv")
        candidates += found
        issues += bad
    if not a.no_tags:
        rules = tag_sync.load_tag_rules(a.tag_rules)
        rule_errors = tag_sync.validate_tag_rules(rules, metrics)
        if rule_errors:
            raise config_io.ConfigError(rule_errors)
        table_tags, column_tags = spark_io.read_tags(
            spark, _split(a.tag_catalogs) or [a.catalog], rules.prefix
        )
        derived, tag_issues = tag_sync.derive_tag_assignments(rules, table_tags, column_tags)
        candidates += derived
        issues += tag_issues

    existing = None
    if not a.skip_column_check:
        existing = spark_io.read_existing_columns(spark, {c.table_name for c in candidates})
    final, problems = assignments_sync.consolidate(candidates, metrics, existing)
    issues += problems

    enabled = sum(1 for x in final if x.is_enabled)
    if not a.allow_shrink:
        assignments_sync.check_shrink(spark_io.count_enabled(spark, names.assignments), enabled)
    user = spark_io.current_user(spark)
    spark_io.write_assignments(spark, names.assignments, final, user, now)
    spark_io.write_issues(spark, names.assignment_issues, issues, run_id, now)
    by_source: dict[str, int] = {}
    for x in final:
        by_source[x.source] = by_source.get(x.source, 0) + 1
    print(f"assignments: {len(final)} ({enabled} enabled) by source {by_source}; "
          f"issues: {len(issues)} ({sum(i.severity == 'ERROR' for i in issues)} errors)")  # fmt: skip


# --------------------------------------------------------------------------- the run


def run_main(argv: Optional[Sequence[str]] = None) -> None:
    """Execute every enabled assignment and append results. Failing checks do not fail the job."""
    a = _parse(
        "Run all enabled DQ checks", argv,
        lambda p: p.add_argument("--max-workers", type=int, default=4),
    )  # fmt: skip
    names = TableNames(a.catalog, a.schema)
    spark = _spark()
    metrics = spark_io.read_metrics(spark, names.metric_catalog)
    assignments = spark_io.read_assignments(spark, names.assignments)
    if not assignments:
        print("WARNING: no enabled assignments; nothing to run")
    results = engine.run_checks(
        assignments, metrics, lambda sql: spark.sql(sql).first().asDict(),
        run_id=str(uuid.uuid4()), run_ts=_now(), max_workers=a.max_workers,
    )  # fmt: skip
    spark_io.append_results(spark, names.results, results)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"ran {len(results)} checks: {counts}")


# --------------------------------------------------------------------------- CI (no Spark)


def validate_config_main(argv: Optional[Sequence[str]] = None) -> None:
    """Static validation of the shipped config. Needs only pyyaml, so it runs on a CI agent."""
    a = _parse(
        "Validate metric catalog, tag rules and assignment files", argv,
        lambda p: (
            p.add_argument("--metrics-file", default="metric_catalog.yml"),
            p.add_argument("--tag-rules", default="tag_rules.yml"),
            p.add_argument("--assignment-files", default="assignments.yml,dev/synthetic_assignments.yml"),
        ),
        needs_catalog=False,
    )  # fmt: skip
    errors: list[str] = []
    metrics = {}
    try:
        metrics = {m.metric_id: m for m in config_io.load_metrics(a.metrics_file)}
    except config_io.ConfigError as exc:
        errors += [f"{a.metrics_file}: {e}" for e in exc.errors]
    try:
        rules = tag_sync.load_tag_rules(a.tag_rules)
        errors += [f"{a.tag_rules}: {e}" for e in tag_sync.validate_tag_rules(rules, metrics)]
    except config_io.ConfigError as exc:
        errors += [f"{a.tag_rules}: {e}" for e in exc.errors]
    candidates = []
    for ref in _split(a.assignment_files):
        try:
            candidates += config_io.load_assignments(ref, "validation_catalog")
        except config_io.ConfigError as exc:
            errors += exc.errors
    _, issues = assignments_sync.consolidate(candidates, metrics)
    errors += [f"{i.table_name}: {i.message}" for i in issues]
    if errors:
        raise SystemExit("config validation failed:\n  " + "\n  ".join(errors))
    print(f"config OK: {len(metrics)} metrics, {len(candidates)} explicit assignments")


# --------------------------------------------------------------------------- dev/test only


def seed_synthetic_main(argv: Optional[Sequence[str]] = None) -> None:
    """Create synthetic tables with known defects and tag them. Refuses to run outside dev/test."""
    a = _parse(
        "Seed synthetic data (dev/test only)", argv,
        lambda p: p.add_argument("--environment", required=True),
    )  # fmt: skip
    if a.environment not in ("dev", "test"):
        raise SystemExit(f"refusing to seed synthetic data in environment '{a.environment}'")
    synthetic.seed_synthetic(_spark(), a.catalog, _now())
    print(f"seeded {a.catalog}.{synthetic.SYNTHETIC_SCHEMA}")


def verify_synthetic_main(argv: Optional[Sequence[str]] = None) -> None:
    """Assert the latest run produced exactly the expected outcomes for the synthetic tables."""
    a = _parse("Verify synthetic outcomes (dev/test only)", argv,
               lambda p: p.add_argument("--environment", required=True))  # fmt: skip
    if a.environment not in ("dev", "test"):
        raise SystemExit(f"refusing to verify synthetic data in environment '{a.environment}'")
    names = TableNames(a.catalog, a.schema)
    spark = _spark()
    latest = f"`{a.catalog}`.`{a.schema}`.`v_latest_results`"
    results = [r.asDict() for r in spark.sql(f"SELECT assignment_id, status FROM {latest}").collect()]
    assigns = [r.asDict() for r in spark.sql(
        f"SELECT assignment_id, source FROM {names.assignments}").collect()]  # fmt: skip
    issues = [r.asDict() for r in spark.sql(
        f"SELECT severity, message FROM {names.assignment_issues}").collect()]  # fmt: skip
    problems = synthetic.verify(a.catalog, results, assigns, issues)
    if problems:
        raise SystemExit("synthetic verification failed:\n  " + "\n  ".join(problems))
    print("synthetic verification passed")
