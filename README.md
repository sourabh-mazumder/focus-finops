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
  optimization signals: per-resource anomaly detection (IsolationForest),
  next-month spend forecasting / overrun risk (linear trend), and
  commitment (Savings Plan/RI/CUD) candidate recommendations (KMeans). See
  "Machine learning-based cost optimization" below.
- `src/focus_finops/cli.py` -- the `focus-finops` command-line tool tying
  it all together.

## Requirements

- Python 3.11+
- A PostgreSQL server, with the **`psql` client on your PATH**
- Python packages: `pandas`, `click`, `python-dotenv`, `tabulate`, `numpy`,
  `scikit-learn` (listed in `pyproject.toml`)

### Why `psql` instead of psycopg2 / SQLAlchemy?

This project talks to Postgres by shelling out to the `psql` command-line
client (see `src/focus_finops/db.py`) rather than using a compiled Python
driver. That keeps the dependency footprint to pure-Python packages only --
handy in locked-down environments where installing a compiled driver isn't
an option, and it's how this project was built and tested. If you'd rather
use `psycopg2`/`SQLAlchemy` in your own environment, they're listed as an
optional `driver` extra in `pyproject.toml` -- the SQL itself
(`schema.sql`, `reports/queries.py`) is plain, driver-agnostic SQL, so
swapping `db.py` for a native-driver version is a self-contained change.

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

- **Cost anomaly detection** (`IsolationForest`) -- fit independently per
  resource on its own daily cost history, so a $500/day database and a
  $2/day Lambda function are judged against their own baseline rather than
  each other. Flags days that look like runaway spend spikes.
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
