# focus-finops

A small sample project for multi-cloud FinOps: it loads AWS + Azure + GCP
cost & usage data in **FOCUS v1.4** format ([FinOps Open Cost and Usage
Specification](https://focus.finops.org/)) into PostgreSQL and generates
cost reports (CLI summary, CSV exports, and an interactive HTML dashboard)
from it.

## What's here

- `src/focus_finops/schema.sql` -- Postgres table for the FOCUS v1.4 "Cost
  and Usage" dataset (all 65 spec columns).
- `src/focus_finops/focus_columns.py` -- the single source of truth mapping
  FOCUS column IDs (`BilledCost`, `ServiceCategory`, ...) to database
  columns; used by both the sample generator and the ingest script.
- `src/focus_finops/generate_sample_data.py` -- generates a realistic,
  synthetic multi-month, multi-cloud cost dataset: AWS (EC2, S3, RDS,
  Lambda, DynamoDB, CloudFront), Azure (Virtual Machines, Blob Storage,
  Azure SQL, Functions, Cosmos DB, CDN) and GCP (Compute Engine, Cloud
  Storage, Cloud SQL, Cloud Functions, BigQuery, Cloud CDN) -- plus data
  transfer, per-provider support/tax charges, commitment discounts, and
  one-off credits, in FOCUS v1.4 CSV format. Every linked account
  (AWS sub-account / Azure subscription / GCP project) is tagged with
  `Environment`, `Team`, `CostCenter`, `Application`, and `Owner` -- FOCUS
  has no dedicated Application/Owner columns, so these are carried in the
  `Tags` JSON column like the rest of the tag set. Several applications
  deliberately span more than one cloud provider so cross-cloud,
  per-application/per-owner views are meaningful.
- `src/focus_finops/ingest.py` -- loads any FOCUS-format CSV (the
  synthetic sample, or a real cloud provider export) into Postgres.
- `src/focus_finops/reports/` -- CLI summary, CSV exports, and the
  interactive HTML dashboard.
- `src/focus_finops/reports/ml_insights.py` -- scikit-learn-based cost
  optimization signals: a rolling z-score anomaly detector and, separately,
  per-resource anomaly detection (IsolationForest), next-month spend
  forecasting / overrun risk (linear trend), and commitment (Savings
  Plan/RI/CUD) candidate recommendations (KMeans). See "Machine
  learning-based cost optimization" below.
- `src/focus_finops/reports/cost_prediction.py` -- a blended 3-month cost
  prediction with an uncertainty interval: a fixed-fee model for
  committed/reserved usage, linear regression for storage growth, and
  Prophet (trend + weekly seasonality) for everything else, combined by
  summing simulated sample paths. See "3-month cost prediction" below.
- `src/focus_finops/generate_otel_data.py`, `otel_schema.sql`,
  `otel_ingest.py`, `src/focus_finops/reports/otel_insights.py` -- simulated
  OpenTelemetry resource-utilization telemetry, correlated against real
  FOCUS cost, in a separate table alongside `focus_cost_and_usage`. See
  "Simulated telemetry & cost/utilization correlation" below.
- `src/focus_finops/cli.py` -- the `focus-finops` command-line tool tying
  it all together.

## Requirements

- Python 3.11+
- A PostgreSQL server
- The **`psql` client on your PATH**, but only for the one-time admin
  bootstrap (`scripts/setup_db.sh` / `setup_db.ps1` create the
  `focus_app` role/database as the Postgres superuser) -- the application
  itself never shells out to `psql`.
- Python packages: `pandas`, `click`, `python-dotenv`, `tabulate`, `numpy`,
  `scikit-learn`, `prophet`, `psycopg2-binary`, `SQLAlchemy` (listed in
  `pyproject.toml`)

### Database access: a pooled SQLAlchemy engine

`src/focus_finops/db.py` talks to Postgres through a process-wide, pooled
SQLAlchemy `Engine` over `psycopg2` (`pool_size=5`, `max_overflow=10`,
`pool_pre_ping=True` to transparently replace a connection Postgres has
dropped). Every report/CLI command reuses the same pool rather than opening
a fresh connection per query.

This project originally shelled out to the `psql` CLI per query instead --
zero compiled dependencies, handy for a locked-down environment, but with
no connection pooling, no concurrency control, and errors surfaced as a
parsed subprocess exit code rather than a typed exception. `db.py`'s public
functions (`query_df`, `execute`, `run_sql_file`, `copy_csv_into`,
`DbError`) kept the same signatures across that swap, so nothing in
`reports/` or the CLI needed to change -- the SQL itself was always plain,
driver-agnostic SQL.

## Quickstart

```bash
# 1. Install Python dependencies (if not already present)
pip install -e .

# 2. Create the database + role, and apply the schema
cp .env.example .env   # adjust credentials if needed
./scripts/setup_db.sh

# 3. Generate 6 months of synthetic FOCUS v1.4 sample data
python -m focus_finops.cli generate-sample --months 6

# 4. Load it into Postgres
python -m focus_finops.cli ingest data/samples/focus_sample_*.csv

# 5. Run reports
python -m focus_finops.cli report summary               # terminal summary
python -m focus_finops.cli report export                # CSVs -> reports_output/
python -m focus_finops.cli report dashboard              # HTML -> reports_output/dashboard.html
```

### Windows

`scripts/setup_db.sh` relies on `sudo -u postgres`, which doesn't exist on
Windows. Use the PowerShell equivalents instead:

```powershell
# 1. Install PostgreSQL itself (skip if you already have it + psql on PATH)
.\scripts\install_postgres.ps1

# 2. Open a NEW PowerShell window, then create the database + role and apply the schema
cp .env.example .env
.\scripts\setup_db.ps1
```

If `.ps1` scripts are blocked by your execution policy, run them via
`powershell -ExecutionPolicy Bypass -File .\scripts\install_postgres.ps1`
instead of changing the system-wide policy. From step 3 onward, the
Quickstart commands below work as-is on Windows too.

Open `reports_output/dashboard.html` in a browser. It's a single HTML
file, but **not** offline-capable: it loads [Chart.js](https://www.chartjs.org/)
from a CDN and needs network access on first load. In exchange, it's a
genuinely interactive dashboard rather than a static report -- a
pre-aggregated cost cube (provider / account / application / owner /
service / region / month / resource) is embedded in the page as JSON, and
everything below re-renders client-side as you use it:

- **Filters** for Provider, Account, Application, and Owner (checkbox
  dropdowns, all selected by default) narrow every chart, KPI tile, and
  the top-resources table at once.
- A **"Group by"** selector picks which dimension the main breakdown bar
  chart and the monthly trend chart split by (Provider, Account,
  Application, Owner, Service Category, Service, or Region) -- so e.g.
  you can filter to Owner=Marcus Chen and group by Provider to see the
  "data-platform" application's AWS/Azure/GCP cost split.
- A fixed cost-by-provider donut always shows the multi-cloud split
  regardless of the "group by" choice.

No fixed drill-down hierarchy -- the four filters are independent and
combine freely with the "group by" dimension.

## Machine learning-based cost optimization

`reports/ml_insights.py` adds three scikit-learn models on top of the FOCUS
data, surfaced in the CLI summary, the CSV exports, and a dedicated
dashboard section (all filterable by the same Provider/Account/Application/
Owner filters as the rest of the page):

- **Rolling z-score anomaly detection** -- a classical statistical
  complement to the ML method below: per service, compares each day's cost
  to the trailing 7-day mean/std of the days *before* it, flagging z >= 2.0
  as a warning and z >= 3.0 as critical.
- **Cost anomaly detection** (`IsolationForest`) -- fit independently per
  resource on its own daily cost history, so a $500/day database and a
  $2/day Lambda function are judged against their own baseline rather than
  each other. Flags days that look like runaway spend spikes. The two
  anomaly methods can and do disagree on specific days -- that's expected
  and worth reviewing, not a bug.
- **Spend forecasting / overrun risk** (`LinearRegression`) -- projects next
  month's cost per provider/account/application/owner/service-category
  combination from a straight-line fit over that combination's own monthly
  history, and flags combinations whose forecast exceeds the last actual
  month by 15%+ as an overrun risk. A linear trend is a deliberately simple
  choice given the short history typical of this project's sample data (a
  handful of months) -- not a claim that cloud spend trends are linear in
  general.
- **Commitment (Savings Plan/RI/CUD) candidate recommendations** (`KMeans`)
  -- clusters currently *uncommitted* Compute/Database resources on mean
  daily cost and cost volatility, then flags the cluster with high, steady
  usage as good commitment candidates.

These are decision-support signals for a FinOps review, not automated
actions -- see the caveats printed alongside each section (e.g. the
forecast's short-history disclaimer, and that "commitment coverage"
elsewhere in this project means usage billed at the committed rate, not
used-vs-purchased utilization).

## 3-month cost prediction

`reports/cost_prediction.py` is a separate, blended forecasting model --
distinct from `ml_insights.py`'s single-model `forecast_spend()` -- that
projects total portfolio cost for the next 3 months with an uncertainty
interval, surfaced as a fan chart (actual history, a projected median line,
and a shaded interval band) in the CLI summary, CSV exports, and dashboard.

It splits daily cost into three segments and forecasts each with the method
that fits how it actually behaves, rather than fitting one model to
everything:

- **Fixed-fee model** for Reserved/committed usage (Savings Plans, Reserved
  Instances, Committed Use Discounts) -- held flat at its historical
  average, since committed capacity is a steady, largely usage-independent
  charge by design.
- **Linear regression** for Storage -- object/block storage cost tends to
  grow roughly linearly as data accumulates.
- **Prophet** (trend + weekly seasonality; no yearly seasonality -- a
  handful of months of history can't support detecting one) for everything
  else (Compute, Databases, Networking, Analytics, support/tax/credits) --
  the most volatile, seasonal segment.

The three components are combined by **summing simulated sample paths**
(bootstrap draws for the first two, Prophet's own posterior predictive
samples for the third) rather than by adding parametric intervals -- this
avoids assuming Gaussian, symmetric, or constant-width uncertainty, and
lets each component's own uncertainty shape combine correctly into one
blended interval for the total.

This forecast runs at the whole-portfolio level (summed across all
providers/accounts) and is **not** filterable by Provider/Account/
Application/Owner like the rest of the dashboard -- the dashboard section
says so explicitly. A per-account breakdown (following the account-level
pattern in `ml_insights.py`, where account determines provider/application/
owner in this dataset) is a natural extension but isn't implemented here.

## Simulated telemetry & cost/utilization correlation

FOCUS is a *billing* schema -- it has no notion of CPU/memory/disk
utilization, only what was billed. `generate_otel_data.py` fills that gap
with **simulated OpenTelemetry-style utilization metrics**, generated for
the Compute/Databases/Storage resources already loaded from FOCUS data (not
independently random -- see below), stored in their own table, and
surfaced in a second dashboard tab so cost and utilization can be looked at
side by side.

**Generation** (`generate_otel_data.py`): for each Compute/Databases/Storage
resource in `focus_cost_and_usage`, over that resource's own actual
charge-period date range:
- Metrics follow OTel semantic-convention names -- `system.cpu.utilization`,
  `system.memory.utilization`, `system.filesystem.utilization`,
  `db.client.connections.active` -- mapped onto that resource's real FOCUS
  attributes (`cloud.provider`, `cloud.account.id`, ...).
- Utilization is **not** independent noise: it reuses the same
  weekday/weekend signal already on the resource's account (`Tags ->>
  'Environment'`), and a simple z-score flags that resource's own cost
  spikes so each spike day gets, on a coin flip, either a matching
  utilization spike (a real, demand-driven increase) or no change at all (an
  unexplained cost spike) -- both cases are deliberately produced so there's
  something genuine to find. A subset of committed resources are pinned to a
  low utilization band regardless of cost, to produce real rightsizing
  candidates.
- Written to `data/samples/otel_metrics_<start>_<end>.csv`, then loaded via
  `ingest-otel` into a **new, separate table** (`otel_resource_metrics`,
  `otel_schema.sql`) -- `focus_cost_and_usage`'s structure is never touched.

**Analysis** (`reports/otel_insights.py`): joins daily utilization back to
daily FOCUS cost on `(resource_id, day)` and computes, per resource, the
Pearson correlation between its cost and its category's primary utilization
metric, classifying it as:
- **Cost tracks usage (healthy)** -- cost and utilization move together.
- **Rightsizing candidate** -- persistently low utilization regardless of
  cost (steady/committed spend on a mostly-idle resource).
- **Investigate** -- cost varies independently of utilization (the pattern
  a pricing error, orphaned resource, or untagged job would produce).
- **Weak correlation / monitor** -- neither clearly healthy nor flaggable.

**Dashboard**: a second top-level tab ("Resource Telemetry", alongside "Cost
Dashboard") with a signals-at-a-glance row, a per-resource picker showing
stacked daily-cost and daily-utilization charts, and the full
correlation/classification table -- independent of the Cost Dashboard tab's
Provider/Account/Application/Owner filters (it has its own resource
picker instead).

**Usage:**
```bash
focus-finops setup-otel-db     # create otel_resource_metrics (once)
focus-finops generate-otel     # writes data/samples/otel_metrics_*.csv
focus-finops ingest-otel data/samples/otel_metrics_<start>_<end>.csv
```
Then regenerate reports as usual (`report summary` / `report export` /
`report dashboard`) -- all three degrade gracefully (empty section, not an
error) if this hasn't been run yet.

**This is simulated data demonstrating a correlation *method*** -- a
legitimate, valuable FinOps technique in practice (what AWS Compute
Optimizer or Azure Advisor do against real CloudWatch/Monitor data) -- not
a finding about real infrastructure, since the "waste" and "unexplained
spikes" here are exactly what the generator was told to inject.

## Loading your own FOCUS export

```bash
python -m focus_finops.cli ingest /path/to/your_focus_export.csv
```

The ingest script matches CSV headers against the official FOCUS v1.4
column IDs (case-sensitive, e.g. `BilledCost`, `SubAccountId`) -- any
subset or reordering of columns is fine, and unrecognized columns are
reported and skipped rather than failing the load. If your export uses a
different header convention, rename the header row (or adjust the mapping
in `focus_columns.py`) to match.

## Schema notes

FOCUS marks columns as Mandatory / Conditional / Recommended / Optional,
but "Mandatory" describes the *column*, not that every row's *value* is
non-null (e.g. `ResourceId` is Mandatory but is legitimately empty for an
account-level charge like tax). `schema.sql` enforces `NOT NULL` only on
the subset of columns that are realistically always populated (the three
core cost amounts, billing/charge period dates, currency, and
charge/service classification) -- see the comments at the top of that file
and `focus_columns.NOT_NULL_FOCUS_IDS`.

**Known simplification in the sample data:** the generator sets
`BilledCost == EffectiveCost` for every row (no proration/invoice-timing
differences), while `ListCost` and `ContractedCost` do differ from
`EffectiveCost` (to demonstrate FOCUS's List -> Contracted -> Effective
discount chain and Savings Plan/Reserved Instance coverage). A real AWS
export can have `BilledCost` differ from `EffectiveCost` in some
circumstances (e.g. upfront commitment purchases amortized over time).

## Tests

A small `unittest`-based suite (no `pytest` needed) sanity-checks the FOCUS
column mapping and the sample data generator:

```bash
python -m unittest discover -s tests -v
```

## Extending

- **More report cuts:** add a query to `reports/queries.py` and a chart/
  table to `reports/html_dashboard.py` or a new CSV in
  `reports/csv_exports.py`.
- **More ML signals:** add a function to `reports/ml_insights.py` returning
  a DataFrame with `provider`/`account`/`application`/`owner` columns (so it
  can reuse the existing dashboard filter wiring), then wire it into
  `cli_summary.py`, `csv_exports.py`, and `html_dashboard.py` the same way
  as the three existing ones.
- **A bigger/different sample dataset:** edit the resource catalog in
  `generate_sample_data.py` (services, accounts, regions, growth/anomaly
  parameters) and re-run `generate-sample`.
- **Scaling to large real exports:** `ingest.py` currently reads the whole
  CSV into memory with pandas before loading; for very large files (the
  multi-GB CUR-in-FOCUS exports some AWS accounts produce), switch to a
  streaming/chunked read before the `\copy`.
