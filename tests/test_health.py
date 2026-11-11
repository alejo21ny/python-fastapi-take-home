import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_notes(client: TestClient) -> None:
    response = client.get("/notes")
    assert response.status_code == 200
    assert len(response.json()) == 3


def test_api_runs_against_the_test_database(client: TestClient) -> None:
    with app.state.pool.connection() as conn:
        row = conn.execute("select current_database() as name").fetchone()
    assert row == {"name": "coffee_test"}
