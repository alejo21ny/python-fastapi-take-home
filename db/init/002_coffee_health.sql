-- Validated coffee health records, one row per person.
--
-- The CHECK constraints enforce the invariants of stored data from app/domain.py
-- (LIMITS, the StrEnum categories, the shape of a country name), so no other client
-- can store an impossible record. Cleaning source text (whitespace, letter case,
-- placeholders such as "unknown") is the loader's job and is not repeated here.
-- tests/test_schema_parity.py fails if limits or categories drift apart.
--
-- Decimal columns are unconstrained `numeric`: values are stored exactly as validated,
-- with no rounding to a fixed scale. Categories are text + CHECK rather than ENUM
-- types, which are harder to change later.
-- Indexes for API filters are added together with the endpoints that need them.
create table public.coffee_health (
  id integer primary key check (id >= 1),
  age smallint not null check (age between 18 and 110),
  gender text not null check (gender in ('Male', 'Female', 'Other')),
  -- Same shape as app/validation.py accepts: letters joined by single spaces, hyphens,
  -- apostrophes or abbreviation dots, at most 100 characters. Rejects "?", "123", "N/A".
  country text not null check (
    country ~ '^[[:alpha:]]+(([ ''’-]|\. ?)[[:alpha:]]+)*\.?$' and char_length(country) <= 100
  ),
  coffee_intake numeric not null check (coffee_intake between 0 and 20),
  caffeine_mg numeric not null check (caffeine_mg between 0 and 2000),
  sleep_hours numeric not null check (sleep_hours between 0 and 24),
  sleep_quality text not null check (sleep_quality in ('Poor', 'Fair', 'Good', 'Excellent')),
  bmi numeric not null check (bmi between 10 and 80),
  heart_rate smallint not null check (heart_rate between 25 and 220),
  stress_level text not null check (stress_level in ('Low', 'Medium', 'High')),
  physical_activity_hours numeric not null check (physical_activity_hours between 0 and 168),
  health_issues text not null check (health_issues in ('None', 'Mild', 'Moderate', 'Severe')),
  occupation text not null
    check (occupation in ('Office', 'Healthcare', 'Service', 'Student', 'Other')),
  smoking boolean not null,
  alcohol_consumption boolean not null
);
