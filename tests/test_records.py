"""Integration tests: GET /records through TestClient against the real test database."""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.domain import MAX_DECIMAL_PLACES
from scripts.load_data import Issue, load
from tests.boundary_cases import varied_digits
from tests.csv_files import line, write_csv

pytestmark = pytest.mark.integration

# Five records loaded once for this module, through the real loader.
LINES = [
    line(ID="1", Age="25", Gender="Male", Country="Germany", Coffee_Intake="1.0", BMI="20.0",
         Sleep_Quality="Good", Stress_Level="Low"),
    line(ID="2", Age="40", Gender="Female", Country="USA", Coffee_Intake="3.5", BMI="24.9",
         Sleep_Quality="Poor", Stress_Level="High"),
    line(ID="3", Age="60", Gender="Other", Country="Japan", Coffee_Intake="5.0", BMI="30.0",
         Sleep_Quality="Fair", Stress_Level="Medium"),
    line(ID="4", Age="33", Gender="Female", Country="Germany", Coffee_Intake="2.0", BMI="22.5",
         Sleep_Quality="Excellent", Stress_Level="Low"),
    line(ID="5", Age="18", Gender="Male", Country="USA", Coffee_Intake="0", BMI="18.5",
         Sleep_Quality="Good", Stress_Level="High"),
]  # fmt: skip


def _no_issues(issue: Issue) -> None:
    pytest.fail(f"test data should load cleanly: {issue}")


@pytest.fixture(scope="module", autouse=True)
def records(test_database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    path = write_csv(tmp_path_factory.mktemp("records"), *LINES)
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute("truncate coffee_health")
        load(conn, path, on_issue=_no_issues)
        yield
        conn.execute("truncate coffee_health")


def ids(client: TestClient, **params: Any) -> list[int]:
    response = client.get("/records", params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_without_filters_records_come_ordered_by_id(client: TestClient) -> None:
    response = client.get("/records")
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [1, 2, 3, 4, 5]
    assert body["next_after_id"] is None
    assert body["items"][1] == {
        "id": 2, "age": 40, "gender": "Female", "country": "USA", "coffee_intake": "3.5",
        "caffeine_mg": "328.1", "sleep_hours": "7.5", "sleep_quality": "Poor", "bmi": "24.9",
        "heart_rate": 78, "stress_level": "High", "physical_activity_hours": "14.5",
        "health_issues": "None", "occupation": "Other", "smoking": False,
        "alcohol_consumption": False,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"country": "USA"}, [2, 5]),
        ({"country": ["USA", "Japan"]}, [2, 3, 5]),
        ({"country": "Narnia"}, []),
        ({"gender": "Female"}, [2, 4]),
        ({"sleep_quality": ["Good", "Fair"]}, [1, 3, 5]),
        ({"stress_level": "Low"}, [1, 4]),
        ({"min_age": 33, "max_age": 40}, [2, 4]),
        ({"min_coffee_intake": "2", "max_coffee_intake": "3.5"}, [2, 4]),
        ({"min_bmi": "24.9"}, [2, 3]),
        ({"max_bmi": "18.5"}, [5]),
        ({"country": "Germany", "stress_level": "Low", "min_age": 30}, [4]),
    ],
)
def test_filters(client: TestClient, params: dict[str, Any], expected: list[int]) -> None:
    assert ids(client, **params) == expected


@pytest.mark.parametrize(
    ("params", "pages"),
    [
        ({"limit": 2}, [[1, 2], [3, 4], [5]]),
        ({"limit": 1, "country": ["Germany", "USA"]}, [[1], [2], [4], [5]]),
        ({"limit": 5}, [[1, 2, 3, 4, 5]]),
    ],
)
def test_pages_follow_next_after_id_until_the_end(
    client: TestClient, params: dict[str, Any], pages: list[list[int]]
) -> None:
    seen: list[list[int]] = []
    after_id = 0
    while True:
        body = client.get("/records", params={**params, "after_id": after_id}).json()
        seen.append([item["id"] for item in body["items"]])
        if body["next_after_id"] is None:
            break
        after_id = body["next_after_id"]
    assert seen == pages


def test_after_the_last_id_the_page_is_empty(client: TestClient) -> None:
    assert client.get("/records", params={"after_id": 5}).json() == {
        "items": [],
        "next_after_id": None,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"min_age": 40, "max_age": 30},
        {"min_coffee_intake": "3", "max_coffee_intake": "1"},
        {"min_bmi": "30", "max_bmi": "20"},
        {"gender": "male"},
        {"stress_level": "low"},
        {"min_age": "thirty"},
        {"limit": 0},
        {"limit": 501},
        {"after_id": -1},
        {"colour": "red"},
        # Not representable as PostgreSQL numeric: rejected before any query runs.
        {"min_bmi": "1e1000000"},
        {"min_bmi": "1e-1000000"},
        {"max_coffee_intake": "-1e1000000"},
    ],
)
def test_invalid_parameters_return_422(client: TestClient, params: dict[str, Any]) -> None:
    response = client.get("/records", params=params)
    assert response.status_code == 422, response.text


def test_422_explains_an_inverted_range(client: TestClient) -> None:
    response = client.get("/records", params={"min_age": 40, "max_age": 30})
    assert "min_age must not be greater than max_age" in response.text


# Decimals a float cannot hold: more than 17 significant digits, and the most decimal
# places validation accepts.
PRECISE = "0.12345678901234567890"
NEAR_MAX_BMI = "79.99999999999999999999"
LONGEST = "0.0" + varied_digits(MAX_DECIMAL_PLACES - 1)  # below PRECISE


@pytest.fixture
def precise_records(test_database_url: str, tmp_path: Path) -> Iterator[None]:
    path = write_csv(
        tmp_path,
        line(ID="100", Coffee_Intake=PRECISE, BMI=NEAR_MAX_BMI),
        line(ID="101", Coffee_Intake=LONGEST),
    )
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        load(conn, path, on_issue=_no_issues)
        yield
        conn.execute("delete from coffee_health where id >= 100")


@pytest.mark.usefixtures("precise_records")
def test_decimals_are_returned_as_exact_strings(client: TestClient) -> None:
    body = client.get("/records", params={"after_id": 99}).json()
    by_id = {item["id"]: item for item in body["items"]}

    assert all(isinstance(by_id[100][f], str) for f in ("coffee_intake", "bmi"))
    assert Decimal(by_id[100]["coffee_intake"]) == Decimal(PRECISE)
    assert Decimal(by_id[100]["bmi"]) == Decimal(NEAR_MAX_BMI)
    assert Decimal(by_id[101]["coffee_intake"]) == Decimal(LONGEST)
    assert isinstance(by_id[100]["age"], int) and isinstance(by_id[100]["heart_rate"], int)


@pytest.mark.usefixtures("precise_records")
def test_decimal_filters_and_pages_compare_exact_values(client: TestClient) -> None:
    assert ids(client, after_id=99, min_bmi=NEAR_MAX_BMI) == [100]
    assert ids(client, after_id=99, min_bmi=NEAR_MAX_BMI + "1") == []
    assert ids(client, after_id=99, max_coffee_intake=PRECISE) == [100, 101]
    assert ids(client, after_id=99, min_coffee_intake=PRECISE + "1") == []

    first = client.get("/records", params={"after_id": 99, "limit": 1}).json()
    assert [item["id"] for item in first["items"]] == [100]
    second = client.get("/records", params={"after_id": first["next_after_id"]}).json()
    assert [item["id"] for item in second["items"]] == [101]
    assert second["next_after_id"] is None
