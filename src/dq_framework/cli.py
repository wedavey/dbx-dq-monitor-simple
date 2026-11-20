"""Entry points, one per Lakeflow Job task (see [project.scripts] in pyproject.toml).

Each parses sys.argv, so the bundle passes job ``parameters`` as command-line arguments.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from . import assignments_sync, config_io, engine, mailer, notifications, spark_io, sqlfiles, synthetic
from .models import SEVERITIES
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
    """Rebuild the assignments table from the YAML assignment files shipped in the wheel."""

    def extra(p: argparse.ArgumentParser) -> None:
        p.add_argument("--assignment-files", default="assignments.yml",
                       help="comma-separated YAML files shipped in the wheel (or absolute paths)")  # fmt: skip
        p.add_argument("--skip-column-check", action="store_true",
                       help="do not verify tables/columns exist in information_schema")

    a = _parse("Rebuild the assignments table from YAML", argv, extra)
    names = TableNames(a.catalog, a.schema)
    spark = _spark()
    run_id, now = str(uuid.uuid4()), _now()
    metrics = {m.metric_id: m for m in spark_io.read_metrics(spark, names.metric_catalog)}

    candidates: list = []
    for ref in _split(a.assignment_files):
        candidates += config_io.load_assignments(ref, a.catalog)  # bad YAML fails the job

    existing = None
    if not a.skip_column_check:
        existing = spark_io.read_existing_columns(spark, {c.table_name for c in candidates})
    final, issues = assignments_sync.validate_all(candidates, metrics, existing)

    enabled = sum(1 for x in final if x.is_enabled)
    user = spark_io.current_user(spark)
    spark_io.write_assignments(spark, names.assignments, final, user, now)
    spark_io.write_issues(spark, names.assignment_issues, issues, run_id, now)
    print(f"assignments: {len(final)} ({enabled} enabled); issues: {len(issues)}")


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


# --------------------------------------------------------------------------- notifications


def _secret(scope: str, key: str) -> str:
    from databricks.sdk.runtime import dbutils  # only available on Databricks compute

    return dbutils.secrets.get(scope, key)


def notify_main(argv: Optional[Sequence[str]] = None) -> None:
    """Email each owner a summary of their high severity failures from the latest run.

    Sends nothing (and fails nothing) when no checks failed. Without --smtp-host the messages are
    only printed to the job log (dry run). Delivery errors fail the job so ops_email hears of it.
    """

    def extra(p: argparse.ArgumentParser) -> None:
        p.add_argument("--environment", required=True, help="shown in the subject line")
        p.add_argument("--severities", default="HIGH,CRITICAL", help="comma-separated severities to notify")
        p.add_argument("--smtp-host", default="none", help="'none' = dry run, print only")
        p.add_argument("--smtp-port", type=int, default=587)
        p.add_argument("--sender", default="none", help="From address")
        p.add_argument("--smtp-secret-scope", default="none",
                       help="secret scope holding keys smtp-username and smtp-password; 'none' = no auth")  # fmt: skip

    a = _parse("Notify owners of failed high severity checks", argv, extra)
    severities = [s.upper() for s in _split(a.severities)]
    bad = [s for s in severities if s not in SEVERITIES]
    if bad or not severities:
        raise SystemExit(f"invalid --severities {a.severities!r}; choose from {', '.join(SEVERITIES)}")

    spark = _spark()
    run_ts, failures = spark_io.read_latest_run_failures(
        spark, TableNames(a.catalog, a.schema).results, severities)  # fmt: skip
    if run_ts is None:
        print("WARNING: no results yet; nothing to notify")
        return
    messages, skipped = notifications.build_messages(failures, run_ts, a.environment)
    for owner, count in skipped.items():
        print(f"WARNING: {count} failure(s) for owner {owner!r} not sent: owner is not an email address")
    print(f"{len(failures)} failure(s) in latest run -> {len(messages)} message(s)")

    host = a.smtp_host.strip()
    if not messages:
        return
    if host.lower() in _EMPTY or a.sender.strip().lower() in _EMPTY:
        for m in messages:
            print(f"--- DRY RUN to {m.owner}\nSubject: {m.subject}\n{m.body}\n")
        return

    user = password = None
    if a.smtp_secret_scope.strip().lower() not in _EMPTY:
        user = _secret(a.smtp_secret_scope, "smtp-username")
        password = _secret(a.smtp_secret_scope, "smtp-password")
    errors = mailer.send_smtp(messages, host, a.smtp_port, a.sender, user, password)
    if errors:
        raise SystemExit("failed to send notifications:\n  " + "\n  ".join(errors))
    print(f"sent {len(messages)} message(s)")


# --------------------------------------------------------------------------- CI (no Spark)


def validate_config_main(argv: Optional[Sequence[str]] = None) -> None:
    """Static validation of the shipped config. Needs only pyyaml, so it runs on a CI agent."""
    a = _parse(
        "Validate metric catalog and assignment files", argv,
        lambda p: (
            p.add_argument("--metrics-file", default="metric_catalog.yml"),
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
    candidates = []
    for ref in _split(a.assignment_files):
        try:
            candidates += config_io.load_assignments(ref, "validation_catalog")
        except config_io.ConfigError as exc:
            errors += exc.errors
    _, issues = assignments_sync.validate_all(candidates, metrics)
    errors += [f"{i.table_name}: {i.message}" for i in issues]
    if errors:
        raise SystemExit("config validation failed:\n  " + "\n  ".join(errors))
    print(f"config OK: {len(metrics)} metrics, {len(candidates)} assignments")


# --------------------------------------------------------------------------- dev/test only


def seed_synthetic_main(argv: Optional[Sequence[str]] = None) -> None:
    """Create synthetic tables with known defects. Refuses to run outside dev/test."""
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
    issues = [r.asDict() for r in spark.sql(
        f"SELECT severity, message FROM {names.assignment_issues}").collect()]  # fmt: skip
    problems = synthetic.verify(a.catalog, results, issues)
    if problems:
        raise SystemExit("synthetic verification failed:\n  " + "\n  ".join(problems))
    print("synthetic verification passed")
