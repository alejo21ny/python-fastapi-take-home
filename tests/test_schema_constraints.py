"""Integration tests: the SQL CHECK constraints enforce the same rules as app.validation.

Each test runs inside a transaction that is always rolled back, so nothing persists.
"""

from collections.abc import Iterator
from dataclasses import fields, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any

import psycopg
import pytest
from psycopg import errors, sql

from app.domain import MAX_COUNTRY_LENGTH, MAX_DECIMAL_PLACES, CoffeeHealthRecord
from app.validation import Accepted, validate_row
from tests.boundary_cases import (
    HEADER,
    INSIDE,
    INTEGER_COLUMNS,
    OUTSIDE,
    WELL_FORMED_COUNTRIES,
    row,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def conn(test_database_url: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(test_database_url) as conn, conn.transaction(force_rollback=True):
        yield conn


def valid_record(**overrides: str) -> CoffeeHealthRecord:
    result = validate_row(2, row(**overrides))
    assert isinstance(result, Accepted), result
    return result.record


def tampered_record(column: str, value: Any) -> CoffeeHealthRecord:
    """A valid record with one field forced to a value Python validation would reject,
    simulating a client that writes to the table without going through the loader."""
    return replace(valid_record(), **{column: value})


def insert(conn: psycopg.Connection[tuple[object, ...]], record: CoffeeHealthRecord) -> None:
    values = {
        f.name: str(v) if isinstance(v := getattr(record, f.name), StrEnum) else v
        for f in fields(record)
    }
    query = sql.SQL("insert into coffee_health ({columns}) values ({values})").format(
        columns=sql.SQL(", ").join(map(sql.Identifier, values)),
        values=sql.SQL(", ").join(map(sql.Placeholder, values)),
    )
    conn.execute(query, values)


def assert_rejected(
    conn: psycopg.Connection[tuple[object, ...]], record: CoffeeHealthRecord
) -> None:
    with pytest.raises(errors.CheckViolation), conn.transaction():  # savepoint
        insert(conn, record)


@pytest.mark.parametrize(("column", "value"), INSIDE)
def test_sql_accepts_the_boundaries_python_accepts(
    conn: psycopg.Connection[tuple[object, ...]], column: str, value: Decimal
) -> None:
    insert(conn, valid_record(**{HEADER[column]: str(value)}))


@pytest.mark.parametrize(("column", "value"), OUTSIDE)
def test_sql_rejects_the_values_python_rejects(
    conn: psycopg.Connection[tuple[object, ...]], column: str, value: Decimal
) -> None:
    bad = int(value) if column in INTEGER_COLUMNS else value
    assert_rejected(conn, tampered_record(column, bad))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("gender", "male"),
        ("stress_level", "LOW"),
        ("country", " USA"),
        ("country", ""),
        ("country", "?"),
        ("country", "123"),
        ("country", "N/A"),
        ("country", "USA1"),
        ("country", "x" * (MAX_COUNTRY_LENGTH + 1)),
    ],
)
def test_sql_rejects_invalid_text(
    conn: psycopg.Connection[tuple[object, ...]], column: str, value: str
) -> None:
    assert_rejected(conn, tampered_record(column, value))


@pytest.mark.parametrize("country", WELL_FORMED_COUNTRIES)
def test_sql_accepts_the_country_names_python_accepts(
    conn: psycopg.Connection[tuple[object, ...]], country: str
) -> None:
    insert(conn, valid_record(Country=country))
    stored = conn.execute("select country from coffee_health where id = 1").fetchone()
    assert stored == (country,)


def test_decimals_round_trip_without_rounding(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    insert(conn, valid_record(Coffee_Intake="0.123456789"))
    stored = conn.execute("select coffee_intake from coffee_health where id = 1").fetchone()
    assert stored == (Decimal("0.123456789"),)


def test_sql_stores_the_longest_decimal_python_accepts(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    longest = "0." + "0" * (MAX_DECIMAL_PLACES - 1) + "1"
    insert(conn, valid_record(Coffee_Intake=longest))
    stored = conn.execute("select coffee_intake from coffee_health where id = 1").fetchone()
    assert stored == (Decimal(longest),)
