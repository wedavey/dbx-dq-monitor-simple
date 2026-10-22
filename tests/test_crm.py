from datetime import date

from crm_demo.cleanse import cleanse_contacts
from crm_demo.generate import COLUMNS, generate_contacts, make_rows


def test_generate_is_deterministic_and_shaped(spark):
    df = generate_contacts(spark, 50, seed=1)
    assert df.columns == COLUMNS
    assert df.count() == 50
    assert make_rows(50, seed=1) == make_rows(50, seed=1)


def _clean(spark, rows):
    # explicit schema: all-None columns can't be type-inferred
    schema = "contact_id long, first_name string, last_name string, email string, phone string, created_at string"
    return cleanse_contacts(spark.createDataFrame(rows, schema)).collect()


def test_names_and_email_normalised(spark):
    [r] = _clean(spark, [(1, "  ada ", "LOVELACE", "  Ada@Example.COM ", "5551234567", "2025-01-02")])
    assert (r.first_name, r.last_name, r.email) == ("Ada", "Lovelace", "ada@example.com")
    assert r.created_at == date(2025, 1, 2)


def test_invalid_email_and_phone_become_null(spark):
    [r] = _clean(spark, [(1, "a", "b", "not-an-email", "123", "2025-01-02")])
    assert r.email is None and r.phone is None


def test_phone_formats_normalised(spark):
    rows = [(1, "a", "b", None, "(555) 123-4567", "2025-01-01"),
            (2, "a", "b", None, "+1 5551234567", "2025-01-01")]
    assert {r.phone for r in _clean(spark, rows)} == {"5551234567"}


def test_dedup_keeps_latest(spark):
    rows = [(1, "old", "x", None, None, "2025-01-01"), (1, "new", "x", None, None, "2025-06-01")]
    [r] = _clean(spark, rows)
    assert r.first_name == "New"


def test_cleansing_generated_data_has_unique_ids(spark):
    out = cleanse_contacts(generate_contacts(spark, 200))
    assert out.count() == out.select("contact_id").distinct().count()
