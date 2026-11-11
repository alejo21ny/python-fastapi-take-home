"""Small CSV files for loader tests, built from the valid row in boundary_cases."""

from pathlib import Path

from app.validation import CSV_HEADERS
from tests.boundary_cases import row


def line(**overrides: str) -> str:
    """One data line: the valid row with some fields replaced, keyed by CSV header."""
    values = row(**overrides)
    return ",".join(values[header] for header in CSV_HEADERS)


def write_csv(directory: Path, *lines: str, bom: bool = True) -> Path:
    """A CSV file with the expected header followed by `lines`, as the source exports it."""
    path = directory / "input.csv"
    text = "\n".join([",".join(CSV_HEADERS), *lines]) + "\n"
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8", newline="")
    return path
