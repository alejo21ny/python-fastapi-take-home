"""Unit tests for app.validation. No database required.

Most raw values below are taken verbatim from data/coffee_health_raw.csv.
"""

from decimal import Decimal

import pytest

from app.domain import (
    MAX_ID,
    CoffeeHealthRecord,
    Gender,
    HealthIssues,
    Occupation,
    SleepQuality,
    StressLevel,
)
from app.validation import Accepted, Rejected, ValidationResult, validate_row
from tests.boundary_cases import HEADER, INSIDE, OUTSIDE, VALID_ROW, WELL_FORMED_COUNTRIES, row


def accepted(result: ValidationResult) -> Accepted:
    assert isinstance(result, Accepted), f"expected Accepted, got {result}"
    return result


def rejected(result: ValidationResult) -> Rejected:
    assert isinstance(result, Rejected), f"expected Rejected, got {result}"
    return result


def test_clean_row_is_accepted_without_fixes() -> None:
    result = accepted(validate_row(2, VALID_ROW))

    assert result.line_no == 2
    assert result.fixes == ()
    assert result.record == CoffeeHealthRecord(
        id=1,
        age=40,
        gender=Gender.MALE,
        country="Germany",
        coffee_intake=Decimal("3.5"),
        caffeine_mg=Decimal("328.1"),
        sleep_hours=Decimal("7.5"),
        sleep_quality=SleepQuality.GOOD,
        bmi=Decimal("24.9"),
        heart_rate=78,
        stress_level=StressLevel.LOW,
        physical_activity_hours=Decimal("14.5"),
        health_issues=HealthIssues.NONE,  # "None" is a category, not a missing value
        occupation=Occupation.OTHER,
        smoking=False,
        alcohol_consumption=False,
    )


@pytest.mark.parametrize(
    ("header", "raw", "field", "expected"),
    [
        # Letter case of categories.
        ("Gender", "MALE", "gender", "Male"),
        ("Gender", "male", "gender", "Male"),
        ("Sleep_Quality", "GOOD", "sleep_quality", "Good"),
        ("Sleep_Quality", "good", "sleep_quality", "Good"),
        ("Stress_Level", "medium", "stress_level", "Medium"),
        ("Stress_Level", "HIGH", "stress_level", "High"),
        # Whitespace and letter case of known countries; acronyms keep their case.
        ("Country", "\tUSA ", "country", "USA"),
        ("Country", "  South Korea  ", "country", "South Korea"),
        ("Country", "belgium", "country", "Belgium"),
        ("Country", "SWITZERLAND", "country", "Switzerland"),
        ("Country", "usa", "country", "USA"),
        # Boolean words.
        ("Smoking", "Y", "smoking", True),
        ("Smoking", "no", "smoking", False),
        ("Alcohol_Consumption", "yes", "alcohol_consumption", True),
        ("Alcohol_Consumption", "false", "alcohol_consumption", False),
        # Whitespace around numbers.
        ("Age", " 40 ", "age", 40),
    ],
)
def test_unambiguous_values_are_normalized_and_reported(
    header: str, raw: str, field: str, expected: object
) -> None:
    result = accepted(validate_row(7, row(**{header: raw})))

    assert getattr(result.record, field) == expected
    assert len(result.fixes) == 1
    assert result.fixes[0].startswith(f"{header}: {raw!r} -> ")


@pytest.mark.parametrize(
    ("header", "raw", "reason"),
    [
        ("Age", "-3", "outside the plausible range 18–110"),
        ("Age", "150", "outside the plausible range"),
        ("Age", "212", "outside the plausible range"),
        ("Age", "thirty", "not an integer"),
        ("Age", "4O", "not an integer"),  # letter O, not zero: never guessed
        ("Age", "unknown", "not an integer"),
        ("Age", "", "missing"),
        ("Age", "40.0", "not an integer"),
        ("Sleep_Hours", "-1", "outside the plausible range 0–24"),
        ("Sleep_Hours", "25", "outside the plausible range"),
        ("Sleep_Hours", "seven", "not a decimal number"),
        ("Sleep_Hours", "", "missing"),
        ("BMI", "0", "outside the plausible range 10–80"),
        ("BMI", "n/a", "not a decimal number"),
        ("BMI", "", "missing"),
        ("Heart_Rate", "0", "outside the plausible range 25–220"),
        ("Heart_Rate", "350", "outside the plausible range"),
        ("Heart_Rate", "", "missing"),
        ("Gender", "", "missing"),
        ("Gender", "?", "is not one of Male, Female, Other"),
        ("Gender", "N/A", "is not one of"),
        ("Smoking", "-1", "is not a boolean"),
        ("Smoking", "2", "is not a boolean"),
        ("Occupation", "Engineer", "is not one of"),
    ],
)
def test_invalid_values_are_rejected_with_a_reason(header: str, raw: str, reason: str) -> None:
    result = rejected(validate_row(9, row(**{header: raw})))

    assert result.line_no == 9
    assert len(result.reasons) == 1
    assert result.reasons[0].startswith(f"{header}: ")
    assert reason in result.reasons[0]


