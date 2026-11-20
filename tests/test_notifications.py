import smtplib
from datetime import datetime, timezone

import pytest

from dq_framework import cli, mailer, notifications
from dq_framework.notifications import Failure

RUN_TS = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)


def fail(owner="a@x.com", severity="HIGH", table="c.s.t", column="email", metric="null_pct", value=0.5):
    return Failure(table, column, metric, value, 0.02, "MAX", severity, owner)


def test_is_deliverable():
    assert notifications.is_deliverable("steward@example.com")
    assert not notifications.is_deliverable("data-stewards")
    assert not notifications.is_deliverable("a@x.com, b@x.com")
    assert not notifications.is_deliverable(None) and not notifications.is_deliverable("")


def test_group_by_owner_is_case_insensitive_and_sorted_critical_first():
    grouped = notifications.group_by_owner([
        fail("A@x.com", "HIGH", table="c.s.b"), fail("a@x.com", "CRITICAL", table="c.s.z"),
        fail("a@x.com", "HIGH", table="c.s.a"), fail("b@x.com"),
    ])  # fmt: skip
    assert set(grouped) == {"a@x.com", "b@x.com"}
    assert [(f.severity, f.table_name) for f in grouped["a@x.com"]] == [
        ("CRITICAL", "c.s.z"), ("HIGH", "c.s.a"), ("HIGH", "c.s.b")]  # fmt: skip


def test_one_message_per_owner_and_undeliverable_reported():
    msgs, skipped = notifications.build_messages(
        [fail("a@x.com"), fail("a@x.com", metric="dup"), fail("b@x.com"), fail("team-x"), fail(None)],
        RUN_TS, "prod")  # fmt: skip
    assert [(m.owner, m.failures) for m in msgs] == [("a@x.com", 2), ("b@x.com", 1)]
    assert skipped == {"team-x": 1, "(blank)": 1}
    assert msgs[0].subject == "[DQ prod] 2 high severity checks failed - 2026-10-05"
    assert "[HIGH] c.s.t.email | null_pct = 0.5 (> limit 0.02)" in msgs[0].body
    assert "b@x.com" not in msgs[0].body


def test_no_failures_no_messages():
    assert notifications.build_messages([], RUN_TS, "dev") == ([], {})


def test_table_scope_failure_and_missing_value_render():
    f = Failure("c.s.t", None, "row_count", None, 10, "MIN", "HIGH", "a@x.com")
    msg = notifications.build_message("a@x.com", [f], RUN_TS, "dev")
    assert "c.s.t | row_count = n/a (< limit 10)" in msg.body
    assert "1 high severity check failed" in msg.subject


def test_send_smtp_collects_per_recipient_errors(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, mail):
            if mail["To"] == "bad@x.com":
                raise smtplib.SMTPRecipientsRefused({})
            sent.append(mail["To"])

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    msgs = [notifications.Message(o, "s", "b", 1) for o in ("a@x.com", "bad@x.com", "c@x.com")]
    errors = mailer.send_smtp(msgs, "h", 587, "dq@x.com", "u", "p")
    assert sent == [("login", "u"), "a@x.com", "c@x.com"]
    assert len(errors) == 1 and errors[0].startswith("bad@x.com")


