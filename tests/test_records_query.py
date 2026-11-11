"""Unit tests for the /records query parameters and the SQL they produce. No database."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.main import app
from app.records import RecordQuery, _build_query


def test_defaults_start_at_the_first_page() -> None:
    q = RecordQuery()
    assert (q.after_id, q.limit) == (0, 50)


@pytest.mark.parametrize("column", ["age", "coffee_intake", "bmi"])
def test_a_minimum_above_its_maximum_is_invalid(column: str) -> None:
    with pytest.raises(ValidationError, match=f"min_{column} must not be greater"):
        RecordQuery.model_validate({f"min_{column}": 40, f"max_{column}": 30})


@pytest.mark.parametrize("column", ["age", "coffee_intake", "bmi"])
def test_equal_minimum_and_maximum_is_valid(column: str) -> None:
    RecordQuery.model_validate({f"min_{column}": 30, f"max_{column}": 30})


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 501}, {"after_id": -1}, {"gender": ["male"]}, {"colour": "red"}],
)
def test_invalid_parameters_are_rejected(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RecordQuery.model_validate(params)


@pytest.mark.parametrize("value", ["1e1000000", "1e-1000000", "-1e1000000", "1e131072", "1e-1001"])
def test_decimal_filters_postgres_cannot_represent_are_invalid(value: str) -> None:
    with pytest.raises(ValidationError):
        RecordQuery.model_validate({"min_bmi": value})


@pytest.mark.parametrize(
    "value", ["24.9", "0.1e2", "1e131071", "1e-1000", "79.99999999999999999999"]
)
def test_representable_decimal_filters_are_kept_exactly(value: str) -> None:
    assert RecordQuery.model_validate({"max_bmi": value}).max_bmi == Decimal(value)


def test_only_given_filters_become_conditions() -> None:
    query, params = _build_query(RecordQuery(country=["USA"], min_age=30))
    assert query.as_string().endswith(
        ' from coffee_health where id > %(after_id)s and "country" = any(%(country)s)'
        ' and "age" >= %(min_age)s order by id limit %(limit)s'
    )
    assert params == {"after_id": 0, "limit": 51, "country": ["USA"], "min_age": 30}


def test_values_are_bound_as_parameters_never_written_into_the_sql() -> None:
    hostile = "USA') or true --"
    query, params = _build_query(RecordQuery(country=[hostile]))
    assert hostile not in query.as_string()
    assert params["country"] == [hostile]


def test_openapi_documents_decimals_as_exact_strings_and_integers_as_numbers() -> None:
    properties = app.openapi()["components"]["schemas"]["Record"]["properties"]
    for field in ("coffee_intake", "caffeine_mg", "sleep_hours", "bmi", "physical_activity_hours"):
        assert (properties[field]["type"], properties[field]["format"]) == ("string", "decimal")
    for field in ("id", "age", "heart_rate"):
        assert properties[field]["type"] == "integer"
