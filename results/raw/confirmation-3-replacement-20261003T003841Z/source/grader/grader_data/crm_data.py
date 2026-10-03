"""Deterministic CRM fixture: the source rows and the records the grader expects.

The clean-v1 generator; for a
given (record_count, id_start, seed) it reproduces the evidence runs' input.csv
byte for byte. At image build it writes the agent's starting workspace:

    python crm_data.py <variant> <workdir>   -> <workdir>/input.csv, <workdir>/output.json
"""

from __future__ import annotations

import csv
import io
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

FIELDS = ("id", "account", "owner", "stage", "amount_usd", "created_date", "close_date")
EXPORT_DATE = date(2026, 9, 30)
STAGES = ("New", "Qualified", "Proposal", "Negotiation", "Closed Won", "Closed Lost")
OWNERS = ("Maya Chen", "Jordan Ellis", "Priya Shah", "Lucas Reed", "Elena Cruz", "Marcus Bell", "Nora Patel", "Owen Brooks")
BRANDS = ("Alder", "Aspen", "Birch", "Cedar", "Copper", "Granite", "Harbor", "Juniper", "Maple", "Orchard", "Pine", "Silver", "Summit", "Willow", "Canyon", "Prairie", "Redwood", "Laurel", "Mesa", "Coral", "Sequoia", "Cypress", "Sierra", "Sterling")
PLACES = ("Ridge", "Grove", "Point", "Valley", "Creek", "Springs", "Hill", "Lake", "Park", "Heights", "Crest", "Bend")
# Industry-specific deal ranges produce varied but plausible opportunity amounts.
INDUSTRIES = (
    ("Logistics", 8000, 180000), ("Medical", 12000, 260000),
    ("Manufacturing", 20000, 400000), ("Consulting", 6000, 90000),
    ("Foods", 4000, 120000), ("Software", 8000, 180000),
    ("Construction", 10000, 250000), ("Retail", 4000, 110000),
    ("Energy", 20000, 450000), ("Packaging", 8000, 160000),
    ("Dental", 3000, 80000), ("Hospitality", 8000, 150000),
    ("Agriculture", 6000, 160000), ("Telecom", 10000, 280000),
    ("Engineering", 15000, 250000), ("Distribution", 8000, 200000),
)
AGE_BOUNDS = {"New": (0, 30), "Qualified": (5, 120), "Proposal": (7, 180), "Negotiation": (10, 240)}
CLOSE_BOUNDS = {"New": (30, 180), "Qualified": (21, 120), "Proposal": (14, 90), "Negotiation": (3, 45)}


def make_records(record_count: int, seed: int, id_start: int) -> list[dict]:
    """Generate fictional CRM opportunities; no customer dataset is imported."""
    if type(record_count) is not int or record_count < 1:
        raise ValueError("record_count must be a positive integer")
    if type(id_start) is not int or not 1 <= id_start <= 99999 or id_start + record_count - 1 > 99999:
        raise ValueError("every record ID must fit D followed by five digits")
    rng = random.Random(seed)
    records = []
    for index in range(id_start, id_start + record_count):
        industry, minimum, maximum = rng.choice(INDUSTRIES)
        account = f"{rng.choice(BRANDS)} {rng.choice(PLACES)} {industry}"
        # Stable ownership across repeated accounts resembles a CRM export.
        owner = OWNERS[sum(account.encode("utf-8")) % len(OWNERS)]
        stage = rng.choices(STAGES, weights=(14, 20, 22, 16, 18, 10), k=1)[0]
        amount_cents = round(rng.triangular(minimum * 100, maximum * 100, (minimum + (maximum - minimum) * 0.15) * 100))
        if stage in ("Closed Won", "Closed Lost"):
            age_days = rng.randint(14, 730)
            created = EXPORT_DATE - timedelta(days=age_days)
            closed = created + timedelta(days=rng.randint(7, min(age_days, 240)))
        else:
            created = EXPORT_DATE - timedelta(days=rng.randint(*AGE_BOUNDS[stage]))
            closed = EXPORT_DATE + timedelta(days=rng.randint(*CLOSE_BOUNDS[stage]))
        records.append({
            "id": f"D{index:05d}", "account": account, "owner": owner, "stage": stage,
            "amount_usd": amount_cents / 100,
            "created_date": created.isoformat(), "close_date": closed.isoformat(),
        })
    return records


def csv_text(records: list[dict]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows({**record, "amount_usd": f"{record['amount_usd']:.2f}"} for record in records)
    return stream.getvalue()


def variant_records(variants_path: Path, variant: str) -> list[dict]:
    data = json.loads(variants_path.read_text())["variants"][variant]["data"]
    return make_records(data["record_count"], data["seed"], data["id_start"])


def main() -> int:
    variant, workdir = sys.argv[1], Path(sys.argv[2])
    records = variant_records(Path(__file__).resolve().parents[2] / "variants.json", variant)
    (workdir / "input.csv").write_text(csv_text(records), encoding="utf-8", newline="")
    (workdir / "output.json").write_text("[]\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
