"""Column-level documentation of every warehouse table (used for the data dictionary)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class TableDoc:
    """Description of a table and of each of its columns."""

    description: str
    columns: Mapping[str, str]


def _doc(table_description: str, /, **columns: str) -> TableDoc:
    return TableDoc(table_description, MappingProxyType(dict(columns)))


TABLE_DOCS: Mapping[str, TableDoc] = MappingProxyType(
    {
        "plants": _doc(
            "Manufacturing sites.",
            plant_id="Plant identifier (P01, P02, ...).",
            plant_name="Human-readable plant name.",
            city="City where the plant is located.",
            timezone="IANA time zone of the plant's local clock.",
        ),
        "production_lines": _doc(
            "Production lines; each belongs to one plant.",
            line_id="Line identifier (L01, L02, ...).",
            plant_id="Plant that hosts the line.",
            line_name="Human-readable line name.",
            commissioned_date="Date the line entered service.",
        ),
        "machines": _doc(
            "Machines installed on the lines.",
            machine_id="Machine identifier (M001, ...).",
            line_id="Line the machine belongs to.",
            machine_type="Machine class: press, welder, cnc_mill, paint_robot or assembly_cell.",
            install_date="Date the machine was installed.",
            firmware_version="Controller firmware version at generation time.",
        ),
        "product_families": _doc(
            "Product families grouping products with similar processes.",
            family_id="Family identifier (F01, ...).",
            family_name="Family name.",
        ),
        "products": _doc(
            "Finished components built on the lines.",
            product_id="Product identifier (PR001, ...).",
            family_id="Family the product belongs to.",
            product_name="Product name.",
            complexity_score="Relative build complexity from 1 (simple) to 5 (complex).",
            standard_cycle_time_s="Engineering standard seconds per unit.",
            spec_target_mm="Nominal value of the inspected dimension in millimetres.",
            spec_tolerance_mm="Allowed absolute deviation from the nominal in millimetres.",
            material_id="Primary raw material consumed by the product.",
        ),
        "suppliers": _doc(
            "Raw material suppliers.",
            supplier_id="Supplier identifier (S01, ...).",
            supplier_name="Supplier name.",
            country="ISO country code of the supplier.",
            onboarded_date="Date the supplier was approved.",
        ),
        "materials": _doc(
            "Raw materials with their incoming specification.",
            material_id="Material identifier (MT01, ...).",
            material_name="Material name.",
            unit="Unit of measure for lot quantities.",
            spec_target="Nominal incoming specification value.",
            spec_tolerance="Allowed absolute deviation of the incoming measurement.",
            primary_supplier_id="Supplier that normally delivers the material.",
        ),
        "material_lots": _doc(
            "Received lots of raw material.",
            lot_id="Lot identifier (LOT000001, ...).",
            material_id="Material in the lot.",
            supplier_id="Supplier that delivered the lot.",
            received_date="Date the lot was received.",
            quantity_kg="Received quantity in kilograms.",
            spec_measurement="Incoming inspection measurement of the lot.",
            certified="True when the lot arrived with a material certificate.",
        ),
        "operators": _doc(
            "Shop-floor staff: operators, inspectors and technicians.",
            operator_id="Operator identifier (O001, ...).",
            hire_date="Date of hire.",
            training_certified_date="Date training was certified; null if uncertified.",
            role="operator, inspector or technician.",
            home_line_id="Line the person is normally rostered on.",
            home_shift="Shift the person is normally rostered on: day, evening or night.",
        ),
        "shift_assignments": _doc(
            "Daily roster: which line and shift each person worked.",
            date="Roster date.",
            operator_id="Rostered person.",
            line_id="Line worked on that date.",
            shift="Shift worked: day, evening or night.",
        ),
        "work_orders": _doc(
            "Production orders scheduling a product on a line.",
            work_order_id="Work order identifier.",
            product_id="Product built by the order.",
            line_id="Line the order ran on.",
            planned_qty="Planned units for the order.",
            start_ts="Timestamp of the first production period of the order.",
            end_ts="Timestamp at which the last production period of the order ended.",
            status="Order status (closed for the historical window).",
        ),
        "production_runs": _doc(
            "Hourly production record per line.",
            run_id="Run identifier.",
            ts="Start timestamp of the hourly period.",
            line_id="Line that produced.",
            shift="Shift during the period: day (06-14), evening (14-22) or night (22-06).",
            product_id="Product built during the period.",
            work_order_id="Work order the period belongs to.",
            units_produced="Good plus scrapped units completed in the period.",
            units_scrapped="Units scrapped in the period.",
            cycle_time_s="Observed average seconds per unit.",
            material_lot_id="Material lot consumed; null when not recorded (about 3% of rows).",
        ),
        "quality_inspections": _doc(
            "Dimensional inspections sampled from production runs.",
            inspection_id="Inspection identifier.",
            run_id="Production run the sample came from.",
            ts="Timestamp the inspection was recorded.",
            inspector_id="Operator (role inspector) who measured the sample.",
            gauge_id="Measuring gauge used.",
            measured_value="Measured value of the inspected dimension in millimetres.",
            passed="True when the measurement is within the product tolerance.",
            defect_type="Defect classification for failed inspections; null when passed.",
            source_system="System that recorded the inspection (MES).",
        ),
        "machine_telemetry": _doc(
            "Hourly sensor readings per machine.",
            ts="Start timestamp of the hourly period.",
            machine_id="Machine measured.",
            temperature_c="Average machine temperature in degrees Celsius.",
            vibration_mm_s="Average vibration velocity in mm/s.",
            pressure_bar="Average pneumatic supply pressure in bar.",
            power_kwh="Energy consumed by the machine in the period in kWh.",
        ),
        "downtime_events": _doc(
            "Machine stoppages.",
            event_id="Downtime event identifier.",
            machine_id="Machine that stopped.",
            start_ts="Timestamp the stoppage began.",
            end_ts="Timestamp the stoppage ended.",
            reason_code="Stoppage reason: breakdown, changeover, minor_stop, power or planned.",
            duration_min="Stoppage duration in minutes.",
        ),
        "maintenance_events": _doc(
            "Planned and unplanned maintenance work orders per machine.",
            event_id="Maintenance event identifier.",
            machine_id="Machine maintained.",
            kind="planned (monthly schedule) or unplanned (after a breakdown).",
            scheduled_date="Date the work was scheduled for.",
            performed_date="Date the work was performed; null when the planned work was skipped.",
            parts_replaced="Part replaced during the work; null when none.",
        ),
        "gauge_calibrations": _doc(
            "Quarterly calibration records of measuring gauges.",
            calibration_id="Calibration identifier.",
            gauge_id="Gauge calibrated.",
            due_date="Date the calibration was due.",
            performed_date="Date the calibration was performed.",
            offset_found="Measurement offset found at calibration in millimetres.",
        ),
        "energy_meter_readings": _doc(
            "Hourly energy meter readings per line.",
            ts="Start timestamp of the hourly period.",
            line_id="Line metered.",
            kwh="Energy consumed by the line in the period in kWh.",
        ),
        "warranty_claims": _doc(
            "Customer warranty claims traced back to the production run.",
            claim_id="Claim identifier.",
            product_id="Product claimed.",
            run_id="Production run that built the claimed unit.",
            claim_date="Date the claim was filed (30 to 90 days after production).",
            defect_type="Reported defect: noise, leak, electrical, cosmetic or failure.",
            claim_cost="Cost of the claim in euros.",
        ),
        "business_events": _doc(
            "Calendar of business events affecting operations.",
            event_id="Event identifier.",
            event_date="Date of the event.",
            event_type="Event type: holiday or planned_maintenance (more types in later layers).",
            description="Free-text description.",
            plant_id="Plant concerned; null when the event applies to all plants.",
        ),
    }
)
