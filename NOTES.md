# Notes

Design decisions for the Medallo take-home. This file grows with each increment.

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
Tests that write data run inside a transaction that is always rolled back.

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
- Indexes will be added with the API filters that use them.

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

### Result on the provided dataset

Validating every well-formed row of `data/coffee_health_raw.csv` gives 9,987 accepted
(56 of them with fixes) and 22 rejected rows. The 9,987 accepted rows hold 9,975
distinct IDs. Blank and malformed lines, duplicates and conflicting IDs are handled by
the loader (next increment).
