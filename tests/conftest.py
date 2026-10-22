import os
import sys

import pytest
from pyspark.sql import SparkSession


# Make Spark workers use the same interpreter as the test driver.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)


@pytest.fixture(scope="session")
def spark():
    s = (SparkSession.builder.master("local[1]").appName("tests")
         .config("spark.sql.shuffle.partitions", "1").config("spark.ui.enabled", "false")
         .getOrCreate())
    yield s
    s.stop()
