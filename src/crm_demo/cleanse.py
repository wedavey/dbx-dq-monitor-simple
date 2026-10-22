"""Cleansing rules for raw CRM contacts."""
from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def cleanse_contacts(df: DataFrame) -> DataFrame:
    """Trim/normalise names, lowercase email, null invalid emails, keep digits-only
    phones (10 digits after stripping a leading US country code), cast created_at
    to date, and keep the latest row per contact_id."""
    digits = F.regexp_replace(F.col("phone"), r"\D", "")
    digits = F.when(F.length(digits) == 11, F.regexp_replace(digits, r"^1", "")).otherwise(digits)
    email = F.lower(F.trim(F.col("email")))

    out = (
        df.withColumn("first_name", F.initcap(F.trim("first_name")))
        .withColumn("last_name", F.initcap(F.trim("last_name")))
        .withColumn("email", F.when(email.rlike(EMAIL_RE), email))
        .withColumn("phone", F.when(F.length(digits) == 10, digits))
        .withColumn("created_at", F.to_date("created_at"))
    )
    w = Window.partitionBy("contact_id").orderBy(F.col("created_at").desc_nulls_last())
    return out.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")
