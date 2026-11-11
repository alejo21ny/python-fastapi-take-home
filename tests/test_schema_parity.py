"""Static check that the SQL schema mirrors app.domain. No database required.

Reads db/init/002_coffee_health.sql as text, so a limit or category changed on one
side only fails fast here, before any integration test runs.
"""

import re
from dataclasses import fields
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from app.domain import (
    LIMITS,
    CoffeeHealthRecord,
    Gender,
    HealthIssues,
    Occupation,
    SleepQuality,
    StressLevel,
)

SCHEMA = (Path(__file__).parent.parent / "db/init/002_coffee_health.sql").read_text()

CATEGORIES: dict[str, type[StrEnum]] = {
    "gender": Gender,
    "sleep_quality": SleepQuality,
    "stress_level": StressLevel,
    "health_issues": HealthIssues,
    "occupation": Occupation,
}


def test_every_record_field_is_a_column() -> None:
    columns = re.findall(r"^\s+(\w+) (?:integer|smallint|text|numeric|boolean)\b", SCHEMA, re.M)
    assert columns == [f.name for f in fields(CoffeeHealthRecord)]


def test_range_checks_match_domain_limits() -> None:
    checks = re.findall(r"check \((\w+) between (\S+) and (\S+)\)", SCHEMA)
    in_sql = {column: (Decimal(low), Decimal(high)) for column, low, high in checks}
    in_python = {column: (r.min, r.max) for column, r in LIMITS.items()}
    assert in_sql == in_python


def test_category_checks_match_domain_enums() -> None:
    checks = re.findall(r"check \((\w+) in \(([^)]*)\)\)", SCHEMA)
    in_sql = {column: set(re.findall(r"'([^']*)'", values)) for column, values in checks}
    in_python = {column: {m.value for m in enum} for column, enum in CATEGORIES.items()}
    assert in_sql == in_python
