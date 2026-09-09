"""REST API exposing paginated, filtered access to FOCUS cost data and its
derived analytics (anomalies, forecasts, telemetry correlation).

This exists to stop shipping whole datasets to the browser at once: the
dashboard (reports/html_dashboard.py) originally embedded every dataset as
JSON directly in the generated HTML file, which is simple and fast for a
handful of KB but stops making sense once a dataset is large -- the
per-resource OTel telemetry alone is ~3MB embedded wholesale for this
project's synthetic data, most of which is irrelevant to any single view
of the dashboard.

Every endpoint here returns only the page/filter the caller actually
asked for. The dashboard's Resource Telemetry tab now calls this API on
demand (see html_dashboard.py's `API_BASE`/`fetch` usage) instead of
embedding OTel data; the Cost Dashboard tab still embeds its (much
smaller) datasets -- converting its instant, client-side filtering to
fetch-per-filter-change is a larger follow-up, not done here.

Run with `focus-finops serve`.
"""
from __future__ import annotations

import json

from flask import Flask, Response, jsonify, request

from . import db
from .reports import cost_prediction, ml_insights, otel_insights, queries

app = Flask(__name__)

FILTER_DIMS = ("provider", "account", "application", "owner")
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


@app.after_request
def _allow_cross_origin(response):
    # The dashboard HTML is typically opened as a local file (or served
    # separately from this API's own host/port); allow it to fetch from
    # here. Fine for a local/demo tool -- a real deployment should scope
    # this to the dashboard's actual origin instead of "*".
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def _filter_dims(df, args):
    for dim in FILTER_DIMS:
        values = args.getlist(dim)
        if values and dim in df.columns:
            df = df[df[dim].isin(values)]
    return df


def _paged(df) -> Response:
    """Wrap a DataFrame as `{total, limit, offset, items}`, applying
    limit/offset from the request's query string. Serializes via pandas'
    own `to_json` (NaN -> null, dates already normalized to ISO strings by
    db.py) rather than Flask's default JSON encoder, so numeric/date
    handling matches the rest of this project exactly.
    """
    try:
        limit = min(max(int(request.args.get("limit", DEFAULT_LIMIT)), 1), MAX_LIMIT)
    except ValueError:
        limit = DEFAULT_LIMIT
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        offset = 0

    total = len(df)
    page = df.iloc[offset: offset + limit]
    items = json.loads(page.to_json(orient="records"))
    return jsonify({"total": total, "limit": limit, "offset": offset, "items": items})


@app.get("/")
def index():
    """A landing page, since navigating to the bare host:port with no
    route registered there is a natural first thing to try and otherwise
    just 404s with no clue what's actually available."""
    endpoints = sorted(
        str(rule) for rule in app.url_map.iter_rules()
        if rule.endpoint != "static" and "GET" in rule.methods
    )
    return jsonify({
        "service": "focus-finops API",
        "docs": "see the README's \"REST API\" section",
        "health_check": "/health",
        "endpoints": endpoints,
    })


@app.get("/health")
def health():
    try:
        db.check_connection()
    except db.DbError as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 503
    return jsonify({"status": "ok"})


@app.get("/api/cost/cube")
def cost_cube():
    return _paged(_filter_dims(queries.dashboard_cube(), request.args))


@app.get("/api/cost/commitment-utilization")
def commitment_utilization():
    return _paged(_filter_dims(queries.commitment_utilization(), request.args))


@app.get("/api/ml/zscore-anomalies")
def zscore_anomalies():
    return _paged(_filter_dims(ml_insights.detect_zscore_anomalies(), request.args))


@app.get("/api/ml/anomalies")
def ml_anomalies():
    return _paged(_filter_dims(ml_insights.detect_cost_anomalies(), request.args))


@app.get("/api/ml/forecast")
def ml_forecast():
    return _paged(_filter_dims(ml_insights.forecast_spend(), request.args))


@app.get("/api/ml/recommendations")
def ml_recommendations():
    return _paged(_filter_dims(ml_insights.recommend_commitments(), request.args))


@app.get("/api/prediction/daily")
def prediction_daily():
    return _paged(cost_prediction.predict_cost()["daily"])


@app.get("/api/prediction/monthly")
def prediction_monthly():
    return _paged(cost_prediction.predict_cost()["monthly"])


def _filter_category(df, args):
    categories = args.getlist("service_category")
    if categories and "service_category" in df.columns:
        df = df[df["service_category"].isin(categories)]
    return df


@app.get("/api/otel/resources")
def otel_resources():
    """Distinct resources (for populating a picker), optionally filtered
    by `service_category` -- e.g. `?service_category=Compute`.
    """
    df = otel_insights.cost_utilization_correlation()
    df = _filter_category(df, request.args)
    cols = ["resource_id", "resource_name", "provider", "account", "service_category"]
    return _paged(df[cols].drop_duplicates(subset="resource_id") if not df.empty else df)


@app.get("/api/otel/daily")
def otel_daily():
    """One resource's full daily cost + utilization series. `resource_id`
    is required -- this is the endpoint that replaces embedding every
    resource's history wholesale; the caller asks for exactly the one it
    needs.
    """
    resource_id = request.args.get("resource_id")
    if not resource_id:
        return jsonify({"error": "resource_id query parameter is required"}), 400
    df = otel_insights.resource_daily_series(resource_id=resource_id)
    return _paged(df)


@app.get("/api/otel/correlation")
def otel_correlation():
    df = otel_insights.cost_utilization_correlation()
    df = _filter_category(df, request.args)
    return _paged(df)