def test_every_invalid_field_is_reported() -> None:
    result = rejected(validate_row(5, row(Age="thirty", Gender="?", BMI="")))
    assert [reason.split(":")[0] for reason in result.reasons] == ["Age", "Gender", "BMI"]
    assert result.reason.count("; ") == 2


def test_absent_column_is_reported_as_missing() -> None:
    raw = {k: v for k, v in VALID_ROW.items() if k != "Country"}
    assert rejected(validate_row(3, raw)).reasons == ("Country: missing",)


# --- Plausibility boundaries (same cases as the SQL integration test) -------------


@pytest.mark.parametrize(("column", "value"), INSIDE)
def test_boundaries_are_inclusive(column: str, value: Decimal) -> None:
    result = accepted(validate_row(2, row(**{HEADER[column]: str(value)})))
    assert getattr(result.record, column) == value


@pytest.mark.parametrize(("column", "value"), OUTSIDE)
def test_values_just_outside_the_boundaries_are_rejected(column: str, value: Decimal) -> None:
    result = rejected(validate_row(2, row(**{HEADER[column]: str(value)})))
    assert "outside the plausible range" in result.reason


# --- Decimal policy -----------------------------------------------------------------


@pytest.mark.parametrize("raw", ["3.25", "3.50", "0.123456789", ".5"])
def test_decimals_keep_their_exact_precision(raw: str) -> None:
    result = accepted(validate_row(2, row(Coffee_Intake=raw)))
    assert result.record.coffee_intake == Decimal(raw)
    assert str(result.record.coffee_intake) == str(Decimal(raw))  # same digits, no rounding


@pytest.mark.parametrize("raw", ["1e1", "1_0", "NaN", "Infinity", "3,5", "٣", "--1"])
def test_non_plain_decimal_notation_is_rejected(raw: str) -> None:
    assert "not a decimal number" in rejected(validate_row(2, row(Coffee_Intake=raw))).reason


def test_negative_zero_is_normalized_to_zero() -> None:
    result = accepted(validate_row(2, row(Coffee_Intake="-0.0")))
    assert str(result.record.coffee_intake) == "0.0"
    assert result.fixes == ("Coffee_Intake: '-0.0' -> '0.0'",)


# --- IDs ----------------------------------------------------------------------------


def test_largest_postgres_integer_id_is_accepted() -> None:
    assert accepted(validate_row(2, row(ID=str(MAX_ID)))).record.id == MAX_ID


@pytest.mark.parametrize("raw", ["0", "-1", str(MAX_ID + 1), "1.0", "abc"])
def test_invalid_ids_are_rejected(raw: str) -> None:
    assert rejected(validate_row(2, row(ID=raw))).reason.startswith("ID: ")


@pytest.mark.parametrize("header", ["ID", "Age", "Heart_Rate"])
def test_integers_too_long_to_parse_are_rejected_not_raised(header: str) -> None:
    result = rejected(validate_row(3, row(**{header: "9" * 5000})))
    assert result.reasons == (f"{header}: integer has too many digits (5000)",)


# --- Countries ----------------------------------------------------------------------


@pytest.mark.parametrize("raw", WELL_FORMED_COUNTRIES)
def test_unlisted_well_formed_countries_are_kept_as_written(raw: str) -> None:
    result = accepted(validate_row(2, row(Country=raw)))
    assert result.record.country == raw
    assert result.fixes == ()


def test_inner_whitespace_in_country_is_collapsed() -> None:
    result = accepted(validate_row(2, row(Country="New   Zealand")))
    assert result.record.country == "New Zealand"


@pytest.mark.parametrize("raw", ["N/A", "?", "unknown", "NULL", "123", "USA1", "x" * 101])
def test_placeholder_or_malformed_countries_are_rejected(raw: str) -> None:
    assert "not a country name" in rejected(validate_row(2, row(Country=raw))).reason
