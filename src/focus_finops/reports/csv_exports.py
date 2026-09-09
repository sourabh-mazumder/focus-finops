"""Writes summary CSV exports (cost by service/account/region/month, top
resources, savings by commitment coverage) for use in spreadsheets.
"""
from __future__ import annotations

from pathlib import Path

from . import ml_insights, queries


def run(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    exports = {
        "cost_by_service.csv": queries.cost_by_service(),
        "cost_by_account.csv": queries.cost_by_account(),
        "cost_by_region.csv": queries.cost_by_region(),
        "cost_trend_monthly.csv": queries.cost_trend_monthly(),
        "top_resources.csv": queries.top_resources(25),
        "savings_by_commitment_type.csv": queries.savings_by_commitment_type(),
        "commitment_utilization.csv": queries.commitment_utilization(),
        "commitment_utilization_summary.csv": queries.commitment_utilization_summary(),
        "cost_by_charge_category.csv": queries.cost_by_charge_category(),
        "cost_by_provider.csv": queries.cost_by_provider(),
        "cost_by_application.csv": queries.cost_by_application(),
        "cost_by_owner.csv": queries.cost_by_owner(),
        "ml_cost_anomalies.csv": ml_insights.detect_cost_anomalies(),
        "ml_spend_forecast.csv": ml_insights.forecast_spend(),
        "ml_commitment_recommendations.csv": ml_insights.recommend_commitments(),
    }
    written = []
    for filename, df in exports.items():
        path = out_dir / filename
        df.to_csv(path, index=False)
        written.append(path)
    return written
