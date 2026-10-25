"""Easy archetype E3: a line is reassigned to the highest-complexity product family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.baseline import BASE_AVAILABILITY
from rootcause_evals.manufacturing.incidents.base import (
    Incident,
    Tables,
    day_of,
    in_span,
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

CYCLE_TIME_FACTOR = 1.3
SECONDS_PER_HOUR = 3600.0
PLANNED_QTY_ROUNDING = 10.0


def top_family(products: pd.DataFrame) -> str:
    """Family of the single product with the highest complexity score."""
    return str(products.sort_values("complexity_score").iloc[-1]["family_id"])


@dataclass(frozen=True)
class ProductMixChange(Incident):
    """E3: cycle time rises 30 percent on one line for two weeks after a mix change."""

    archetype: ClassVar[str] = "E3"
    level: ClassVar[str] = "easy"
    direction: ClassVar[str] = "up"

    def apply(self, tables: Tables, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
        """Switch overlapping work orders and their runs to the top family; slow the window."""
        start, end = window_bounds(self.window)
        line = self.segment["line_id"]
        products = tables["products"].set_index("product_id")
        family = products[products["family_id"] == top_family(tables["products"])]
        orders = tables["work_orders"]
        selected = (
            (orders["line_id"] == line) & (orders["start_ts"] < end) & (orders["end_ts"] >= start)
        )
        old_std = orders["product_id"].map(products["standard_cycle_time_s"]).to_numpy(float)
        family_std = family["standard_cycle_time_s"].to_numpy(float)
        # Per order, the family product whose standard cycle time is closest to 1.3x the old one.
        nearest = np.abs(family_std[None, :] - CYCLE_TIME_FACTOR * old_std[:, None]).argmin(axis=1)
        new_product = pd.Series(family.index.to_numpy()[nearest], index=orders.index)
        switched = orders["product_id"].where(~selected, new_product)
        new_std = switched.map(products["standard_cycle_time_s"]).to_numpy(float)
        hours = (
            orders["end_ts"] - orders["start_ts"]
        ).dt.total_seconds().to_numpy() / SECONDS_PER_HOUR
        planned = np.round(
            hours * SECONDS_PER_HOUR / new_std * BASE_AVAILABILITY / PLANNED_QTY_ROUNDING
        )
        new_orders = orders.assign(
            product_id=switched,
            planned_qty=np.where(
                selected, planned * PLANNED_QTY_ROUNDING, orders["planned_qty"]
            ).astype("int64"),
        )
        order_product = pd.Series(
            switched[selected].to_numpy(), index=orders.loc[selected, "work_order_id"].to_numpy()
        )
        return {
            **tables,
            "work_orders": new_orders,
            "production_runs": self._rewrite_runs(
                tables["production_runs"], order_product, start, end
            ),
        }

    def _rewrite_runs(
        self,
        runs: pd.DataFrame,
        order_product: pd.Series[str],
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DataFrame:
        on_line = runs["line_id"] == self.segment["line_id"]
        follows = on_line & runs["work_order_id"].isin(order_product.index)
        slowed = (on_line & in_span(runs["ts"], start, end)).to_numpy()
        product = runs["product_id"].where(~follows, runs["work_order_id"].map(order_product))
        cycle = runs["cycle_time_s"].to_numpy() * np.where(slowed, CYCLE_TIME_FACTOR, 1.0)
        units = np.where(
            slowed, np.rint(runs["units_produced"] / CYCLE_TIME_FACTOR), runs["units_produced"]
        )
        scrapped = np.where(
            slowed, np.rint(runs["units_scrapped"] / CYCLE_TIME_FACTOR), runs["units_scrapped"]
        )
        return runs.assign(
            product_id=product,
            cycle_time_s=np.round(cycle, 2),
            units_produced=units.astype("int64"),
            units_scrapped=scrapped.astype("int64"),
        )

    def daily_metric(self, tables: Tables) -> pd.Series[float]:
        """Daily mean cycle time of the affected line."""
        runs = tables["production_runs"]
        line = runs[runs["line_id"] == self.segment["line_id"]]
        return pd.Series(line.groupby(day_of(line, "ts"))["cycle_time_s"].mean(), dtype=float)

    def manifest_entry(self) -> CaseManifest:
        """Ground truth for the product mix change."""
        line = self.segment["line_id"]
        start, end = (d.isoformat() for d in self.window)
        chain = [
            ChainStep(
                f"Average cycle time on line {line} rises about 30 percent during the window",
                ["production_runs"],
                ["line_id", "ts", "cycle_time_s", "units_produced"],
                f"SELECT CAST(ts AS DATE), AVG(cycle_time_s) FROM production_runs "
                f"WHERE line_id = '{line}' GROUP BY 1",
            ),
            ChainStep(
                f"Work orders on line {line} were switched to the highest-complexity family",
                ["work_orders", "products"],
                ["line_id", "product_id", "start_ts", "end_ts", "family_id", "complexity_score"],
                f"SELECT w.product_id, p.family_id, p.complexity_score FROM work_orders w "
                f"JOIN products p USING (product_id) WHERE w.line_id = '{line}' "
                f"AND w.start_ts < TIMESTAMP '{end} 23:59:59' AND w.end_ts >= TIMESTAMP '{start}'",
            ),
            ChainStep(
                "The standard cycle time of the new family explains the longer cycle times",
                ["products", "production_runs"],
                ["product_id", "standard_cycle_time_s", "cycle_time_s"],
                "SELECT p.family_id, AVG(p.standard_cycle_time_s), AVG(r.cycle_time_s) "
                f"FROM production_runs r JOIN products p USING (product_id) "
                f"WHERE r.line_id = '{line}' GROUP BY 1",
            ),
        ]
        return self.build_manifest(
            metric=MetricSpec(
                "avg_cycle_time_s", "production_runs", "AVG(cycle_time_s)", "line_id x day"
            ),
            effect=ExpectedEffect("up", CYCLE_TIME_FACTOR - 1.0),
            chain=chain,
            root_cause=RootCause(
                f"A planned product mix change moved line {line} to the highest-complexity "
                "product family, whose standard cycle time is longer",
                "process",
            ),
            counterfactual=Counterfactual(
                "Keeping the previous product family on the line would leave cycle time "
                "and throughput at their usual level",
                f"SELECT AVG(cycle_time_s) FROM production_runs WHERE line_id = '{line}'",
            ),
            keywords=["cycle_time_s", "work_orders", "product family", "complexity_score"],
        )
