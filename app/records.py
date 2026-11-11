"""GET /records: filtered, keyset-paginated coffee health records."""

from decimal import Decimal
from typing import Annotated, Any, Self

from fastapi import APIRouter, Query
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db import Conn
from app.domain import (
    MAX_DECIMAL_PLACES,
    MAX_ID,
    Gender,
    HealthIssues,
    Occupation,
    SleepQuality,
    StressLevel,
)

router = APIRouter()

# Decimals stay Decimal end to end; Pydantic writes them as exact JSON strings ("3.5"),
# because a JSON number would be read as a float and could be rounded.
ExactDecimal = Annotated[
    Decimal, Field(json_schema_extra={"format": "decimal", "examples": ["3.5"]})
]


class Record(BaseModel):
    id: int
    age: int
    gender: Gender
    country: str
    coffee_intake: ExactDecimal
    caffeine_mg: ExactDecimal
    sleep_hours: ExactDecimal
    sleep_quality: SleepQuality
    bmi: ExactDecimal
    heart_rate: int
    stress_level: StressLevel
    physical_activity_hours: ExactDecimal
    health_issues: HealthIssues
    occupation: Occupation
    smoking: bool
    alcohol_consumption: bool


class RecordPage(BaseModel):
    items: list[Record]
    next_after_id: int | None = Field(
        description="Pass as `after_id` to get the next page; null on the last page."
    )


# Multi-select filters: a record matches if its value is any of the given ones.
_CHOICE_FILTERS = ("country", "gender", "sleep_quality", "stress_level")
# Inclusive range filters: min_<column> and max_<column>.
_RANGE_FILTERS = ("age", "coffee_intake", "bmi")
# A filter value must be one PostgreSQL `numeric` can hold (at most 131,072 digits
# before the point), or the query fails; 1e1000000 is a 422, not a 500.
_MAX_WHOLE_DIGITS = 131_072
FilterDecimal = Annotated[
    Decimal,
    Field(max_digits=_MAX_WHOLE_DIGITS + MAX_DECIMAL_PLACES, decimal_places=MAX_DECIMAL_PLACES),
]


class RecordQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled filter is an error, not ignored

    country: list[str] = Field(default=[], description="Exact country names, e.g. `USA`.")
    gender: list[Gender] = []
    sleep_quality: list[SleepQuality] = []
    stress_level: list[StressLevel] = []
    min_age: int | None = None
    max_age: int | None = None
    min_coffee_intake: FilterDecimal | None = None
    max_coffee_intake: FilterDecimal | None = None
    min_bmi: FilterDecimal | None = None
    max_bmi: FilterDecimal | None = None
    after_id: int = Field(default=0, ge=0, le=MAX_ID, description="Return IDs above this one.")
    limit: int = Field(default=50, ge=1, le=500)

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> Self:
        for column in _RANGE_FILTERS:
            low, high = getattr(self, f"min_{column}"), getattr(self, f"max_{column}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"min_{column} must not be greater than max_{column}")
        return self


_COLUMNS = sql.SQL(", ").join(map(sql.Identifier, Record.model_fields))


def _build_query(q: RecordQuery) -> tuple[sql.Composed, dict[str, Any]]:
    """Only the filters that were given become conditions, so each query stays simple
    for the planner. Values are always bound as parameters."""
    conditions: list[sql.Composable] = [sql.SQL("id > %(after_id)s")]
    params: dict[str, Any] = {"after_id": q.after_id, "limit": q.limit + 1}
    for column in _CHOICE_FILTERS:
        if values := getattr(q, column):
            conditions.append(
                sql.SQL("{} = any({})").format(sql.Identifier(column), sql.Placeholder(column))
            )
            params[column] = [str(v) for v in values]
    for column in _RANGE_FILTERS:
        for bound, operator in (("min", ">="), ("max", "<=")):
            name = f"{bound}_{column}"
            if (value := getattr(q, name)) is not None:
                conditions.append(
                    sql.SQL("{} {} {}").format(
                        sql.Identifier(column), sql.SQL(operator), sql.Placeholder(name)
                    )
                )
                params[name] = value
    query = sql.SQL(
        "select {columns} from coffee_health where {conditions} order by id limit %(limit)s"
    ).format(columns=_COLUMNS, conditions=sql.SQL(" and ").join(conditions))
    return query, params


@router.get("/records")
def list_records(q: Annotated[RecordQuery, Query()], conn: Conn) -> RecordPage:
    """Records ordered by ID, filtered by every given parameter (AND between parameters,
    OR between the values of one parameter).

    Pagination is by keyset: pass the returned `next_after_id` as `after_id`. Each page
    costs the same however deep it is, because it starts from an index lookup on ID
    instead of skipping rows with OFFSET.
    """
    query, params = _build_query(q)
    rows = conn.execute(query, params).fetchall()  # at most limit + 1 rows
    items = [Record.model_validate(row) for row in rows[: q.limit]]
    has_more = len(rows) > q.limit
    return RecordPage(items=items, next_after_id=items[-1].id if has_more else None)
