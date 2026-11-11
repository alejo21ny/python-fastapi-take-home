# Notes

Design decisions for the Medallo take-home: schema, loader, API, scale and tests.

## Quick start

```bash
make setup                          # dependencies and .env
make db-reset && make load && make dev
make test                           # needs the database; make test-unit does not
```

Then open <http://localhost:8000/docs> and try `GET /records`.

## Local environment

- Postgres 17 runs in the Compose project `medallo-coffee-health` (volume
  `medallo-coffee-health_pgdata`), published on **127.0.0.1:5434** only. The fixed
  project name and loopback port keep it apart from other local stacks.
- `coffee` is the development database. `coffee_test` is used only by integration
  tests (`TEST_DATABASE_URL`).
- `make test-unit` needs no database. `make test-integration` and `make test` need
  `make db-up` and **fail** (never skip) when the database is unreachable.

### Test database

In `tests/conftest.py`:

1. For the whole pytest session, the app's settings point at `TEST_DATABASE_URL`, so
   no `TestClient` can reach the development database `coffee`.
2. Integration tests refuse to run unless that URL is exactly
   `127.0.0.1:5434/coffee_test`.
3. `coffee_test` is created if missing, and its `public` schema is dropped and rebuilt
   from `db/init/*.sql` in one transaction.

This works the same locally and in CI, where only an empty Postgres server is needed.
Schema-constraint tests run inside a transaction that is always rolled back. Loader
and API tests commit, as the real code does, and empty `coffee_health` before and
after.

## Schema

`db/init/002_coffee_health.sql` adds `coffee_health`; the sample `notes` table from
`001_notes.sql` is kept.

- `id integer primary key`, one row per person.
- Every column is `NOT NULL`: incomplete records are rejected, not stored with gaps.
- Categories are `text` with `CHECK (... in (...))` instead of `ENUM` types, which are
  harder to change.
- Decimal columns are unconstrained `numeric` (see the decimal policy below).
- Python validates and normalizes source text; `CHECK` constraints enforce the
  invariants of stored data (ranges, categories, the shape of a country name), so
  another client cannot store an impossible record. The database does not repeat
  input clean-up rules such as trimming, re-casing or rejecting placeholder words like
  `unknown`. `tests/test_schema_parity.py` compares ranges and categories in the SQL
  text with `app/domain.py`, and `tests/test_schema_constraints.py` checks boundary
  values and country names against the real database.
