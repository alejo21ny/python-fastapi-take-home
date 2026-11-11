"""Load the coffee health CSV into Postgres.

Usage:
    uv run python -m scripts.load_data data/coffee_health_raw.csv

The file is streamed: each line is parsed and validated by app/validation.py, and
valid records are copied into a temporary staging table. PostgreSQL then resolves
repeated IDs, and the remaining records are upserted into coffee_health. Everything
runs in one transaction: a failed run changes nothing, and a repeated run finds
nothing to change. Python keeps only counters, so memory does not grow with the file.
"""

import argparse
import csv
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql

from app.config import settings
from app.domain import CoffeeHealthRecord
from app.validation import CSV_HEADERS, Accepted, Rejected, validate_row

COLUMNS = [f.name for f in fields(CoffeeHealthRecord)]
VALUE_COLUMNS = [c for c in COLUMNS if c != "id"]


class LoadError(Exception):
    """The file cannot be loaded at all, for example because of a wrong header."""


@dataclass(frozen=True, slots=True)
class Blank:
    line_no: int


@dataclass(frozen=True, slots=True)
class Malformed:
    line_no: int
    reason: str


ParsedLine = Blank | Malformed | Accepted | Rejected


def parse_csv(path: Path) -> Iterator[ParsedLine]:
    """Classify each data line of the file, one at a time.

    `utf-8-sig` drops a byte-order mark if present. Line numbers are physical lines of
    the file, with the header as line 1.
    """
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        header = next(reader, [])
        if tuple(header) != CSV_HEADERS:
            raise LoadError(f"unexpected header {header}; expected {list(CSV_HEADERS)}")
        while True:
            try:
                row = next(reader)
            except StopIteration:
                return
            except csv.Error as exc:  # e.g. a field above the csv module's size limit
                yield Malformed(reader.line_num, str(exc))
                continue
            if not row:
                yield Blank(reader.line_num)
            elif len(row) != len(header):
                yield Malformed(reader.line_num, f"expected {len(header)} fields, got {len(row)}")
            else:
                yield validate_row(reader.line_num, dict(zip(header, row, strict=True)))


@dataclass(frozen=True, slots=True)
class Issue:
    """A data line that was not loaded, or not loaded as its own row."""

    line_no: int
    kind: Literal["blank", "malformed", "rejected", "duplicate", "conflict"]
    detail: str


