"""Domain model for a clean coffee health record.

This module is the single source of truth for categories and plausibility limits.
db/init/002_coffee_health.sql mirrors them as CHECK constraints, and
tests/test_schema_parity.py fails if the two drift apart.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Gender(StrEnum):
    MALE = "Male"
    FEMALE = "Female"
    OTHER = "Other"


class SleepQuality(StrEnum):
    POOR = "Poor"
    FAIR = "Fair"
    GOOD = "Good"
    EXCELLENT = "Excellent"


class StressLevel(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class HealthIssues(StrEnum):
    # "None" is a real category here (no health issues), not a missing value.
    NONE = "None"
    MILD = "Mild"
    MODERATE = "Moderate"
    SEVERE = "Severe"


class Occupation(StrEnum):
    OFFICE = "Office"
    HEALTHCARE = "Healthcare"
    SERVICE = "Service"
    STUDENT = "Student"
    OTHER = "Other"


@dataclass(frozen=True, slots=True)
class Range:
    """Inclusive plausibility range."""

    min: Decimal
    max: Decimal

    def __contains__(self, value: Decimal | int) -> bool:
        return self.min <= value <= self.max


# Inclusive limits keyed by database column. Chosen to reject physically implausible
# values (negative ages, 25 h of sleep, a BMI of 0) while staying wide enough not to
# reject rare but real people. Rationale per field lives in NOTES.md.
LIMITS: dict[str, Range] = {
    "age": Range(Decimal(18), Decimal(110)),  # adults only, per the exercise
    "coffee_intake": Range(Decimal(0), Decimal(20)),  # cups/day
    "caffeine_mg": Range(Decimal(0), Decimal(2000)),  # mg/day
    "sleep_hours": Range(Decimal(0), Decimal(24)),  # hours/night
    "bmi": Range(Decimal(10), Decimal(80)),
    "heart_rate": Range(Decimal(25), Decimal(220)),  # resting bpm
    "physical_activity_hours": Range(Decimal(0), Decimal(168)),  # hours/week
}

# Upper bound of PostgreSQL `integer`, used for the primary key.
MAX_ID = 2_147_483_647
MAX_COUNTRY_LENGTH = 100


@dataclass(frozen=True, slots=True)
class CoffeeHealthRecord:
    """A validated, normalized record, ready to be written to `coffee_health`."""

    id: int
    age: int
    gender: Gender
    country: str
    coffee_intake: Decimal
    caffeine_mg: Decimal
    sleep_hours: Decimal
    sleep_quality: SleepQuality
    bmi: Decimal
    heart_rate: int
    stress_level: StressLevel
    physical_activity_hours: Decimal
    health_issues: HealthIssues
    occupation: Occupation
    smoking: bool
    alcohol_consumption: bool
