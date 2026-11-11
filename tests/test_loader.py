"""Integration tests: the loader against the real test database.

The loader commits, so every test starts and ends with an empty coffee_health table.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from scripts.load_data import Issue, LoadError, LoadSummary, load, main
from tests.csv_files import line, write_csv

pytestmark = pytest.mark.integration

DATASET = Path(__file__).parent.parent / "data/coffee_health_raw.csv"
Conn = psycopg.Connection[tuple[Any, ...]]


@pytest.fixture
def conn(test_database_url: str) -> Iterator[Conn]:
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute("truncate coffee_health")
        yield conn
        conn.execute("truncate coffee_health")


def run(conn: Conn, path: Path) -> tuple[LoadSummary, list[Issue]]:
    issues: list[Issue] = []
    summary = load(conn, path, on_issue=issues.append)
    return summary, issues


def table(conn: Conn) -> list[tuple[Any, ...]]:
    return conn.execute("select * from coffee_health order by id").fetchall()


def assert_every_line_accounted_for(s: LoadSummary) -> None:
    buckets = s.blank + s.malformed + s.rejected + s.duplicate + s.conflicting + s.loaded
    assert buckets == s.read


def test_every_line_is_loaded_or_explained(conn: Conn, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        line(ID="1"),  # line 2
        "",  # 3: blank
        line(ID="9") + ",extra",  # 4: malformed
        line(ID="9", Age="thirty"),  # 5: rejected
        line(ID="2", Gender="MALE"),  # 6: fixed, loaded
        line(ID="1", Gender=" Male", Coffee_Intake="3.50"),  # 7: same values as line 2
        line(ID="3", Age="30"),  # 8: conflicts with line 9
        line(ID="3", Age="31"),  # 9
        line(ID="4"),  # 10
    )
    summary, issues = run(conn, path)

    assert summary == LoadSummary(
        read=9, blank=1, malformed=1, rejected=1, duplicate=1, conflicting=2, inserted=3,
        fixed=2, conflicting_ids=1, rows_in_table=3,
    )  # fmt: skip
    assert_every_line_accounted_for(summary)
    assert [(i.line_no, i.kind) for i in issues] == [
        (3, "blank"),
        (4, "malformed"),
        (5, "rejected"),
        (7, "duplicate"),
        (8, "conflict"),
        (9, "conflict"),
    ]
    detail = {i.line_no: i.detail for i in issues}
    assert detail[7] == "ID 1 identical to line 2"
    assert detail[9] == "ID 3 differs across 2 lines, first on line 8"
    assert [r[0] for r in table(conn)] == [1, 2, 4]
    # The first of two identical lines is stored, as written after validation.
    stored = conn.execute("select gender, coffee_intake::text from coffee_health where id = 1")
    assert stored.fetchone() == ("Male", "3.5")


def test_many_copies_of_an_id_give_one_short_issue_per_line(conn: Conn, tmp_path: Path) -> None:
    copies = 2_000
    path = write_csv(
        tmp_path,
        *(line(ID="1") for _ in range(copies)),  # lines 2..2001: identical
        *(line(ID="2", Age=str(30 + n % 2)) for n in range(copies)),  # two versions
    )
    summary, issues = run(conn, path)

    assert (summary.duplicate, summary.conflicting, summary.inserted) == (copies - 1, copies, 1)
    assert len(issues) == len({i.line_no for i in issues}) == 2 * copies - 1
    assert {i.detail for i in issues if i.kind == "conflict"} == {
        f"ID 2 differs across {copies} lines, first on line {copies + 2}"
    }


def test_a_decimal_postgres_cannot_store_is_rejected_and_the_rest_loads(
    conn: Conn, tmp_path: Path
) -> None:
    too_precise = "0." + "0" * 17_000 + "1"
    path = write_csv(tmp_path, line(ID="1"), line(ID="2", Coffee_Intake=too_precise), line(ID="3"))
    summary, issues = run(conn, path)

    assert (summary.rejected, summary.inserted) == (1, 2)
    assert issues == [
        Issue(3, "rejected", "Coffee_Intake: more than 16383 digits after the decimal point")
    ]


def test_second_run_of_the_same_file_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    path = write_csv(tmp_path, line(ID="1"), line(ID="2"), line(ID="2"), line(ID="3"))
    first, _ = run(conn, path)
    before = table(conn)
    second, _ = run(conn, path)

    assert (first.inserted, first.updated, first.unchanged) == (3, 0, 0)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 3)
    assert table(conn) == before


def test_changed_values_for_a_stored_id_update_that_row_only(conn: Conn, tmp_path: Path) -> None:
    run(conn, write_csv(tmp_path, line(ID="1"), line(ID="2")))
    summary, _ = run(conn, write_csv(tmp_path, line(ID="1", Age="41"), line(ID="2")))

    assert (summary.inserted, summary.updated, summary.unchanged) == (0, 1, 1)
    assert conn.execute("select id, age from coffee_health order by id").fetchall() == [
        (1, 41),
        (2, 40),
    ]


def test_a_conflict_never_overwrites_the_stored_row(conn: Conn, tmp_path: Path) -> None:
    run(conn, write_csv(tmp_path, line(ID="1")))
    before = table(conn)
    summary, _ = run(conn, write_csv(tmp_path, line(ID="1", Age="50"), line(ID="1", Age="60")))

    assert (summary.conflicting, summary.loaded) == (2, 0)
    assert table(conn) == before


def test_a_failed_load_leaves_the_table_as_it_was(conn: Conn, tmp_path: Path) -> None:
    run(conn, write_csv(tmp_path, line(ID="1")))
    before = table(conn)
    # Enough valid lines to be decoded, validated and copied before the bad bytes are read.
    path = write_csv(tmp_path, *(line(ID=str(i)) for i in range(2, 2_002)))
    path.write_bytes(path.read_bytes() + b"\xff\xfe not utf-8\n")

    with pytest.raises(UnicodeDecodeError):
        run(conn, path)
    assert table(conn) == before
    run(conn, write_csv(tmp_path, line(ID="2")))  # the connection is still usable
    assert [r[0] for r in table(conn)] == [1, 2]


def test_a_wrong_header_loads_nothing(conn: Conn, tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("id,age\n1,40\n", encoding="utf-8")
    with pytest.raises(LoadError):
        run(conn, path)
    assert table(conn) == []


def test_provided_dataset(conn: Conn) -> None:
    """Every line of data/coffee_health_raw.csv is loaded or explained, as in NOTES.md."""
    summary, issues = run(conn, DATASET)

    assert summary == LoadSummary(
        read=10_015, blank=3, malformed=3, rejected=22, duplicate=8, conflicting=8,
        inserted=9_971, fixed=56, conflicting_ids=4, rows_in_table=9_971,
    )  # fmt: skip
    assert_every_line_accounted_for(summary)
    assert len(issues) == (
        summary.blank
        + summary.malformed
        + summary.rejected
        + summary.duplicate
        + summary.conflicting
    )
    conflicting = {3338, 6115, 6748, 7494}
    stored = conn.execute("select count(*), count(distinct id) from coffee_health").fetchone()
    assert stored == (9_971, 9_971)
    present = conn.execute("select id from coffee_health where id = any(%s)", [list(conflicting)])
    assert present.fetchall() == []

    again, _ = run(conn, DATASET)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 9_971)


def test_command_line_prints_issues_and_summary(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_csv(tmp_path, line(ID="1"), line(ID="2", Age="thirty"))
    monkeypatch.setattr("sys.argv", ["load_data.py", str(path)])
    main()  # uses settings.database_url, which conftest points at the test database

    out, err = capsys.readouterr()
    assert err.startswith("line 3: rejected: Age: ")
    assert "inserted 1, updated 0, unchanged 0" in out
    assert "Rows in coffee_health: 1" in out


def test_command_line_reports_a_failed_load(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["load_data.py", str(tmp_path / "missing.csv")])
    with pytest.raises(SystemExit, match="Load failed, the database was not changed"):
        main()
