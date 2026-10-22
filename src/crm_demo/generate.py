"""Generate deliberately messy fake CRM contacts (no external deps, deterministic by seed)."""
import random

from pyspark.sql import DataFrame, SparkSession

FIRST = ["Ada", "grace", "LINUS", "Margaret", "alan", "Edsger", "Barbara", "ken"]
LAST = ["Lovelace", "hopper", "TORVALDS", "Hamilton", "Turing", "dijkstra", "Liskov", "Thompson"]
DOMAINS = ["example.com", "Example.COM", "test.org", "mail.net"]
COLUMNS = ["contact_id", "first_name", "last_name", "email", "phone", "created_at"]


def _mess(rng: random.Random, value: str) -> str:
    """Randomly add whitespace noise."""
    return rng.choice(["", " ", "  "]) + value + rng.choice(["", " "])


def make_rows(num_rows: int, seed: int = 42) -> list[tuple]:
    rng = random.Random(seed)
    rows = []
    for i in range(num_rows):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first}.{last}{rng.randint(1, 50)}@{rng.choice(DOMAINS)}"
        if rng.random() < 0.05:
            email = None
        elif rng.random() < 0.05:
            email = email.replace("@", "")  # invalid
        phone = "".join(rng.choice("0123456789") for _ in range(10))
        phone = rng.choice([phone, f"({phone[:3]}) {phone[3:6]}-{phone[6:]}", f"+1 {phone}"])
        if rng.random() < 0.05:
            phone = None
        # ~10% duplicate ids to exercise dedup
        contact_id = rng.randint(0, max(num_rows // 10, 1)) if rng.random() < 0.1 else i
        rows.append((contact_id, _mess(rng, first), _mess(rng, last),
                     None if email is None else _mess(rng, email), phone,
                     f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"))
    return rows


def generate_contacts(spark: SparkSession, num_rows: int, seed: int = 42) -> DataFrame:
    return spark.createDataFrame(make_rows(num_rows, seed), COLUMNS)
