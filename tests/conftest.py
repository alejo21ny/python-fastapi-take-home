from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import settings
from app.main import app

SCHEMA_DIR = Path(__file__).parent.parent / "db/init"

# The only database integration tests may touch: a dedicated database in this
# project's container, published on loopback only (see docker-compose.yml). The guard
# below keeps tests away from the development database and from any other Postgres on
# this machine, such as one on 5433.
TEST_TARGET = "127.0.0.1:5434/coffee_test"


class _TestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: str = f"postgresql://postgres:postgres@{TEST_TARGET}"


@pytest.fixture(scope="session", autouse=True)
def _app_never_uses_dev_database() -> Iterator[None]:
    """Point the app at TEST_DATABASE_URL for the whole session, before any TestClient
    starts, so no test can reach the development database `coffee`. Does not connect."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "database_url", _TestSettings().test_database_url)
        yield


@pytest.fixture(scope="session")
def test_database_url(_app_never_uses_dev_database: None) -> str:
    """TEST_DATABASE_URL, verified, created if missing and rebuilt from db/init/*.sql.

    Fails (never skips) when the target is wrong or unreachable, so integration tests
    cannot pass silently.
    """
    url = settings.database_url  # already TEST_DATABASE_URL, see above
    params = conninfo_to_dict(url)
    target = f"{params.get('host')}:{params.get('port')}/{params.get('dbname')}"
    if target != TEST_TARGET:
        pytest.fail(
            f"Refusing to run integration tests against {target}; "
            f"TEST_DATABASE_URL must point at {TEST_TARGET}.",
            pytrace=False,
        )
    try:
        _create_database_if_missing(url)
        _rebuild_schema(url)
    except psycopg.OperationalError as exc:
        pytest.fail(f"Test database {target} unreachable (run `make db-up`): {exc}", pytrace=False)
    return url


def _create_database_if_missing(url: str) -> None:
    name = conninfo_to_dict(url)["dbname"]
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", [name]).fetchone()
        if not exists:
            conn.execute(sql.SQL("create database {}").format(sql.Identifier(str(name))))


def _rebuild_schema(url: str) -> None:
    """Recreate the schema from scratch in one transaction, exactly as db/init defines it."""
    with psycopg.connect(url) as conn:
        conn.execute("drop schema public cascade; create schema public")
        for script in sorted(SCHEMA_DIR.glob("*.sql")):
            conn.execute(script.read_text(encoding="utf-8"))


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    # Using TestClient as a context manager runs the app lifespan (opens the DB pool).
    with TestClient(app) as c:
        yield c