- Indexes on the selective filter columns; see [API](#api) for how they were chosen.

## Validation and normalization

`app/validation.py` is pure (no I/O): `validate_row` returns either `Accepted` (record
plus a list of fixes) or `Rejected` (every invalid field with its reason).

**Fixed, because the intent is unambiguous** (each fix is reported):

- Surrounding whitespace, including tabs: `"\tUSA "` → `USA`.
- Letter case of categories: `MALE` → `Male`, `GOOD` → `Good`.
- Known countries, matched case-insensitively against a canonical list. Acronyms keep
  their case: `usa` → `USA`. Inner whitespace runs are collapsed.
- Boolean words: `y/yes/true` → true, `n/no/false` → false, besides `0/1`.

**Rejected, because fixing them would be a guess:**

- Missing values and placeholders: `""`, `?`, `N/A`, `unknown`.
- Numbers written as words or with typos: `thirty`, `4O` (letter O), `seven`.
- Out-of-range values, and booleans other than the above (`-1`, `2`).

**Countries outside the known list** are accepted as written if they look like a name
(letters joined by spaces, hyphens, apostrophes or dots, at most 100 characters), e.g.
`Côte d'Ivoire`. They are not re-cased, and synonyms are not merged (`United States`
stays distinct from `USA`), because doing so reliably would need a reference list.

### Plausibility limits (inclusive)

| Field | Range | Reason |
| --- | --- | --- |
| Age | 18–110 | Adults only, per the exercise; 110 covers the oldest living people |
| Coffee_Intake | 0–20 cups/day | Well above heavy consumption, below the absurd |
| Caffeine_mg | 0–2000 mg/day | About 5× the commonly cited 400 mg safe daily amount |
| Sleep_Hours | 0–24 h/night | Physical limit of a day |
| BMI | 10–80 | Covers severe underweight to extreme obesity; rejects `0` |
| Heart_Rate | 25–220 bpm | Very low resting rates of endurance athletes up to the usual maximum |
| Physical_Activity_Hours | 0–168 h/week | Physical limit of a week |

Integers (`ID`, `Age`, `Heart_Rate`) must be written as integers; `40.0` is rejected.
`ID` must fit Postgres `integer` (1–2,147,483,647).

### Decimal policy

- Values are parsed from text straight into `Decimal`, never through `float`.
- They are stored in unconstrained `numeric`, so nothing is rounded and extra precision
  is kept (`0.123456789` round-trips exactly). A value just above a limit, such as
  `20.0001`, is rejected rather than rounded into range.
- Only plain decimal notation is accepted. Exponents (`1e1`), digit separators (`1_0`),
  comma decimals (`3,5`), `NaN`/`Infinity` and non-ASCII digits are rejected as
  malformed.
- `-0` is normalized to `0`, because Postgres `numeric` has no negative zero.
- More than 1,000 digits after the decimal point is rejected. Postgres `numeric`
  stores up to 16,383, but an indexed value must also fit a B-tree entry (2,704
  bytes). Measured on PostgreSQL 17, that limit falls between 5,300 and 5,400 varied
  digits. Accepting such a value would make the load fail as a whole.

## Loader

`make load` runs `python -m scripts.load_data data/coffee_health_raw.csv`. (The module
form is needed so the script can import `app`.)

1. **Stream and classify.** The file is read line by line as `utf-8-sig`, which drops
   the byte-order mark. Each data line is blank, malformed (wrong number of fields, or
   unreadable by the `csv` module), rejected by `app/validation.py`, or valid. A wrong
   header stops the load before anything is written.
2. **Stage.** Valid records are sent with `COPY` into a temporary table, together
   with their line number. Python keeps only counters, so memory does not grow with
   the file.
3. **Reconcile in PostgreSQL.** IDs that appear on more than one line are grouped:
   - If every copy has the same values (compared after normalization, so `3.5` equals
     `3.50`), the first line is loaded and the others count as **duplicates**.
   - If the values differ, **no line for that ID is loaded**: choosing one would be a
     guess. A row already stored for that ID is left untouched.
4. **Upsert.** Rows whose stored values differ are updated, new IDs are inserted, and
   identical rows are left alone. IDs that are stored but missing from the file are not
   deleted: the loader adds and corrects records, it does not mirror the file.

Everything runs in one transaction. A failed run, for example on bytes that are not
UTF-8, changes nothing. A second run of the same file inserts and updates nothing.

Every line that is not loaded as its own row is printed to stderr with its line number
and reason. The summary goes to stdout.

### Result on the provided dataset

| Lines | Count | Detail |
| --- | ---: | --- |
| Read | 10,015 | data lines after the header |
| Blank | 3 | |
| Malformed | 3 | lines 2659 (17 fields), 4869 and 7175 (13 fields) |
| Rejected | 22 | see below |
| Duplicate | 8 | identical copies of an earlier line |
| Conflicting | 8 | IDs 3338, 6115, 6748, 7494, two different versions each |
| **Loaded** | **9,971** | 56 valid lines needed fixes |

The 44 lines that were not loaded are 3 blank, 3 malformed, 22 rejected, 8
duplicates and 8 conflicting. The 22 rejected lines:

- 8 out of range: Age `212`, `150`, `-3`; Sleep_Hours `-1`, `25.0`; BMI `0`;
  Heart_Rate `0`, `350`.
- 5 missing: Age, Sleep_Hours, Gender, BMI, Heart_Rate.
- 5 not a number: Age `thirty`, `4O`, `unknown`; Sleep_Hours `seven`; BMI `n/a`.
- 4 invalid values: Gender `?`, `N/A`; Smoking `-1`, `2`.

`make load` prints each of the 44 with its line number and reason. A second run
reports `inserted 0, updated 0, unchanged 9971`.

### Known limits

- Line numbers are physical lines. For a quoted field spanning several lines, the
  number is the record's last line. The dataset has none.
- Two loads running at the same time do not corrupt data (`on conflict do nothing`),
  but their summaries may overlap. Serialize loads if that matters.
- Rows are parsed and validated one by one in Python. That step alone measured about
  25,000 lines per second here, about 7 minutes for 10 million lines. This is not
  the total load time: `COPY`, reconciliation and upsert in PostgreSQL come on top and
  were not measured at that scale. To go faster, validate chunks of the file in
  parallel, all feeding the same staging table.

## API

`GET /records` returns records ordered by ID, one page at a time:

```
GET /records?country=USA&country=Japan&stress_level=Low&min_age=30&max_age=40&limit=50
-> {"items": [...], "next_after_id": 1234}
GET /records?...same filters...&after_id=1234
```

- **Filters.** `country`, `gender`, `sleep_quality` and `stress_level` are
  multi-select: repeat the parameter, and a record matches any of the values.
  `min_`/`max_` pairs on `age`, `coffee_intake` and `bmi` are inclusive ranges.
  Different parameters combine with AND. Values match exactly as stored, so use
  `USA`, not `usa`.
- **Validation (422).** Unknown categories, a minimum above its maximum, `limit`
  outside 1–500 and unknown parameters are rejected. Rejecting unknown parameters
  means a misspelled filter is an error instead of silently returning everything.
  Decimal bounds that Postgres `numeric` cannot hold, such as `1e1000000`, are
  rejected too, instead of failing in the database.
- **Pagination by keyset.** Each page is `id > after_id order by id limit n`, an index
  range scan, so page 50,000 costs the same as page 1. `OFFSET` would read and discard
  every earlier row. There is no total count: `count(*)` over a filter scans every
  match, which is the slow part on a large table.
- **SQL.** Only the filters that were given become conditions, and every value is
  bound as a parameter.
- **Connections.** One pooled connection per request (psycopg pool, max 10). Each
  request is a single short query, so the pool size, not the query, limits
  concurrency.
- **Decimals are exact JSON strings**, such as `"coffee_intake": "3.5"`, and are
  documented in OpenAPI as `string` with `format: decimal`. A JSON number would be
  parsed as a float by most clients, which rounds values with more than about 17
  significant digits and turns `1E-1000` into `0`. Integers (`id`, `age`,
  `heart_rate`) stay JSON numbers. Very small values come out in exponent notation
  (`1E-7`), which is still exact; the dataset has at most one decimal place.

### Indexes

Measured with `EXPLAIN ANALYZE` on 1 million synthetic rows (PostgreSQL 17):

| Query | Primary key only | With filter indexes |
| --- | ---: | ---: |
| First page, no filter | 0.2 ms | same plan |
| Page near the end with keyset (with `OFFSET`) | 0.2 ms (467 ms) | same plan |
| One country | 0.4 ms | 0.3 ms, primary key still chosen |
| Two countries + stress + age range | 8.4 ms | 6.7 ms, primary key still chosen |
| Rare combination: USA, age ≥ 105, BMI ≥ 35 | 275 ms | 31 ms |
| No match: unknown country | 96 ms | 0.25 ms |
| Insert 100,000 rows | 2.7 s | 6.3 s |

Walking the primary key in ID order and filtering is fastest when matches are
common: the first 50 turn up quickly. It degrades when matches are rare, because it
reads most of the table. So the schema indexes the selective filter columns:
`(country, id)`, `age`, `coffee_intake` and `bmi`. The planner combines them
(bitmap AND) for rare combinations. The 3- and 4-value categories are not indexed,
because they never narrow the search enough to beat the primary key.

The cost is on writes: inserts are about 2.3 times slower, plus about 60 MB of
indexes per million rows. For a one-off bulk load of tens of millions of rows,
dropping the indexes and recreating them afterwards would be faster.

**Applying the indexes.** Postgres runs `db/init/*.sql` only when it creates a new,
empty data volume, so new databases get the indexes then. An existing database does
not: run the four `create index` statements from `db/init/002_coffee_health.sql`
against it. On a large table in use, `create index concurrently` avoids blocking
writes. Re-running the whole file fails at `create table`. Never use `make db-reset`
to add indexes to a database whose data must be kept: it deletes the volume.

## Tests

| Command | Database | Covers |
| --- | --- | --- |
| `make test-unit` | none | validation rules with real values from the CSV, CSV parsing, schema parity (SQL text against `app/domain.py`), `/records` parameters and SQL |
| `make test-integration` | `coffee_test` | `CHECK` constraints, the loader (duplicates, conflicts, idempotency, rollback on failure, the full dataset), `/health`, `/notes` and `/records` through `TestClient` |
| `make test` | `coffee_test` | both |

`.github/workflows/ci.yml` runs `make lint`, `make test-unit` and `make test-integration`
(together, the whole suite) against a PostgreSQL 17 service on port 5434, and fails if
any test is skipped. It then applies `db/init` to the empty `coffee` database and runs
`make load` twice: the first run must insert 9,971 rows and the second must change
nothing.
