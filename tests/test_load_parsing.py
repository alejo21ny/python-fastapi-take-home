"""Unit tests for how the loader reads and classifies lines. No database required."""

from pathlib import Path

import pytest

from app.validation import CSV_HEADERS, Accepted, Rejected
from scripts.load_data import Blank, LoadError, Malformed, parse_csv
from tests.csv_files import line, write_csv


@pytest.mark.parametrize("bom", [True, False])
def test_header_is_recognized_with_or_without_byte_order_mark(tmp_path: Path, bom: bool) -> None:
    [parsed] = parse_csv(write_csv(tmp_path, line(), bom=bom))
    assert isinstance(parsed, Accepted)
    assert parsed.line_no == 2


def test_each_line_is_classified_with_its_physical_line_number(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        line(ID="1"),
        "",
        line(ID="2") + ",extra",
        ",".join(["3", "40", "Male"]),
        line(ID="4", Age="thirty"),
        line(ID="5", Gender="MALE"),
    )
    parsed = list(parse_csv(path))

    assert [type(p) for p in parsed] == [Accepted, Blank, Malformed, Malformed, Rejected, Accepted]
    assert [p.line_no for p in parsed] == [2, 3, 4, 5, 6, 7]
    assert parsed[2] == Malformed(4, "expected 16 fields, got 17")
    assert parsed[3] == Malformed(5, "expected 16 fields, got 3")
    assert isinstance(parsed[4], Rejected) and parsed[4].reason.startswith("Age: ")
    assert isinstance(parsed[5], Accepted) and parsed[5].fixes == ("Gender: 'MALE' -> 'Male'",)


def test_windows_line_endings_are_read_like_unix_ones(tmp_path: Path) -> None:
    path = tmp_path / "crlf.csv"
    path.write_bytes(
        ("\r\n".join([",".join(CSV_HEADERS), line(), "", line(ID="2")]) + "\r\n").encode()
    )
    assert [(type(p), p.line_no) for p in parse_csv(path)] == [
        (Accepted, 2),
        (Blank, 3),
        (Accepted, 4),
    ]


def test_a_line_the_csv_module_cannot_read_is_malformed_and_reading_continues(
    tmp_path: Path,
) -> None:
    huge = "x" * 200_000  # above csv.field_size_limit() (131,072 by default)
    path = write_csv(tmp_path, line(Country=huge), line(ID="2"))
    first, second = parse_csv(path)
    assert isinstance(first, Malformed) and "field larger than field limit" in first.reason
    assert isinstance(second, Accepted) and second.record.id == 2


@pytest.mark.parametrize(
    "header",
    ["", "id,age", ",".join(CSV_HEADERS[:-1]), ",".join(h.lower() for h in CSV_HEADERS)],
)
def test_unexpected_header_stops_the_load(tmp_path: Path, header: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(header + "\n" + line() + "\n", encoding="utf-8")
    with pytest.raises(LoadError, match="unexpected header"):
        list(parse_csv(path))
