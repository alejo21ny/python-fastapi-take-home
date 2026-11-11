"""Shared test data: a valid raw row, and boundary cases derived from LIMITS.

Unit and integration tests use the same cases, so the Python validator and the SQL
CHECK constraints are held to identical boundaries.
"""

from dataclasses import fields
from decimal import Decimal

from app.domain import LIMITS, CoffeeHealthRecord

# Line 2 of data/coffee_health_raw.csv: a clean record.
VALID_ROW: dict[str, str] = {
    "ID": "1",
    "Age": "40",
    "Gender": "Male",
    "Country": "Germany",
    "Coffee_Intake": "3.5",
    "Caffeine_mg": "328.1",
    "Sleep_Hours": "7.5",
    "Sleep_Quality": "Good",
    "BMI": "24.9",
    "Heart_Rate": "78",
    "Stress_Level": "Low",
    "Physical_Activity_Hours": "14.5",
    "Health_Issues": "None",
    "Occupation": "Other",
    "Smoking": "0",
    "Alcohol_Consumption": "0",
}

# CSV header for each range-checked column.
HEADER = {
    "age": "Age",
    "coffee_intake": "Coffee_Intake",
    "caffeine_mg": "Caffeine_mg",
    "sleep_hours": "Sleep_Hours",
    "bmi": "BMI",
    "heart_rate": "Heart_Rate",
    "physical_activity_hours": "Physical_Activity_Hours",
}
INTEGER_COLUMNS = frozenset(f.name for f in fields(CoffeeHealthRecord) if f.type is int)

# Countries outside the known list that both Python and SQL must accept as written.
WELL_FORMED_COUNTRIES = ["Côte d'Ivoire", "Côte d’Ivoire", "Guinea-Bissau", "St. Lucia", "Portugal"]


def row(**overrides: str) -> dict[str, str]:
    """VALID_ROW with some fields replaced, keyed by CSV header."""
    return VALID_ROW | overrides


def _step(column: str) -> Decimal:
    # One unit for integers; one extra decimal place for decimals, which proves the
    # bounds are exact (20.0001 is rejected, not rounded down to 20).
    return Decimal(1) if column in INTEGER_COLUMNS else Decimal("0.0001")


INSIDE: list[tuple[str, Decimal]] = [
    (column, bound) for column, limits in LIMITS.items() for bound in (limits.min, limits.max)
]
OUTSIDE: list[tuple[str, Decimal]] = [
    (column, bound)
    for column, limits in LIMITS.items()
    for bound in (limits.min - _step(column), limits.max + _step(column))
]