@pytest.fixture
def results_table(spark):
    spark.sql("DROP TABLE IF EXISTS spark_catalog.default.dq_notify_results")
    rows = [  # old run, then latest run
        ("r1", datetime(2026, 10, 4, 6), "old|x", "c.s.t", "email", "null_pct", 0.9, 0.02, "MAX", "FAIL", "HIGH", "a@x.com", None, None),
        ("r2", datetime(2026, 10, 5, 6), "a", "c.s.t", "email", "null_pct", 0.5, 0.02, "MAX", "FAIL", "HIGH", "a@x.com", None, None),
        ("r2", datetime(2026, 10, 5, 6), "b", "c.s.t", "id", "dup_pct", 0.0, 0.0, "MAX", "PASS", "HIGH", "a@x.com", None, None),
        ("r2", datetime(2026, 10, 5, 6), "c", "c.s.t", "x", "null_pct", 0.5, 0.02, "MAX", "FAIL", "LOW", "a@x.com", None, None),
        ("r2", datetime(2026, 10, 5, 6), "d", "c.s.u", None, "row_count", 0.0, 10.0, "MIN", "FAIL", "CRITICAL", "b@x.com", None, None),
    ]  # fmt: skip
    ddl = ("run_id STRING, run_ts TIMESTAMP, assignment_id STRING, table_name STRING, column_name STRING, "
           "metric_id STRING, metric_value DOUBLE, threshold DOUBLE, direction STRING, status STRING, "
           "severity STRING, owner STRING, data_product STRING, error_message STRING")
    spark.createDataFrame(rows, ddl).write.saveAsTable("spark_catalog.default.dq_notify_results")
    yield "spark_catalog.default.dq_notify_results"
    spark.sql("DROP TABLE IF EXISTS spark_catalog.default.dq_notify_results")


@pytest.mark.spark
def test_read_latest_run_failures_only_latest_run_and_severity(spark, results_table):
    from dq_framework import spark_io

    run_ts, failures = spark_io.read_latest_run_failures(spark, results_table, ["HIGH", "CRITICAL"])
    assert run_ts == datetime(2026, 10, 5, 6)
    assert sorted((f.owner, f.metric_id) for f in failures) == [("a@x.com", "null_pct"), ("b@x.com", "row_count")]


@pytest.mark.spark
def test_read_latest_run_failures_empty_table(spark):
    from dq_framework import spark_io

    spark.sql("DROP TABLE IF EXISTS spark_catalog.default.dq_notify_empty")
    spark.sql("CREATE TABLE spark_catalog.default.dq_notify_empty (run_id STRING, run_ts TIMESTAMP) USING parquet")
    assert spark_io.read_latest_run_failures(spark, "spark_catalog.default.dq_notify_empty", ["HIGH"]) == (None, [])
    spark.sql("DROP TABLE spark_catalog.default.dq_notify_empty")


def test_notify_main_rejects_bad_severity():
    with pytest.raises(SystemExit, match="invalid --severities"):
        cli.notify_main(["--catalog", "c", "--environment", "dev", "--severities", "URGENT"])


def _patch(monkeypatch, failures):
    monkeypatch.setattr(cli, "_spark", lambda: object())
    monkeypatch.setattr(cli.spark_io, "read_latest_run_failures", lambda *a: (RUN_TS, failures))


def test_notify_main_dry_run_prints_and_does_not_send(monkeypatch, capsys):
    _patch(monkeypatch, [fail("a@x.com")])
    monkeypatch.setattr(cli.mailer, "send_smtp", lambda *a, **k: pytest.fail("must not send"))
    cli.notify_main(["--catalog", "c", "--environment", "dev"])
    out = capsys.readouterr().out
    assert "DRY RUN to a@x.com" in out and "null_pct" in out


def test_notify_main_sends_and_fails_on_delivery_error(monkeypatch):
    _patch(monkeypatch, [fail("a@x.com")])
    calls = []
    monkeypatch.setattr(cli.mailer, "send_smtp", lambda *a, **k: calls.append(a) or ["a@x.com: refused"])
    args = ["--catalog", "c", "--environment", "dev", "--smtp-host", "smtp.x.com", "--sender", "dq@x.com"]
    with pytest.raises(SystemExit, match="failed to send"):
        cli.notify_main(args)
    assert calls[0][1:4] == ("smtp.x.com", 587, "dq@x.com")


def test_notify_main_no_failures_is_quiet(monkeypatch):
    _patch(monkeypatch, [])
    monkeypatch.setattr(cli.mailer, "send_smtp", lambda *a, **k: pytest.fail("must not send"))
    cli.notify_main(["--catalog", "c", "--environment", "dev", "--smtp-host", "h", "--sender", "s@x.com"])
