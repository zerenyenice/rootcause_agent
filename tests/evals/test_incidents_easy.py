"""Planted effects of the easy archetypes E1 to E3 on a 120-day warehouse."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest

if TYPE_CHECKING:
    from datetime import date

CASE_IDS_E1 = ("E1-dev", "E1-heldout")
CASE_IDS_E2 = ("E2-dev", "E2-heldout")
CASE_IDS_E3 = ("E3-dev", "E3-heldout")
SHIFT_HOURS = 8


def _line_runs(tables: dict[str, pd.DataFrame], line_id: str) -> pd.DataFrame:
    runs = tables["production_runs"]
    return pd.DataFrame(runs[runs["line_id"] == line_id])


def _machine_ids(tables: dict[str, pd.DataFrame], line_id: str) -> list[str]:
    machines = tables["machines"]
    return list(machines.loc[machines["line_id"] == line_id, "machine_id"])


def _date_window(frame: pd.DataFrame, column: str, window: tuple[date, date]) -> pd.DataFrame:
    days = frame[column].dt.normalize()
    inside = (days >= pd.Timestamp(window[0])) & (days <= pd.Timestamp(window[1]))
    return pd.DataFrame(frame[inside])


def _hot_machines(
    tables: dict[str, pd.DataFrame], incident: Any, metrics: Any, threshold: float
) -> list[str]:
    telemetry = tables["machine_telemetry"]
    hot = []
    for machine_id in _machine_ids(tables, incident.segment["line_id"]):
        series = telemetry[telemetry["machine_id"] == machine_id]
        during = metrics.in_window(series, incident.window)["temperature_c"].mean()
        before = metrics.pre_window(series, incident.window)["temperature_c"].mean()
        if during - before >= threshold:
            hot.append(machine_id)
    return hot


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E1)
def test_e1_scrap_rate_triples_inside_the_window(
    case_id: str, incidents120: dict[str, Any], applied120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    runs = _line_runs(applied120[case_id], incident.segment["line_id"])

    ratio = metrics.scrap_rate(metrics.in_window(runs, incident.window)) / metrics.scrap_rate(
        metrics.pre_window(runs, incident.window)
    )

    assert 2.4 <= ratio <= 3.6


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E1)
def test_e1_scrap_rate_outside_the_window_matches_the_baseline(
    case_id: str, incidents120: dict[str, Any], applied120: Any, baseline120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    line_id = incident.segment["line_id"]
    start, end = metrics.span(incident.window)

    def outside(tables: dict[str, pd.DataFrame]) -> float:
        runs = _line_runs(tables, line_id)
        return float(metrics.scrap_rate(runs[(runs["ts"] < start) | (runs["ts"] >= end)]))

    assert outside(applied120[case_id]) == pytest.approx(outside(baseline120), rel=0.05)


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E1)
def test_e1_other_lines_are_unchanged(
    case_id: str, incidents120: dict[str, Any], applied120: Any, baseline120: Any
) -> None:
    line_id = incidents120[case_id].segment["line_id"]
    after = applied120[case_id]["production_runs"]
    before = baseline120["production_runs"]

    pd.testing.assert_frame_equal(
        after[after["line_id"] != line_id].reset_index(drop=True),
        before[before["line_id"] != line_id].reset_index(drop=True),
    )


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E1)
def test_e1_exactly_one_machine_runs_hot(
    case_id: str, incidents120: dict[str, Any], applied120: Any, baseline120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]

    assert len(_hot_machines(applied120[case_id], incident, metrics, 8.0)) == 1
    assert _hot_machines(baseline120, incident, metrics, 8.0) == []


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E1)
def test_e1_downtime_breakdowns_appear_for_the_hot_machine(
    case_id: str, incidents120: dict[str, Any], applied120: Any, baseline120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    machine = _hot_machines(applied120[case_id], incident, metrics, 8.0)[0]

    def breakdowns(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
        events = tables["downtime_events"]
        rows = events[(events["machine_id"] == machine) & (events["reason_code"] == "breakdown")]
        return _date_window(rows, "start_ts", incident.window)

    assert len(breakdowns(applied120[case_id])) > len(breakdowns(baseline120))


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E1)
def test_e1_maintenance_log_records_the_coolant_pump_replacement(
    case_id: str, incidents120: dict[str, Any], applied120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    machine = _hot_machines(applied120[case_id], incident, metrics, 8.0)[0]
    events = applied120[case_id]["maintenance_events"]

    rows = events[
        (events["machine_id"] == machine)
        & (events["kind"] == "unplanned")
        & (events["parts_replaced"] == "coolant pump")
    ]
    performed = rows["performed_date"]

    assert len(rows) == 1
    assert pd.Timestamp(incident.window[0]) <= performed.iloc[0] <= pd.Timestamp(incident.window[1])


def _e2_zero_rows(tables: dict[str, pd.DataFrame], incident: Any) -> pd.DataFrame:
    lines = tables["production_lines"]
    plant_lines = set(lines.loc[lines["plant_id"] == incident.segment["plant_id"], "line_id"])
    runs = tables["production_runs"]
    on_day = _date_window(runs, "ts", incident.window)
    return on_day[on_day["line_id"].isin(plant_lines)]


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E2)
def test_e2_output_is_zero_for_exactly_one_eight_hour_shift(
    case_id: str, incidents120: dict[str, Any], applied120: Any
) -> None:
    incident = incidents120[case_id]
    rows = _e2_zero_rows(applied120[case_id], incident)
    zero = rows[(rows["units_produced"] == 0) & (rows["units_scrapped"] == 0)]
    hours = sorted(zero["ts"].dt.hour.unique())

    assert len(hours) == SHIFT_HOURS
    assert zero["shift"].nunique() == 1
    assert len(zero) == SHIFT_HOURS * rows["line_id"].nunique()
    untouched = rows[~rows.index.isin(zero.index)]
    assert (untouched["units_produced"] > 0).all()


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E2)
def test_e2_other_plant_keeps_normal_output(
    case_id: str, incidents120: dict[str, Any], applied120: Any, baseline120: Any
) -> None:
    incident = incidents120[case_id]
    lines = baseline120["production_lines"]
    other = set(lines.loc[lines["plant_id"] != incident.segment["plant_id"], "line_id"])

    def other_plant(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
        runs = _date_window(tables["production_runs"], "ts", incident.window)
        return runs[runs["line_id"].isin(other)].reset_index(drop=True)

    pd.testing.assert_frame_equal(other_plant(applied120[case_id]), other_plant(baseline120))


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E2)
def test_e2_power_downtime_covers_every_machine_of_the_plant(
    case_id: str, incidents120: dict[str, Any], applied120: Any
) -> None:
    incident = incidents120[case_id]
    tables = applied120[case_id]
    rows = _e2_zero_rows(tables, incident)
    zero = rows[rows["units_produced"] == 0]
    shift_start = zero["ts"].min()
    shift_end = zero["ts"].max() + pd.Timedelta(hours=1)
    lines = tables["production_lines"]
    machines = tables["machines"]
    plant_lines = lines.loc[lines["plant_id"] == incident.segment["plant_id"], "line_id"]
    plant_machines = set(machines.loc[machines["line_id"].isin(plant_lines), "machine_id"])

    events = tables["downtime_events"]
    power = events[events["reason_code"] == "power"]
    for machine_id in plant_machines:
        mine = power[power["machine_id"] == machine_id]
        assert ((mine["start_ts"] <= shift_start) & (mine["end_ts"] >= shift_end)).any(), machine_id


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E2)
def test_e2_business_event_names_the_outage_and_the_plant(
    case_id: str, incidents120: dict[str, Any], applied120: Any
) -> None:
    incident = incidents120[case_id]
    events = applied120[case_id]["business_events"]
    day = pd.Timestamp(incident.window[0])

    rows = events[(events["event_type"] == "outage") & (events["event_date"] == day)]

    assert len(rows) == 1
    assert incident.segment["plant_id"] in rows.iloc[0]["description"]


def _top_family(tables: dict[str, pd.DataFrame]) -> str:
    products = tables["products"]
    top = products.sort_values("complexity_score").iloc[-1]
    return str(top["family_id"])


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E3)
def test_e3_cycle_time_rises_by_thirty_percent_in_the_window(
    case_id: str, incidents120: dict[str, Any], applied120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    runs = _line_runs(applied120[case_id], incident.segment["line_id"])

    during = metrics.in_window(runs, incident.window)["cycle_time_s"].mean()
    before = metrics.pre_window(runs, incident.window)["cycle_time_s"].mean()

    assert 1.2 <= during / before <= 1.4


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E3)
def test_e3_throughput_drops_in_the_window(
    case_id: str, incidents120: dict[str, Any], applied120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    runs = _line_runs(applied120[case_id], incident.segment["line_id"])

    during = metrics.in_window(runs, incident.window)["units_produced"].mean()
    before = metrics.pre_window(runs, incident.window)["units_produced"].mean()

    assert during < 0.85 * before


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E3)
def test_e3_work_orders_switch_to_the_highest_complexity_family(
    case_id: str, incidents120: dict[str, Any], applied120: Any
) -> None:
    incident = incidents120[case_id]
    tables = applied120[case_id]
    family = _top_family(tables)
    products = tables["products"].set_index("product_id")["family_id"]
    start = pd.Timestamp(incident.window[0])
    end = pd.Timestamp(incident.window[1]) + pd.Timedelta(days=1)
    orders = tables["work_orders"]
    overlapping = orders[
        (orders["line_id"] == incident.segment["line_id"])
        & (orders["start_ts"] < end)
        & (orders["end_ts"] >= start)
    ]

    assert not overlapping.empty
    assert set(overlapping["product_id"].map(products)) == {family}


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E3)
def test_e3_production_runs_follow_the_family_switch(
    case_id: str, incidents120: dict[str, Any], applied120: Any, metrics: Any
) -> None:
    incident = incidents120[case_id]
    tables = applied120[case_id]
    products = tables["products"].set_index("product_id")["family_id"]
    runs = metrics.in_window(_line_runs(tables, incident.segment["line_id"]), incident.window)

    assert set(runs["product_id"].map(products)) == {_top_family(tables)}


@pytest.mark.unit
@pytest.mark.parametrize("case_id", CASE_IDS_E3)
def test_e3_other_lines_are_unchanged(
    case_id: str, incidents120: dict[str, Any], applied120: Any, baseline120: Any
) -> None:
    line_id = incidents120[case_id].segment["line_id"]
    after = applied120[case_id]["production_runs"]
    before = baseline120["production_runs"]

    pd.testing.assert_frame_equal(
        after[after["line_id"] != line_id].reset_index(drop=True),
        before[before["line_id"] != line_id].reset_index(drop=True),
    )
