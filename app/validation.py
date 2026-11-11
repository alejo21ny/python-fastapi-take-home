"""Pure validation and normalization of raw CSV rows.

No I/O and no database: every function maps strings to values or errors, so the rules
are unit-testable and the loader can stream rows through them one at a time.

Policy: fix only what is unambiguous (surrounding whitespace, letter case, boolean
words) and reject anything that would need a guess. Every fix is reported.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.domain import (
    LIMITS,
    MAX_COUNTRY_LENGTH,
    MAX_ID,
    CoffeeHealthRecord,
    Gender,
    HealthIssues,
    Occupation,
    SleepQuality,
    StressLevel,
)

RawRow = Mapping[str, str | None]


@dataclass(frozen=True, slots=True)
class Accepted:
    line_no: int
    record: CoffeeHealthRecord
    fixes: tuple[str, ...]  # human-readable normalizations, empty if the row was clean


@dataclass(frozen=True, slots=True)
class Rejected:
    line_no: int
    reasons: tuple[str, ...]  # one entry per invalid field

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


ValidationResult = Accepted | Rejected


class _FieldError(ValueError):
    """A raw value that cannot be turned into a valid field value."""


_INTEGER = re.compile(r"[+-]?[0-9]+")
# Plain decimal notation only. Exponents ("1e3"), digit separators ("1_000") and
# non-ASCII digits are rejected as malformed instead of being guessed at.
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


def _parse_int(text: str) -> int:
    if not _INTEGER.fullmatch(text):
        raise _FieldError(f"not an integer: {text!r}")
    try:
        return int(text)
    except ValueError:  # longer than Python's int digit limit (4300 by default)
        raise _FieldError(f"integer has too many digits ({len(text)})") from None


def _parse_decimal(text: str) -> Decimal:
    """Parse exactly as written: no float conversion and no rounding."""
    if not _DECIMAL.fullmatch(text):
        raise _FieldError(f"not a decimal number: {text!r}")
    value = Decimal(text)
    # PostgreSQL numeric has no negative zero; normalize so Python and the DB agree.
    return abs(value) if value.is_zero() else value


def _parse_id(text: str) -> int:
    value = _parse_int(text)
    if not 1 <= value <= MAX_ID:
        raise _FieldError(f"{value} is not a valid ID (1–{MAX_ID})")
    return value


def _ranged[N: (int, Decimal)](column: str, parse: Callable[[str], N]) -> Callable[[str], N]:
    """Wrap a number parser with the plausibility range of `column` from LIMITS."""
    limits = LIMITS[column]

    def parse_in_range(text: str) -> N:
        value = parse(text)
        if value not in limits:
            raise _FieldError(f"{value} is outside the plausible range {limits.min}–{limits.max}")
        return value

    return parse_in_range


def _enum_parser[E: StrEnum](enum_type: type[E]) -> Callable[[str], E]:
    """Case-insensitive match against the enum's canonical values."""
    by_key = {member.value.casefold(): member for member in enum_type}

    def parse(text: str) -> E:
        try:
            return by_key[text.casefold()]
        except KeyError:
            allowed = ", ".join(enum_type)
            raise _FieldError(f"{text!r} is not one of {allowed}") from None

    return parse


_BOOLEANS = {
    "0": False,
    "1": True,
    "n": False,
    "y": True,
    "no": False,
    "yes": True,
    "false": False,
    "true": True,
}


def _parse_bool(text: str) -> bool:
    try:
        return _BOOLEANS[text.casefold()]
    except KeyError:
        raise _FieldError(f"{text!r} is not a boolean (0/1, yes/no, true/false)") from None


# Canonical spelling for countries seen in the source data. Other names are accepted
# as written, because the source system is not limited to these countries.
_KNOWN_COUNTRIES = {
    name.casefold(): name
    for name in (
        "Australia", "Belgium", "Brazil", "Canada", "China", "Finland", "France",
        "Germany", "India", "Italy", "Japan", "Mexico", "Netherlands", "Norway",
        "South Korea", "Spain", "Sweden", "Switzerland", "UK", "USA",
    )
}  # fmt: skip
# Words of letters joined by single spaces, hyphens, apostrophes or abbreviation dots,
# e.g. "Côte d'Ivoire", "Guinea-Bissau", "St. Lucia". Digits and symbols are rejected.
_COUNTRY_NAME = re.compile(r"[^\W\d_]+(?:(?:[ '’-]|\. ?)[^\W\d_]+)*\.?")
_PLACEHOLDERS = frozenset({"na", "nan", "none", "null", "unknown"})


def _parse_country(text: str) -> str:
    name = " ".join(text.split())  # also collapses inner runs of whitespace
    if known := _KNOWN_COUNTRIES.get(name.casefold()):
        return known
    if (
        name.casefold() in _PLACEHOLDERS
        or len(name) > MAX_COUNTRY_LENGTH
        or not _COUNTRY_NAME.fullmatch(name)
    ):
        raise _FieldError(f"not a country name: {text!r}")
    return name


# CSV header -> (record field, parser), in file order.
_COLUMNS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "ID": ("id", _parse_id),
    "Age": ("age", _ranged("age", _parse_int)),
    "Gender": ("gender", _enum_parser(Gender)),
    "Country": ("country", _parse_country),
    "Coffee_Intake": ("coffee_intake", _ranged("coffee_intake", _parse_decimal)),
    "Caffeine_mg": ("caffeine_mg", _ranged("caffeine_mg", _parse_decimal)),
    "Sleep_Hours": ("sleep_hours", _ranged("sleep_hours", _parse_decimal)),
    "Sleep_Quality": ("sleep_quality", _enum_parser(SleepQuality)),
    "BMI": ("bmi", _ranged("bmi", _parse_decimal)),
    "Heart_Rate": ("heart_rate", _ranged("heart_rate", _parse_int)),
    "Stress_Level": ("stress_level", _enum_parser(StressLevel)),
    "Physical_Activity_Hours": (
        "physical_activity_hours",
        _ranged("physical_activity_hours", _parse_decimal),
    ),
    "Health_Issues": ("health_issues", _enum_parser(HealthIssues)),
    "Occupation": ("occupation", _enum_parser(Occupation)),
    "Smoking": ("smoking", _parse_bool),
    "Alcohol_Consumption": ("alcohol_consumption", _parse_bool),
}


def validate_row(line_no: int, raw: RawRow) -> ValidationResult:
    """Validate one CSV row (header -> raw text), reporting every invalid field."""
    values: dict[str, Any] = {}
    fixes: list[str] = []
    errors: list[str] = []

    for header, (field, parse) in _COLUMNS.items():
        original = raw.get(header) or ""
        text = original.strip()
        if not text:
            errors.append(f"{header}: missing")
            continue
        try:
            value = parse(text)
        except _FieldError as exc:
            errors.append(f"{header}: {exc}")
            continue
        values[field] = value
        if (normalized := _as_text(value)) != original:
            fixes.append(f"{header}: {original!r} -> {normalized!r}")

    if errors:
        return Rejected(line_no, tuple(errors))
    return Accepted(line_no, CoffeeHealthRecord(**values), tuple(fixes))


def _as_text(value: object) -> str:
    """How a parsed value reads back in CSV form; used to detect what was changed."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)
