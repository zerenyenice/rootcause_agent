"""Easy archetype E3: a line is reassigned to the highest-complexity product family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pandas as pd

from rootcause.evals.manufacturing.baseline import BASE_AVAILABILITY, pick_recent_lots
from rootcause.evals.manufacturing.incidents.base import (
    PRE_WINDOW_DAYS,
    Incident,
    Tables,
    day_of,
    in_span,
    window_bounds,
)
from rootcause.evals.manufacturing.incidents.manifest import (
    CaseManifest,
    ChainStep,
    Counterfactual,
    ExpectedEffect,
    MetricSpec,
    RootCause,
)

CYCLE_TIME_FACTOR = 1.35  # target mean ratio of new to old standard cycle time in the window
SECONDS_PER_HOUR = 3600.0
PLANNED_QTY_ROUNDING = 10.0
RATIO_PENALTY = 0.1  # weight of a single order's deviation versus the running window mean


def top_family(products: pd.DataFrame) -> str:
    """Family of the single product with the highest complexity score."""
    return str(products.sort_values("complexity_score").iloc[-1]["family_id"])


def choose_replacements(
    old_std: npt.NDArray[np.float64],
    old_cycle_sum: npt.NDArray[np.float64],
    runs: npt.NDArray[np.float64],
    family_std: npt.NDArray[np.float64],
    target_mean: float,
) -> npt.NDArray[np.int64]:
    """Index into the family for each order (given in time order).

    ``old_cycle_sum`` and ``runs`` are each order's in-window cycle time total and run
    count before the switch. An order moved to a product with ratio ``r = new_std /
    old_std`` contributes ``r * old_cycle_sum``; each order takes the family product that
    keeps the window mean cycle time closest to ``target_mean`` (capped at the family's
    ceiling, so a line with little headroom gets the largest rise possible; wide single-order
    deviations from ``CYCLE_TIME_FACTOR`` are mildly discouraged), so the window mean
    lands as near the target as the family allows.
    """
    picks = np.zeros(len(old_std), dtype=np.int64)
    total_cycle = total_runs = 0.0
    penalty = RATIO_PENALTY
    if runs.sum() > 0:
        ceiling = float((old_cycle_sum * family_std.max() / old_std).sum() / runs.sum())
        if target_mean >= ceiling:  # unreachable target: every order takes its slowest product
            target_mean, penalty = ceiling, 0.0
    for k in range(len(old_std)):
        ratios = family_std / old_std[k]
        if runs[k] == 0:  # touches the window without a run in it: nothing to calibrate
            picks[k] = int(np.abs(ratios - CYCLE_TIME_FACTOR).argmin())
            continue
        running = (total_cycle + ratios * old_cycle_sum[k]) / (total_runs + runs[k])
        cost = np.abs(running - target_mean) / target_mean + penalty * np.abs(
            ratios - CYCLE_TIME_FACTOR
        )
        picks[k] = int(cost.argmin())
        total_cycle += float(ratios[picks[k]]) * old_cycle_sum[k]
        total_runs += runs[k]
    return picks


@dataclass(frozen=True)
class ProductMixChange(Incident):
    """E3: cycle time rises by about a third on one line for two weeks after a mix change."""

    archetype: ClassVar[str] = "E3"
    level: ClassVar[str] = "easy"
    direction: ClassVar[str] = "up"

    def apply(self, tables: Tables, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
        """Switch overlapping work orders to the top family and rescale all their runs.

        Every run of a switched order keeps the baseline ratio of cycle time to the
        product's standard: it is scaled by the order's own ``new_std / old_std``, units
        and scrap by the inverse, whether or not the run lies inside the window.
        """
        start, end = window_bounds(self.window)
        line = self.segment["line_id"]
        products = tables["products"].set_index("product_id")
        family = products[products["family_id"] == top_family(tables["products"])]
        orders, runs = tables["work_orders"], tables["production_runs"]
        selected = (
            (orders["line_id"] == line) & (orders["start_ts"] < end) & (orders["end_ts"] >= start)
        )
        chosen = orders[selected].sort_values("start_ts", kind="stable")
        line_runs = runs[runs["line_id"] == line]
        in_window = line_runs[in_span(line_runs["ts"], start, end)]
        per_order = in_window.groupby("work_order_id")["cycle_time_s"].agg(["sum", "count"])
        per_order = per_order.reindex(chosen["work_order_id"]).fillna(0.0)
        pre_mean = line_runs.loc[
            in_span(line_runs["ts"], start - pd.Timedelta(days=PRE_WINDOW_DAYS), start),
            "cycle_time_s",
        ].mean()
        old_std = chosen["product_id"].map(products["standard_cycle_time_s"]).to_numpy(float)
        family_std = family["standard_cycle_time_s"].to_numpy(float)
        picks = choose_replacements(
            old_std,
            per_order["sum"].to_numpy(np.float64),
            per_order["count"].to_numpy(np.float64),
            family_std,
            CYCLE_TIME_FACTOR * float(pre_mean),
        )
        new_product = pd.Series(family.index.to_numpy()[picks], index=chosen.index)
        new_std = new_product.map(products["standard_cycle_time_s"]).to_numpy(float)
        switch = pd.DataFrame(
            {
                "old_product": chosen["product_id"].to_numpy(),
                "new_product": new_product.to_numpy(),
                "ratio": new_std / old_std,
            },
            index=chosen["work_order_id"].to_numpy(),
        )
        new_runs, changed = self._rewrite_runs(tables, switch, rng)
        return {
            **tables,
            "work_orders": self._switch_orders(orders, chosen.index, new_product, new_std),
            "production_runs": new_runs,
            "quality_inspections": self._remap_inspections(tables, changed),
            "warranty_claims": self._remap_claims(tables["warranty_claims"], changed),
        }

    @staticmethod
    def _switch_orders(
        orders: pd.DataFrame,
        index: pd.Index[int],
        new_product: pd.Series[str],
        new_std: npt.NDArray[np.float64],
    ) -> pd.DataFrame:
        sub = orders.loc[index]
        hours = (sub["end_ts"] - sub["start_ts"]).dt.total_seconds() / SECONDS_PER_HOUR
        planned = np.round(
            hours.to_numpy() * SECONDS_PER_HOUR / new_std * BASE_AVAILABILITY / PLANNED_QTY_ROUNDING
        )
        return orders.assign(
            product_id=orders["product_id"].mask(orders.index.isin(index), new_product),
            planned_qty=orders["planned_qty"].mask(
                orders.index.isin(index),
                pd.Series(planned * PLANNED_QTY_ROUNDING, index=index).astype("int64"),
            ),
        )

    def _rewrite_runs(
        self, tables: Tables, switch: pd.DataFrame, rng: np.random.Generator
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Rescaled runs plus the (run_id, old, new product) frame of runs whose product changed."""
        runs = tables["production_runs"]
        follows = (
            (runs["line_id"] == self.segment["line_id"]) & runs["work_order_id"].isin(switch.index)
        ).to_numpy()
        order = runs["work_order_id"]
        factor = np.where(follows, order.map(switch["ratio"]).fillna(1.0).to_numpy(float), 1.0)
        product = runs["product_id"].where(~follows, order.map(switch["new_product"]))
        changed_mask = (product != runs["product_id"]).to_numpy()
        new_runs = runs.assign(
            product_id=product,
            cycle_time_s=np.round(runs["cycle_time_s"].to_numpy() * factor, 2),
            units_produced=np.rint(runs["units_produced"].to_numpy() / factor).astype("int64"),
            units_scrapped=np.rint(runs["units_scrapped"].to_numpy() / factor).astype("int64"),
            material_lot_id=self._relot(tables, runs, product, changed_mask, rng),
        )
        changed = pd.DataFrame(
            {
                "run_id": runs["run_id"].to_numpy()[changed_mask],
                "old_product": runs["product_id"].to_numpy()[changed_mask],
                "new_product": product.to_numpy()[changed_mask],
            }
        ).set_index("run_id")
        return new_runs, changed

    @staticmethod
    def _remap_inspections(tables: Tables, changed: pd.DataFrame) -> pd.DataFrame:
        """Re-express measured values against the new product's spec, keeping pass flags."""
        inspections = tables["quality_inspections"]
        products = tables["products"].set_index("product_id")
        hit = inspections["run_id"].isin(changed.index).to_numpy()
        if not hit.any():
            return inspections
        rows = changed.reindex(inspections["run_id"].to_numpy()[hit])
        old, new = (
            products.reindex(rows[col].to_numpy()) for col in ("old_product", "new_product")
        )
        deviation = inspections["measured_value"].to_numpy()[hit] - old["spec_target_mm"].to_numpy()
        scaled = (
            new["spec_target_mm"].to_numpy()
            + deviation * new["spec_tolerance_mm"].to_numpy() / old["spec_tolerance_mm"].to_numpy()
        )
        values = inspections["measured_value"].to_numpy(dtype=float).copy()
        values[hit] = np.round(scaled, 4)
        return inspections.assign(measured_value=values)

    @staticmethod
    def _remap_claims(claims: pd.DataFrame, changed: pd.DataFrame) -> pd.DataFrame:
        """Claims raised by a switched run belong to the run's new product."""
        new_product = claims["run_id"].map(changed["new_product"])
        return claims.assign(product_id=claims["product_id"].mask(new_product.notna(), new_product))

    @staticmethod
    def _relot(
        tables: Tables,
        runs: pd.DataFrame,
        product: pd.Series[str],
        changed: np.ndarray[Any, Any],
        rng: np.random.Generator,
    ) -> pd.Series[str]:
        """Lots of the new product's material for switched runs; null lots stay null."""
        lot = runs["material_lot_id"]
        redo = changed & lot.notna().to_numpy()
        if not redo.any():
            return lot
        material = product[redo].map(tables["products"].set_index("product_id")["material_id"])
        picked = pick_recent_lots(
            rng,
            runs["ts"].to_numpy()[redo],
            material.to_numpy(dtype=str),
            tables["material_lots"],
        )
        values = lot.to_numpy(dtype=object).copy()
        values[redo] = picked
        return pd.Series(values, index=lot.index, dtype=lot.dtype)

    def daily_metric(self, tables: Tables) -> pd.Series[float]:
        """Daily mean cycle time of the affected line."""
        runs = tables["production_runs"]
        line = runs[runs["line_id"] == self.segment["line_id"]]
        return pd.Series(line.groupby(day_of(line, "ts"))["cycle_time_s"].mean(), dtype=float)

    def achieved_effect(self, tables: Tables) -> float:
        """Window mean cycle time over the pre-window mean on the line, minus one."""
        runs = tables["production_runs"]
        line = runs[runs["line_id"] == self.segment["line_id"]]
        start, end = window_bounds(self.window)
        pre = in_span(line["ts"], start - pd.Timedelta(days=PRE_WINDOW_DAYS), start)
        window = in_span(line["ts"], start, end)
        return float(
            line.loc[window, "cycle_time_s"].mean() / line.loc[pre, "cycle_time_s"].mean() - 1
        )

    def manifest_entry(self, tables: Tables | None = None) -> CaseManifest:
        """Ground truth; the effect size is the achieved window/pre ratio minus one.

        Measured on ``tables`` when given, else the value the registry measured on the baseline
        at placement (``params["expected_effect"]``), else the target.
        """
        line = self.segment["line_id"]
        first, last = (d.isoformat() for d in self.window)
        size = (
            float(self.params.get("expected_effect", CYCLE_TIME_FACTOR - 1.0))
            if tables is None
            else self.achieved_effect(tables)
        )
        family = self.params.get("family_id") or (
            top_family(tables["products"]) if tables is not None else None
        )
        chain = (
            ChainStep(
                f"Average cycle time on line {line} rises by about a third during the window",
                ("production_runs",),
                ("line_id", "ts", "cycle_time_s", "units_produced"),
                # sql_hint is manifest text, never executed; values are generator IDs/dates
                f"SELECT CAST(ts AS DATE), AVG(cycle_time_s) FROM production_runs "  # nosec B608
                f"WHERE line_id = '{line}' GROUP BY 1",
            ),
            ChainStep(
                f"Work orders on line {line} were switched to the highest-complexity family",
                ("work_orders", "products"),
                ("line_id", "product_id", "start_ts", "end_ts", "family_id", "complexity_score"),
                # sql_hint is manifest text, never executed; values are generator IDs/dates
                f"SELECT w.product_id, p.family_id, p.complexity_score FROM work_orders w "  # nosec B608
                f"JOIN products p USING (product_id) WHERE w.line_id = '{line}' "
                f"AND w.start_ts < TIMESTAMP '{last} 23:59:59' AND w.end_ts >= TIMESTAMP '{first}'",
            ),
            ChainStep(
                "The standard cycle time of the new family explains the longer cycle times",
                ("products", "production_runs"),
                ("product_id", "standard_cycle_time_s", "cycle_time_s"),
                # sql_hint is manifest text, never executed; values are generator IDs/dates
                "SELECT p.family_id, AVG(p.standard_cycle_time_s), AVG(r.cycle_time_s) "  # nosec B608
                f"FROM production_runs r JOIN products p USING (product_id) "
                f"WHERE r.line_id = '{line}' GROUP BY 1",
            ),
        )
        keywords = (
            "cycle_time_s",
            "work_orders",
            "product family",
            "complexity_score",
            line,
            *((str(family),) if family else ()),
        )
        return self.build_manifest(
            metric=MetricSpec(
                "avg_cycle_time_s", "production_runs", "AVG(cycle_time_s)", "line_id x day"
            ),
            effect=ExpectedEffect("up", round(size, 4)),
            chain=chain,
            root_cause=RootCause(
                f"A planned product mix change moved line {line} to the highest-complexity "
                "product family, whose standard cycle time is longer",
                "process",
            ),
            counterfactual=Counterfactual(
                "Relative to each product's standard cycle time the line is unchanged: the "
                "ratio stays flat through the window, so the rise comes from the product mix",
                # sql_hint is manifest text, never executed; values are generator IDs/dates
                f"SELECT CAST(r.ts AS DATE), AVG(r.cycle_time_s / p.standard_cycle_time_s) "  # nosec B608
                f"FROM production_runs r JOIN products p USING (product_id) "
                f"WHERE r.line_id = '{line}' GROUP BY 1 ORDER BY 1",
            ),
            keywords=keywords,
        )