@dataclass(slots=True)
class LoadSummary:
    read: int = 0  # data lines after the header; each one lands in exactly one bucket:
    blank: int = 0
    malformed: int = 0
    rejected: int = 0
    duplicate: int = 0  # identical to an earlier line with the same ID
    conflicting: int = 0  # lines whose ID appears with different values; none loaded
    inserted: int = 0
    updated: int = 0  # the ID was already stored with different values
    unchanged: int = 0  # the ID was already stored with the same values
    # Not buckets:
    fixed: int = 0  # valid lines that needed a normalization (see app/validation.py)
    conflicting_ids: int = 0
    rows_in_table: int = 0

    @property
    def loaded(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _columns(names: list[str], table: str | None = None) -> sql.Composed:
    return sql.SQL(", ").join(
        sql.Identifier(table, name) if table else sql.Identifier(name) for name in names
    )


_CREATE_STAGING = sql.SQL(
    "create temp table staging (line_no integer not null, like coffee_health) on commit drop"
)
_COPY = sql.SQL("copy staging (line_no, {}) from stdin").format(_columns(COLUMNS))
# IDs seen on more than one line. `conflicting` compares values, so "3.5" and "3.50"
# or " Male" and "Male" (after normalization) count as identical.
_FIND_REPEATED_IDS = sql.SQL("""
    create temp table repeated_ids on commit drop as
    select id, min(line_no) as first_line, count(*) as copies,
           count(distinct ({values})) > 1 as conflicting
    from staging group by id having count(*) > 1
""").format(values=_columns(VALUE_COLUMNS))
_SKIPPED_LINES = sql.SQL("""
    select s.line_no, s.id, r.first_line, r.copies, r.conflicting
    from staging s join repeated_ids r using (id)
    where r.conflicting or s.line_no <> r.first_line
    order by s.line_no
""")
_DROP_SKIPPED_LINES = sql.SQL("""
    delete from staging s using repeated_ids r
    where s.id = r.id and (r.conflicting or s.line_no <> r.first_line)
""")
_UPDATE_CHANGED = sql.SQL("""
    update coffee_health t set ({columns}) = ({new})
    from staging s
    where t.id = s.id and ({old}) is distinct from ({new})
""").format(
    columns=_columns(VALUE_COLUMNS),
    new=_columns(VALUE_COLUMNS, "s"),
    old=_columns(VALUE_COLUMNS, "t"),
)
_INSERT_NEW = sql.SQL("""
    insert into coffee_health ({columns}) select {columns} from staging
    on conflict (id) do nothing
""").format(columns=_columns(COLUMNS))


def _as_db_values(line_no: int, record: CoffeeHealthRecord) -> list[Any]:
    values = (getattr(record, column) for column in COLUMNS)
    # Explicit, so the text stored never depends on how psycopg adapts enums.
    return [line_no, *(str(v) if isinstance(v, StrEnum) else v for v in values)]


def load(
    conn: psycopg.Connection[Any], path: Path, on_issue: Callable[[Issue], None]
) -> LoadSummary:
    """Load `path` into coffee_health in one transaction and return what happened.

    Every line that is not loaded as its own row is passed to `on_issue`.
    """
    summary = LoadSummary()
    with conn.transaction():
        conn.execute(_CREATE_STAGING)
        with conn.cursor() as cur, cur.copy(_COPY) as copy:
            for line in parse_csv(path):
                summary.read += 1
                match line:
                    case Blank(line_no):
                        summary.blank += 1
                        on_issue(Issue(line_no, "blank", "empty line"))
                    case Malformed(line_no, reason):
                        summary.malformed += 1
                        on_issue(Issue(line_no, "malformed", reason))
                    case Rejected(line_no):
                        summary.rejected += 1
                        on_issue(Issue(line_no, "rejected", line.reason))
                    case Accepted(line_no, record, fixes):
                        if fixes:
                            summary.fixed += 1
                        copy.write_row(_as_db_values(line_no, record))
        conn.execute("analyze staging")  # temp tables are never auto-analyzed

        conn.execute(_FIND_REPEATED_IDS)
        # A server-side cursor streams the rows in batches instead of buffering them all.
        with conn.cursor(name="skipped_lines") as skipped:
            for line_no, id_, first_line, copies, conflicting in skipped.execute(_SKIPPED_LINES):
                if conflicting:
                    summary.conflicting += 1
                    detail = f"ID {id_} differs across {copies} lines, first on line {first_line}"
                    on_issue(Issue(line_no, "conflict", detail))
                else:
                    summary.duplicate += 1
                    detail = f"ID {id_} identical to line {first_line}"
                    on_issue(Issue(line_no, "duplicate", detail))
        summary.conflicting_ids = _count(
            conn, "select count(*) from repeated_ids where conflicting"
        )
        conn.execute(_DROP_SKIPPED_LINES)

        summary.updated = conn.execute(_UPDATE_CHANGED).rowcount
        summary.inserted = conn.execute(_INSERT_NEW).rowcount
        staged = _count(conn, "select count(*) from staging")  # one line per ID left
        summary.unchanged = staged - summary.updated - summary.inserted
        summary.rows_in_table = _count(conn, "select count(*) from coffee_health")
    return summary


def _count(conn: psycopg.Connection[Any], query: str) -> int:
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def format_summary(s: LoadSummary) -> str:
    return "\n".join(
        [
            f"Lines read        {s.read:>8}",
            f"  blank           {s.blank:>8}",
            f"  malformed       {s.malformed:>8}",
            f"  rejected        {s.rejected:>8}",
            f"  duplicate       {s.duplicate:>8}  (identical copies, loaded once)",
            f"  conflicting     {s.conflicting:>8}  ({s.conflicting_ids} IDs, none loaded)",
            f"  loaded          {s.loaded:>8}  (inserted {s.inserted}, updated {s.updated},"
            f" unchanged {s.unchanged})",
            f"Valid lines fixed {s.fixed:>8}  (normalized during validation)",
            f"Rows in coffee_health: {s.rows_in_table}",
        ]
    )


def _print_issue(issue: Issue) -> None:
    print(f"line {issue.line_no}: {issue.kind}: {issue.detail}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()
    try:
        with psycopg.connect(settings.database_url) as conn:
            summary = load(conn, args.csv_path, on_issue=_print_issue)
    except (LoadError, OSError, UnicodeDecodeError, csv.Error, psycopg.Error) as exc:
        raise SystemExit(f"Load failed, the database was not changed: {exc}") from None
    print(format_summary(summary))


if __name__ == "__main__":
    main()
