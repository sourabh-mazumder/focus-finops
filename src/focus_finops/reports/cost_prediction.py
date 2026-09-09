"""Blended 3-month cost prediction with an uncertainty interval.

Combines three different forecasting methods, one per cost segment, chosen
to match how each segment actually behaves rather than fitting one model to
everything:

  - **Fixed-fee model** for Reserved/committed usage (Savings Plans,
    Reserved Instances, Committed Use Discounts): held flat at its
    historical average. Committed capacity is, by design, a steady,
    largely usage-independent charge -- fitting a trend or seasonality to
    it would be fitting noise.
  - **Linear regression** for Storage: object/block storage cost tends to
    grow roughly linearly as data accumulates (absent lifecycle/deletion
    policies), so a simple trend line on day-index is a reasonable and
    transparent fit.
  - **Prophet** (trend + weekly seasonality, no yearly seasonality -- a
    handful of months of history can't support detecting an annual cycle)
    for everything else: Compute, Databases, Networking, Analytics, and
    account-level charges like support/tax. This is the most volatile,
    seasonal segment, and the one that benefits most from a real
    time-series model.

The three components are combined by SUMMING SIMULATED SAMPLE PATHS --
bootstrap draws (from each model's own historical residuals) for the first
two, Prophet's own posterior predictive samples for the third -- rather
than by adding parametric intervals. That avoids assuming Gaussian,
symmetric, or constant-width uncertainty, and lets each component's own
uncertainty shape combine correctly into one blended interval for the
total. The three components are treated as independent (they're different
cost mechanisms), so their samples are combined with independent draws --
a documented simplification, not a claim that spend categories never
co-move in practice.

The forecast is computed at the whole-portfolio level (summed across all
providers/accounts and regenerated fresh each time this is run) --
a per-account/provider breakdown, matching the account-level pattern used
in `ml_insights.py`, is a natural extension but isn't implemented here.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from .. import db
from .queries import TABLE

# Prophet/cmdstanpy log their fit progress at INFO level by default, which
# would otherwise spam every CLI/report run.
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

RNG_SEED = 20260908
N_SAMPLES = 500
INTERVAL = 0.80  # 80% prediction interval (10th-90th percentile of simulated draws)


def _daily_segment_totals() -> pd.DataFrame:
    """Daily billed cost, split into three mutually-exclusive, collectively
    exhaustive segments across the whole portfolio (all providers/accounts):

      - 'reserved': usage billed under a commitment discount (Savings
        Plan / Reserved Instance / Committed Use Discount).
      - 'storage':  Storage-category usage, not under a commitment.
      - 'other':    everything else (Compute, Databases, Networking,
        Analytics, support/tax/credits, ...), not under a commitment.
    """
    return db.query_df(f"""
        SELECT
            date_trunc('day', charge_period_start)::date AS day,
            CASE
                WHEN commitment_discount_id IS NOT NULL THEN 'reserved'
                WHEN service_category = 'Storage' THEN 'storage'
                ELSE 'other'
            END AS segment,
            SUM(billed_cost) AS cost
        FROM {TABLE}
        GROUP BY 1, 2
        ORDER BY 1;
    """)


def _forecast_horizon(last_day) -> pd.DatetimeIndex:
    """The next 3 full calendar months after `last_day`."""
    last_day = pd.Timestamp(last_day)
    start = last_day + pd.Timedelta(days=1)
    first_of_next_month = last_day.replace(day=1) + pd.DateOffset(months=1)
    end = first_of_next_month + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    return pd.date_range(start, end, freq="D")


def _forecast_reserved(cost: pd.Series, horizon: pd.DatetimeIndex, n_samples: int, rng) -> np.ndarray:
    """Fixed-fee model: hold the historical mean flat forward, with
    day-to-day uncertainty bootstrapped from the series' own historical
    residuals around that mean (not a trend or seasonal fit -- committed
    capacity isn't expected to have either).
    """
    mean = float(cost.mean())
    residuals = (cost - mean).to_numpy()
    draws = rng.choice(residuals, size=(n_samples, len(horizon)), replace=True)
    return np.clip(mean + draws, a_min=0, a_max=None)


def _forecast_storage(cost: pd.Series, horizon: pd.DatetimeIndex, n_samples: int, rng) -> np.ndarray:
    """Linear regression of cost on day-index, extrapolated forward, with
    uncertainty bootstrapped from the regression's own residuals.
    """
    x = np.arange(len(cost)).reshape(-1, 1)
    y = cost.to_numpy()
    model = LinearRegression().fit(x, y)
    residuals = y - model.predict(x)

    future_x = np.arange(len(cost), len(cost) + len(horizon)).reshape(-1, 1)
    trend = model.predict(future_x)
    draws = rng.choice(residuals, size=(n_samples, len(horizon)), replace=True)
    return np.clip(trend + draws, a_min=0, a_max=None)


def _forecast_other(day: pd.DatetimeIndex, cost: pd.Series, horizon: pd.DatetimeIndex, n_samples: int, rng) -> np.ndarray:
    """Prophet (trend + weekly seasonality) for the volatile, seasonal
    remainder of spend. Uses Prophet's own posterior predictive samples
    (rather than its parametric yhat_lower/yhat_upper interval) so its
    uncertainty combines cleanly, draw-for-draw, with the other two
    components' bootstrap samples.
    """
    from prophet import Prophet  # imported lazily: heavy optional dependency

    prophet_df = pd.DataFrame({"ds": day, "y": cost.to_numpy()})
    model = Prophet(weekly_seasonality=True, yearly_seasonality=False, daily_seasonality=False)
    model.fit(prophet_df)

    future = model.make_future_dataframe(periods=len(horizon))
    samples = model.predictive_samples(future)["yhat"]  # shape (len(history)+horizon, prophet_n_samples)
    future_samples = samples[-len(horizon):, :].T  # (prophet_n_samples, horizon)

    # Resample to exactly n_samples draws so all three components align
    # element-for-element when summed.
    idx = rng.integers(0, future_samples.shape[0], size=n_samples)
    return np.clip(future_samples[idx, :], a_min=0, a_max=None)


def predict_cost(n_samples: int = N_SAMPLES, interval: float = INTERVAL) -> dict[str, pd.DataFrame]:
    """Blended 3-method cost forecast for the next 3 months, with a
    prediction interval, at the whole-portfolio level.

    Returns a dict with:
      - 'daily': DataFrame [date, actual, forecast, lower, upper] --
        historical days have `actual` populated; forecast days have
        `forecast`/`lower`/`upper` populated (a fan-chart-ready shape).
      - 'monthly': DataFrame [month, forecast, lower, upper] -- the daily
        forecast summed into the next 3 calendar months, for a compact
        summary table.
    Empty DataFrames (for both keys) if there's no data loaded yet.
    """
    rng = np.random.default_rng(RNG_SEED)
    raw = _daily_segment_totals()
    if raw.empty:
        return {"daily": pd.DataFrame(), "monthly": pd.DataFrame()}

    pivot = raw.pivot(index="day", columns="segment", values="cost").fillna(0.0)
    pivot.index = pd.to_datetime(pivot.index)
    pivot = pivot.sort_index()
    for seg in ("reserved", "storage", "other"):
        if seg not in pivot.columns:
            pivot[seg] = 0.0

    horizon = _forecast_horizon(pivot.index.max())

    reserved_samples = _forecast_reserved(pivot["reserved"], horizon, n_samples, rng)
    storage_samples = _forecast_storage(pivot["storage"], horizon, n_samples, rng)
    other_samples = _forecast_other(pivot.index, pivot["other"], horizon, n_samples, rng)

    total_samples = reserved_samples + storage_samples + other_samples  # (n_samples, horizon_days)

    lo_pct, hi_pct = (1 - interval) / 2 * 100, (1 + interval) / 2 * 100
    daily_point = np.median(total_samples, axis=0)
    daily_lo = np.percentile(total_samples, lo_pct, axis=0)
    daily_hi = np.percentile(total_samples, hi_pct, axis=0)

    actual_total = pivot.sum(axis=1)
    history_df = pd.DataFrame({
        "date": actual_total.index.strftime("%Y-%m-%d"),
        "actual": actual_total.to_numpy().round(2),
        "forecast": np.nan, "lower": np.nan, "upper": np.nan,
    })
    forecast_df = pd.DataFrame({
        "date": horizon.strftime("%Y-%m-%d"),
        "actual": np.nan,
        "forecast": daily_point.round(2), "lower": daily_lo.round(2), "upper": daily_hi.round(2),
    })
    daily_df = pd.concat([history_df, forecast_df], ignore_index=True)

    # `horizon` runs from tomorrow through the end of the 3rd full future
    # month, so it starts with a *partial* current month (kept in the daily
    # series above for a visually continuous fan chart) followed by exactly
    # 3 full calendar months -- the last 3 distinct month labels are always
    # those 3 full months, whether or not a partial leading month exists.
    month_labels = horizon.strftime("%Y-%m")
    full_months = sorted(set(month_labels))[-3:]
    monthly_rows = []
    for month in full_months:
        mask = np.asarray(month_labels == month)
        month_sums = total_samples[:, mask].sum(axis=1)
        monthly_rows.append({
            "month": month,
            "forecast": round(float(np.median(month_sums)), 2),
            "lower": round(float(np.percentile(month_sums, lo_pct)), 2),
            "upper": round(float(np.percentile(month_sums, hi_pct)), 2),
        })
    monthly_df = pd.DataFrame(monthly_rows)

    return {"daily": daily_df, "monthly": monthly_df}
