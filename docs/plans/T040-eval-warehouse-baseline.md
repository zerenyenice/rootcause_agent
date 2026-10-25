# Plan: T040 Manufacturing warehouse generator — baseline layer

Backlog T040 (epic EP4). Branch `feat/eval-warehouse-baseline` off `dev`. Gate 1 approved 2026-10-25 (batch approval). Incidents (T041–T043), benign events (T044) are later tasks; this task builds the deterministic baseline only.

## Goal
`rootcause_evals.manufacturing` generates the synthetic warehouse from PRD section 11: dimensions and one year of hourly baseline data with realistic structure, written to DuckDB, CSV and a Postgres SQL dump, plus a data dictionary.

## Design
- `config.py`: `GeneratorConfig` frozen dataclass: `seed=42`, `start="2025-10-01"`, `days=365`, `plants=2`, `lines_per_plant=4`, `machines_per_line=5`, `products=30`, `families=5`, `suppliers=12`, `materials=20`, `operators=200`, `scale=1` (10 → 10-minute grain, 5 plants in T047).
- `dimensions.py`: builders for plants, production_lines, machines, product_families, products (complexity score, standard cycle time), suppliers, materials, material_lots (received date, spec measurement, certificate flag), operators (hire date, training certified date), shift_assignments (daily, 3 shifts).
- `baseline.py`: `production_runs` hourly per line (shift, product from work order, units produced from standard cycle time × availability, units scrapped with base scrap rate × night-shift penalty, cycle time, material lot), `work_orders`, `quality_inspections` (per run, gauge id, measured value around spec, pass flag, defect type, source system "MES"), `machine_telemetry` hourly (temperature, vibration, pressure, power kWh correlated with output), `downtime_events` (reason codes: breakdown, changeover, minor_stop, power, planned), `maintenance_events` (monthly planned per machine, occasional unplanned), `gauge_calibrations` (quarterly, offset found), `energy_meter_readings` hourly per line, `warranty_claims` (small base rate, 30–90 day lag), `business_events` (holidays, planned maintenance windows). Seasonality: weekday factor (weekends 0.6), monthly planned maintenance dip, slow +4%/year trend, Gaussian noise. Vectorised with numpy and pandas; all randomness from `numpy.random.default_rng(seed)`.
- `writers.py`: `write_csv(tables, out_dir)`, `write_duckdb(tables, path)`, `write_postgres_sql(tables, path)` (CREATE TABLE + COPY FROM STDIN blocks); `write_data_dictionary(out_dir)` from a `TABLE_DOCS` mapping (column descriptions used later to test catalog enrichment).
- `cli.py`: `python -m rootcause_evals.manufacturing generate --out <dir> [--seed N] [--days N] [--scale N]` printing row counts and elapsed time.
- `__init__.py` exports `generate(config) -> dict[str, pandas.DataFrame]`.
- Dependencies (packages/evals/pyproject.toml): `numpy>=2`, `pandas>=2.2`, `duckdb>=1.1`, `pyarrow>=17`.

## Tests (tester-owned, RED first)
Determinism (same seed → identical frames, different seed → different); row counts for a 30-day config scale to the PRD table within 10% at 365 days (assert the per-day rates); schema: every table has the PRD key columns and non-null keys; referential integrity (runs→lines, runs→work_orders, inspections→runs, telemetry→machines, claims→runs); weekend output lower than weekday output; night shift scrap rate higher than day shift; warranty claim dates after their run timestamps; writers produce files and DuckDB tables with matching row counts; CLI runs on a 7-day config; a 365-day base generation finishes under 120 s (marked `integration`). Coverage >= 80%.

## Acceptance (backlog)
Base scale under 2 minutes; row counts within 10% of PRD; data dictionary generated; DuckDB, Postgres dump, CSV outputs.
