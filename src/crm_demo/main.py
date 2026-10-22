"""Entry points for the bundle's python_wheel_tasks (parameters are --key=value)."""
import argparse

from pyspark.sql import SparkSession

from crm_demo.cleanse import cleanse_contacts
from crm_demo.generate import generate_contacts


def _args(extra=()):
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    for name, typ, default in extra:
        p.add_argument(f"--{name}", type=typ, default=default)
    return p.parse_args()


def generate():
    a = _args([("num_rows", int, 1000)])
    spark = SparkSession.builder.getOrCreate()
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{a.catalog}`.`{a.schema}`")
    (generate_contacts(spark, a.num_rows).write.mode("overwrite")
     .saveAsTable(f"`{a.catalog}`.`{a.schema}`.contacts_raw"))


def cleanse():
    a = _args()
    spark = SparkSession.builder.getOrCreate()
    raw = spark.table(f"`{a.catalog}`.`{a.schema}`.contacts_raw")
    (cleanse_contacts(raw).write.mode("overwrite")
     .saveAsTable(f"`{a.catalog}`.`{a.schema}`.contacts_clean"))
