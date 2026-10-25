# Plan: T041 Incident layer and easy cases E1 to E3

Backlog T041 (epic EP4). Branch `feat/eval-incidents-easy` off `dev`. Gate 1 approved 2026-10-25 (batch approval). Depends on T040 (merged).

## Goal
Add the incident layer to `rootcause_evals.manufacturing`: composable incident modules applied on top of the deterministic baseline, a ground-truth manifest (`cases.json`) with the PRD section 11 fields, two instances per archetype (dev and held-out), a visibility assertion, and the three easy archetypes.

## Design
- `incidents/base.py`: `Incident` protocol / frozen dataclass base with `case_id`, `archetype` (E1..D4), `level` (easy|medium|difficult), `split` (dev|heldout), `window: tuple[date, date]`, `segment: Mapping[str, str]` (e.g. line_id, plant_id), `params`; `apply(tables: Mapping[str, DataFrame], rng) -> dict[str, DataFrame]` returns new frames (no mutation of inputs); `manifest_entry() -> CaseManifest`.
- `incidents/manifest.py`: `CaseManifest` frozen dataclass and `write_cases_json(cases, path)`; fields: case_id, archetype, level, split, metric (name, table, expression, grain), anomaly_window, segment_filters, expected_effect (direction, size), chain (ordered list of {cause, evidence_tables, evidence_columns, sql_hint}), root_cause (statement, type), counterfactual (description, sql_hint), decoys (list of {description, why_wrong}), benign_events_in_window, keywords.
- `incidents/registry.py`: `ARCHETYPES` mapping id -> factory; `instantiate(config, seed) -> tuple[Incident, ...]` creating two instances per archetype on different lines/dates (dev: first half of the year, heldout: second half), deterministic from the seed.
- `incidents/visibility.py`: `assert_visible(tables, incident)`: robust z-score (median/MAD, weekday-adjusted) of the affected daily series inside the window versus the 8 weeks before must exceed 3; decoy series must not; returns a `VisibilityReport`; `generate` raises `IncidentNotVisibleError` when it fails.
- `generate(config, incidents=None)` (in `__init__.py`): baseline then incidents in order, then visibility check; `generate_with_manifest(config) -> (tables, cases)`; CLI `generate --incidents all|none|E1,E2` writes `cases.json` beside the data and prints the visibility report; `--no-visibility-check` for debugging.
- Easy archetypes (`incidents/easy.py`):
  - **E1 coolant pump failure**: one line, 5 consecutive days; scrap rate ×3 on that line's runs (units_scrapped resampled with the raised rate); one machine on the line gets temperature +12 C in `machine_telemetry` for the window; `downtime_events` gain breakdown events for that machine on days 3 to 5; `maintenance_events` gains an unplanned row on day 5 with `parts_replaced = "coolant pump"`; root cause type `equipment`; chain: scrap up on line -> only runs on machine M hours affected -> telemetry temperature high -> maintenance log coolant pump failure. No decoy.
  - **E2 power outage**: one plant, one full shift (8 hours): `units_produced = 0`, `units_scrapped = 0`, cycle_time null or unchanged for all lines of the plant; `downtime_events` reason `power` for every machine of the plant covering the shift; `business_events` row type `outage` "Plant P power outage"; root cause type `external`; no decoy.
  - **E3 product mix change**: one line, 14 days; `work_orders` for the line switched to the highest-complexity family; `production_runs.product_id` follows; `cycle_time_s` ×1.3 and `units_produced` ÷1.3 accordingly; root cause type `process` (planned mix change, business event optional: "Line L reassigned to family F"); no decoy; the chain ends at the product-mix decision.
- Immutability: every `apply` builds new frames with `assign`/`mask`/`concat`; the baseline frames are never modified in place.
- Determinism: incidents draw from a child rng (`rng.spawn` or `default_rng(seed + offset)`), so adding an incident does not change other tables' baseline values.

## Tests (tester-owned, RED first)
Registry instantiates 6 incidents (3 archetypes × 2 splits) with non-overlapping windows and distinct lines/plants; `apply` does not mutate inputs; manifest entries carry every PRD field and `cases.json` round-trips; per archetype on a 120-day config: the observable metric moves as specified in the window and not outside (scrap rate ×3 within 20%, zero output for exactly the shift hours, cycle time ×1.3 within 10%); evidence rows exist (telemetry delta, downtime, maintenance row, business event, work-order family switch); visibility report passes for each incident and `assert_visible` fails on an unmodified baseline; CLI writes `cases.json`; coverage >= 80%.

## Acceptance (backlog)
Generator asserts each planted anomaly has robust z-score above 3; manifest entries contain all PRD fields.
