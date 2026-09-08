"""Writes summary CSV exports (cost by service/account/region/month, top
resources, savings by commitment coverage) for use in spreadsheets.
"""
from __future__ import annotations

from pathlib import Path

from . import queries


def run(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    exports = {
        "cost_by_service.csv": queries.cost_by_service(),
        "cost_by_account.csv": queries.cost_by_account(),
        "cost_by_region.csv": queries.cost_by_region(),
        "cost_trend_monthly.csv": queries.cost_trend_monthly(),
        "top_resources.csv": queries.top_resources(25),
        "savings_by_commitment_type.csv": queries.savings_by_commitment_type(),
        "cost_by_charge_category.csv": queries.cost_by_charge_category(),
    }
    written = []
    for filename, df in exports.items():
        path = out_dir / filename
        df.to_csv(path, index=False)
        written.append(path)
    return written
