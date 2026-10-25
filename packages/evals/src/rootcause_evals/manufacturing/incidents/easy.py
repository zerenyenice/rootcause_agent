"""Easy archetypes E1 (coolant pump failure), E2 (power outage), E3 (product mix change)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.baseline import scrap_rate
from rootcause_evals.manufacturing.incidents.base import (
    PRE_WINDOW_DAYS,
    Incident,
    Tables,
    append_rows,
    day_of,
    in_span,
    next_ids,
    window_bounds,
)
from rootcause_evals.manufacturing.incidents.manifest import (
    CaseManifest,
    ChainStep,
    Counterfactual,
    ExpectedEffect,
    MetricSpec,
    RootCause,
)
from rootcause_evals.manufacturing.seasonality import (
    DAY_SHIFT_START_HOUR,
    EVENING_SHIFT_START_HOUR,
    NIGHT_SHIFT_START_HOUR,
    SHIFT_NAMES,
)

SCRAP_MULTIPLIER = 3.0
HOT_MACHINE_DELTA_C = 12.0
BREAKDOWN_DAY_OFFSETS = (2, 3, 4)  # days 3 to 5 of the window
BREAKDOWNS_PER_DAY = 2
BREAKDOWN_MINUTES = (60.0, 180.0)
PUMP_PART = "coolant pump"
SHIFT_HOURS: dict[str, tuple[int, int]] = {
    "day": (DAY_SHIFT_START_HOUR, EVENING_SHIFT_START_HOUR),
    "evening": (EVENING_SHIFT_START_HOUR, NIGHT_SHIFT_START_HOUR),
}
OUTAGE_EVENT = "outage"


@dataclass(frozen=True)
class CoolantPumpFailure(Incident):
    """E1: scrap rate triples on one line for five days; one machine overheats."""

    archetype: ClassVar[str] = "E1"
    level: ClassVar[str] = "easy"
    direction: ClassVar[str] = "up"

    def apply(self, tables: Tables, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
        """Raise scrap on the line, heat one machine, add breakdowns and the pump repair."""
        start, end = window_bounds(self.window)
        machine = str(self.params["machine_id"])
        return {
            **tables,
            "production_runs": self._raise_scrap(tables, rng, start, end),
            "machine_telemetry": self._heat(tables["machine_telemetry"], machine, start, end),
            "downtime_events": self._breakdowns(tables["downtime_events"], machine, rng, start),
            "maintenance_events": self._pump_replacement(tables["maintenance_events"], machine),
        }

    def _raise_scrap(
        self, tables: Tables, rng: np.random.Generator, start: pd.Timestamp, end: pd.Timestamp
    ) -> pd.DataFrame:
        runs = tables["production_runs"]
        mask = (runs["line_id"] == self.segment["line_id"]) & in_span(runs["ts"], start, end)
        complexity = runs["product_id"].map(
            tables["products"].set_index("product_id")["complexity_score"]
        )
        shift_idx = pd.Index(SHIFT_NAMES).get_indexer(pd.Index(runs["shift"]))
        rate = scrap_rate(shift_idx, complexity.to_numpy(dtype=float), SCRAP_MULTIPLIER)
        hit = mask.to_numpy()
        scrapped = runs["units_scrapped"].to_numpy().copy()
        scrapped[hit] = rng.binomial(runs["units_produced"].to_numpy()[hit], rate[hit])
        return runs.assign(units_scrapped=scrapped)

    @staticmethod
    def _heat(
        telemetry: pd.DataFrame, machine: str, start: pd.Timestamp, end: pd.Timestamp
    ) -> pd.DataFrame:
        hit = (
            (telemetry["machine_id"] == machine) & in_span(telemetry["ts"], start, end)
        ).to_numpy()
        heated = telemetry["temperature_c"].to_numpy() + HOT_MACHINE_DELTA_C * hit
        return telemetry.assign(temperature_c=np.round(heated, 2))

    @staticmethod
    def _breakdowns(
        downtime: pd.DataFrame, machine: str, rng: np.random.Generator, start: pd.Timestamp
    ) -> pd.DataFrame:
        count = len(BREAKDOWN_DAY_OFFSETS) * BREAKDOWNS_PER_DAY
        days = np.repeat(BREAKDOWN_DAY_OFFSETS, BREAKDOWNS_PER_DAY)
        hours = rng.integers(0, 24, size=count)
        duration = np.round(rng.uniform(*BREAKDOWN_MINUTES, size=count), 1)
        begin = start + pd.to_timedelta(days, unit="D") + pd.to_timedelta(hours, unit="h")
        return append_rows(
            downtime,
            {
                "event_id": next_ids(downtime, "event_id", count),
                "machine_id": [machine] * count,
                "start_ts": begin,
                "end_ts": begin + pd.to_timedelta(duration, unit="min"),
                "reason_code": ["breakdown"] * count,
                "duration_min": duration,
            },
        )

    def _pump_replacement(self, maintenance: pd.DataFrame, machine: str) -> pd.DataFrame:
        day = pd.Timestamp(self.window[1])
        return append_rows(
            maintenance,
            {
                "event_id": next_ids(maintenance, "event_id", 1),
                "machine_id": [machine],
                "kind": ["unplanned"],
                "scheduled_date": [day],
                "performed_date": [day],
                "parts_replaced": [PUMP_PART],
            },
        )

    def daily_metric(self, tables: Tables) -> pd.Series[float]:
        """Daily scrap rate of the affected line."""
        runs = tables["production_runs"]
        line = runs[runs["line_id"] == self.segment["line_id"]]
        totals = line.groupby(day_of(line, "ts"))[["units_scrapped", "units_produced"]].sum()
        return pd.Series(totals["units_scrapped"] / totals["units_produced"].where(lambda s: s > 0))

    def manifest_entry(self, tables: Tables | None = None) -> CaseManifest:
        """Ground truth for the coolant pump failure."""
        line, machine = self.segment["line_id"], self.params["machine_id"]
        first, last = (d.isoformat() for d in self.window)
        chain = (
            ChainStep(
                f"Scrap rate on line {line} triples during the window",
                ("production_runs",),
                ("line_id", "ts", "units_produced", "units_scrapped"),
                f"SELECT CAST(ts AS DATE), SUM(units_scrapped) * 1.0 / SUM(units_produced) "
                f"FROM production_runs WHERE line_id = '{line}' GROUP BY 1",
            ),
            ChainStep(
                f"Machine {machine} runs hot while the rest of line {line} is normal",
                ("machine_telemetry", "machines"),
                ("machine_id", "line_id", "ts", "temperature_c"),
                f"SELECT CAST(t.ts AS DATE), t.machine_id, AVG(t.temperature_c) "
                f"FROM machine_telemetry t JOIN machines m USING (machine_id) "
                f"WHERE m.line_id = '{line}' GROUP BY 1, 2",
            ),
            ChainStep(
                f"Machine {machine} has extra breakdown downtime on the last days of the window",
                ("downtime_events", "machines"),
                ("machine_id", "line_id", "start_ts", "end_ts", "reason_code"),
                f"SELECT d.* FROM downtime_events d JOIN machines m USING (machine_id) "
                f"WHERE m.line_id = '{line}' AND d.machine_id = '{machine}' "
                f"AND d.reason_code = 'breakdown'",
            ),
            ChainStep(
                "The maintenance log records an unplanned coolant pump replacement",
                ("maintenance_events", "machines"),
                ("machine_id", "line_id", "kind", "performed_date", "parts_replaced"),
                f"SELECT e.* FROM maintenance_events e JOIN machines m USING (machine_id) "
                f"WHERE m.line_id = '{line}' AND e.machine_id = '{machine}' "
                f"AND e.parts_replaced = '{PUMP_PART}'",
            ),
        )
        return self.build_manifest(
            metric=MetricSpec(
                "scrap_rate",
                "production_runs",
                "SUM(units_scrapped) * 1.0 / SUM(units_produced)",
                "line_id x day",
            ),
            effect=ExpectedEffect("up", SCRAP_MULTIPLIER - 1.0),
            chain=chain,
            root_cause=RootCause(
                f"Coolant pump failure on machine {machine} overheated it and raised scrap "
                f"on line {line} until the pump was replaced",
                "equipment",
            ),
            counterfactual=Counterfactual(
                f"Replacing the coolant pump of {machine} before the window would keep the "
                f"scrap rate of line {line} inside the window at its level outside it",
                f"SELECT CAST(ts AS DATE) BETWEEN DATE '{first}' AND DATE '{last}' AS in_window, "
                f"SUM(units_scrapped) * 1.0 / SUM(units_produced) FROM production_runs "
                f"WHERE line_id = '{line}' GROUP BY 1",
            ),
            keywords=(
                "scrap",
                "coolant pump",
                "temperature_c",
                "maintenance_events",
                line,
                machine,
            ),
        )


@dataclass(frozen=True)
class PowerOutage(Incident):
    """E2: one plant loses all output for one full shift."""

    archetype: ClassVar[str] = "E2"
    level: ClassVar[str] = "easy"
    direction: ClassVar[str] = "down"
    weekday_adjusted: ClassVar[bool] = True

    def _shift_span(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        first, last = SHIFT_HOURS[str(self.params["shift"])]
        day = pd.Timestamp(self.window[0])
        return day + pd.Timedelta(hours=first), day + pd.Timedelta(hours=last)

    def _plant_lines(self, tables: Tables) -> list[str]:
        lines = tables["production_lines"]
        return [str(v) for v in lines.loc[lines["plant_id"] == self.segment["plant_id"], "line_id"]]

    def apply(self, tables: Tables, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
        """Zero the plant's output for the shift and record the outage in the logs."""
        begin, finish = self._shift_span()
        lines = self._plant_lines(tables)
        machines = tables["machines"]
        plant_machines = [
            str(v) for v in machines.loc[machines["line_id"].isin(lines), "machine_id"]
        ]
        runs, telemetry, energy = (
            tables[name]
            for name in ("production_runs", "machine_telemetry", "energy_meter_readings")
        )
        run_hit = (runs["line_id"].isin(lines) & in_span(runs["ts"], begin, finish)).to_numpy()
        tel_hit = (
            telemetry["machine_id"].isin(plant_machines) & in_span(telemetry["ts"], begin, finish)
        ).to_numpy()
        energy_hit = (
            energy["line_id"].isin(lines) & in_span(energy["ts"], begin, finish)
        ).to_numpy()
        return {
            **tables,
            "production_runs": runs.assign(
                units_produced=np.where(run_hit, 0, runs["units_produced"]),
                units_scrapped=np.where(run_hit, 0, runs["units_scrapped"]),
                cycle_time_s=runs["cycle_time_s"].mask(run_hit),
            ),
            "machine_telemetry": telemetry.assign(
                power_kwh=telemetry["power_kwh"].mask(tel_hit, 0.0),
                vibration_mm_s=telemetry["vibration_mm_s"].mask(tel_hit, 0.0),
            ),
            "energy_meter_readings": energy.assign(kwh=energy["kwh"].mask(energy_hit, 0.0)),
            "downtime_events": self._power_downtime(
                tables["downtime_events"], plant_machines, begin, finish
            ),
            "business_events": self._outage_event(tables["business_events"]),
        }

    @staticmethod
    def _power_downtime(
        downtime: pd.DataFrame, machines: list[str], begin: pd.Timestamp, finish: pd.Timestamp
    ) -> pd.DataFrame:
        count = len(machines)
        ids = next_ids(downtime, "event_id", count)
        overlapping = (
            downtime["machine_id"].isin(machines)
            & (downtime["start_ts"] < finish)
            & (downtime["end_ts"] > begin)
        )  # baseline events inside the outage would double count; the outage supersedes them
        return append_rows(
            downtime[~overlapping],
            {
                "event_id": ids,
                "machine_id": machines,
                "start_ts": [begin] * count,
                "end_ts": [finish] * count,
                "reason_code": ["power"] * count,
                "duration_min": [(finish - begin).total_seconds() / 60.0] * count,
            },
        )

    def _outage_event(self, events: pd.DataFrame) -> pd.DataFrame:
        plant = self.segment["plant_id"]
        return append_rows(
            events,
            {
                "event_id": next_ids(events, "event_id", 1),
                "event_date": [pd.Timestamp(self.window[0])],
                "event_type": [OUTAGE_EVENT],
                "description": [f"Plant {plant} power outage"],
                "plant_id": [plant],
            },
        )

    def daily_metric(self, tables: Tables) -> pd.Series[float]:
        """Units the plant produced in the outage shift, per day (plant x shift x day)."""
        runs = tables["production_runs"]
        plant = runs[
            runs["line_id"].isin(self._plant_lines(tables))
            & (runs["shift"] == self.params["shift"])
        ]
        return pd.Series(plant.groupby(day_of(plant, "ts"))["units_produced"].sum(), dtype=float)

    def manifest_entry(self, tables: Tables | None = None) -> CaseManifest:
        """Ground truth for the power outage."""
        plant, shift = self.segment["plant_id"], self.params["shift"]
        day = self.window[0].isoformat()
        chain = (
            ChainStep(
                f"Output of plant {plant} is zero for the {shift} shift on {day}",
                ("production_runs", "production_lines"),
                ("line_id", "plant_id", "ts", "shift", "units_produced"),
                f"SELECT r.ts, SUM(r.units_produced) FROM production_runs r JOIN "
                f"production_lines l USING (line_id) WHERE l.plant_id = '{plant}' "
                f"AND CAST(r.ts AS DATE) = DATE '{day}' GROUP BY 1 ORDER BY 1",
            ),
            ChainStep(
                f"Every machine of plant {plant} has a power downtime event covering the shift",
                ("downtime_events", "machines", "production_lines"),
                ("machine_id", "line_id", "plant_id", "start_ts", "end_ts", "reason_code"),
                "SELECT d.machine_id, d.start_ts, d.end_ts FROM downtime_events d "
                "JOIN machines m USING (machine_id) JOIN production_lines l USING (line_id) "
                f"WHERE l.plant_id = '{plant}' AND d.reason_code = 'power' "
                f"AND CAST(d.start_ts AS DATE) = DATE '{day}'",
            ),
            ChainStep(
                f"A business event records the power outage at plant {plant}",
                ("business_events",),
                ("event_date", "event_type", "description", "plant_id"),
                f"SELECT * FROM business_events WHERE event_type = '{OUTAGE_EVENT}' "
                f"AND plant_id = '{plant}'",
            ),
        )
        return self.build_manifest(
            metric=MetricSpec(
                "plant_shift_units_produced",
                "production_runs",
                "SUM(units_produced)",
                "plant x shift x day",
            ),
            effect=ExpectedEffect("down", 1.0),
            chain=chain,
            root_cause=RootCause(
                f"A power outage at plant {plant} stopped every machine for the {shift} shift",
                "external",
            ),
            counterfactual=Counterfactual(
                f"With grid power available, plant {plant} would have produced in the {shift} "
                f"shift what it usually produces on the same weekday: compare the outage day "
                f"with the preceding {PRE_WINDOW_DAYS} days",
                f"SELECT CAST(r.ts AS DATE) AS day, SUM(r.units_produced) FROM production_runs r "
                f"JOIN production_lines l USING (line_id) WHERE l.plant_id = '{plant}' "
                f"AND r.shift = '{shift}' AND dayofweek(r.ts) = dayofweek(DATE '{day}') "
                f"AND CAST(r.ts AS DATE) BETWEEN DATE '{day}' - INTERVAL {PRE_WINDOW_DAYS} DAY "
                f"AND DATE '{day}' GROUP BY 1 ORDER BY 1",
            ),
            keywords=("power", "outage", "downtime_events", "business_events", plant, shift),
        )
