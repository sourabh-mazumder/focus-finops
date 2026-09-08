"""Generates a realistic, synthetic AWS cost/usage dataset in FOCUS v1.4
CSV format (data/samples/focus_sample_<start>_<end>.csv).

This is sample data for exercising the ingest + report pipeline, not a
real billing export. It models a small AWS Organization: one payer
(billing) account and several linked (sub) accounts, running EC2, Lambda,
S3, EBS, RDS, DynamoDB, CloudFront and data transfer, over the last N
months, with:
  - daily usage-based charges per resource (with weekday/weekend and
    growth patterns, plus one deliberate cost anomaly to make trend
    reports interesting),
  - a flat enterprise "contracted" discount applied to (almost) everything
    (List Cost -> Contracted Cost), to demonstrate FOCUS's List/Contracted/
    Effective cost distinction,
  - a subset of EC2/RDS resources covered by Savings Plans / Reserved
    Instances (populates the Commitment Discount * columns and further
    reduces Effective Cost),
  - monthly AWS Support and Tax charges per account,
  - a couple of one-off promotional credits.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .focus_columns import ALL_FOCUS_IDS

RNG_SEED = 20260908
random.seed(RNG_SEED)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"

BILLING_ACCOUNT_ID = "111111111111"
BILLING_ACCOUNT_NAME = "acme-payer"
BILLING_CURRENCY = "USD"
SERVICE_PROVIDER = "AWS"
INVOICE_ISSUER = "Amazon Web Services, Inc."

SUB_ACCOUNTS = [
    {"id": "222222222222", "name": "acme-prod", "type": "Linked",
     "tags": {"Environment": "production", "Team": "platform-eng", "CostCenter": "CC-1001"}},
    {"id": "333333333333", "name": "acme-staging", "type": "Linked",
     "tags": {"Environment": "staging", "Team": "platform-eng", "CostCenter": "CC-1001"}},
    {"id": "444444444444", "name": "acme-data", "type": "Linked",
     "tags": {"Environment": "production", "Team": "data-eng", "CostCenter": "CC-2044"}},
]

REGIONS = [
    {"id": "us-east-1", "name": "US East (N. Virginia)", "azs": ["us-east-1a", "us-east-1b", "us-east-1c"]},
    {"id": "us-west-2", "name": "US West (Oregon)", "azs": ["us-west-2a", "us-west-2b"]},
    {"id": "eu-west-1", "name": "Europe (Ireland)", "azs": ["eu-west-1a", "eu-west-1b"]},
]

ENTERPRISE_DISCOUNT = 0.03   # flat negotiated discount: List -> Contracted
COMMITMENT_DISCOUNT = 0.32   # additional discount for Savings-Plan/RI covered usage


@dataclass
class Resource:
    resource_id: str
    resource_name: str
    resource_type: str
    service_category: str
    service_name: str
    service_subcategory: str
    sku_id: str
    sku_meter: str
    pricing_unit: str
    consumed_unit: str
    list_unit_price: float
    base_quantity: float
    growth_per_day: float
    sub_account: dict
    region: dict
    az: str | None
    committed: bool = False
    commitment_type: str = ""       # "SavingsPlan" | "Reserved"
    commitment_coverage: float = 0.0  # fraction of usage covered
    weekday_dampening: bool = True   # lower usage on weekends (prod web-style workload)
    anomaly_month_index: int | None = None
    anomaly_multiplier: float = 1.0


def _build_resources() -> list[Resource]:
    resources: list[Resource] = []

    def acct(i: int) -> dict:
        return SUB_ACCOUNTS[i % len(SUB_ACCOUNTS)]

    def region(i: int) -> dict:
        return REGIONS[i % len(REGIONS)]

    # --- EC2 instances -----------------------------------------------
    ec2_specs = [
        ("t3.medium", 0.0416, 1),
        ("m5.large", 0.096, 1),
        ("m5.xlarge", 0.192, 1),
        ("c5.2xlarge", 0.34, 1),
        ("r5.large", 0.126, 1),
    ]
    for idx, (itype, price, _) in enumerate(ec2_specs):
        r = region(idx)
        a = acct(idx)
        committed = idx % 2 == 0
        resources.append(Resource(
            resource_id=f"i-0{RNG_SEED:08x}{idx:02x}",
            resource_name=f"{a['name']}-ec2-{itype}-{idx+1:02d}",
            resource_type="Instance",
            service_category="Compute",
            service_name="Amazon Elastic Compute Cloud",
            service_subcategory="Compute Instances",
            sku_id=f"EC2-{itype.upper()}",
            sku_meter=f"BoxUsage:{itype}",
            pricing_unit="Hours",
            consumed_unit="Hours",
            list_unit_price=price,
            base_quantity=24.0,
            growth_per_day=0.0,
            sub_account=a,
            region=r,
            az=random.choice(r["azs"]),
            committed=committed,
            commitment_type="SavingsPlan" if idx % 4 != 1 else "Reserved",
            commitment_coverage=0.85 if committed else 0.0,
            weekday_dampening=(a["tags"]["Environment"] != "production"),
        ))

    # --- RDS instances --------------------------------------------------
    rds_specs = [("db.m5.large", 0.171), ("db.r5.xlarge", 0.48)]
    for idx, (itype, price) in enumerate(rds_specs):
        r = region(idx + 1)
        a = acct(idx + 1)
        resources.append(Resource(
            resource_id=f"db-{RNG_SEED:08x}{idx:02x}",
            resource_name=f"{a['name']}-rds-{itype.replace('db.', '')}-{idx+1:02d}",
            resource_type="DB Instance",
            service_category="Databases",
            service_name="Amazon Relational Database Service",
            service_subcategory="Relational Databases",
            sku_id=f"RDS-{itype.upper()}",
            sku_meter=f"InstanceUsage:{itype}",
            pricing_unit="Hours",
            consumed_unit="Hours",
            list_unit_price=price,
            base_quantity=24.0,
            growth_per_day=0.0,
            sub_account=a,
            region=r,
            az=random.choice(r["azs"]),
            committed=(idx == 0),
            commitment_type="Reserved",
            commitment_coverage=1.0 if idx == 0 else 0.0,
            weekday_dampening=False,
        ))

    # --- S3 buckets -------------------------------------------------
    for idx, a in enumerate(SUB_ACCOUNTS):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"arn:aws:s3:::{a['name']}-app-data-{idx+1:02d}",
            resource_name=f"{a['name']}-app-data-{idx+1:02d}",
            resource_type="Bucket",
            service_category="Storage",
            service_name="Amazon Simple Storage Service",
            service_subcategory="Object Storage",
            sku_id="S3-STANDARD-STORAGE",
            sku_meter="TimedStorage-ByteHrs",
            pricing_unit="GB-Month",
            consumed_unit="GB",
            list_unit_price=0.023,
            base_quantity=500.0 + idx * 300,
            growth_per_day=1.8,
            sub_account=a,
            region=r,
            az=None,
            weekday_dampening=False,
        ))

    # --- EBS volumes --------------------------------------------------
    for idx, a in enumerate(SUB_ACCOUNTS[:2]):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"vol-0{RNG_SEED:08x}{idx:02x}",
            resource_name=f"{a['name']}-ebs-gp3-{idx+1:02d}",
            resource_type="Volume",
            service_category="Storage",
            service_name="Amazon Elastic Block Store",
            service_subcategory="Block Storage",
            sku_id="EBS-GP3",
            sku_meter="VolumeUsage.gp3",
            pricing_unit="GB-Month",
            consumed_unit="GB",
            list_unit_price=0.08,
            base_quantity=200.0,
            growth_per_day=0.3,
            sub_account=a,
            region=r,
            az=random.choice(r["azs"]),
            weekday_dampening=False,
        ))

    # --- Lambda functions ------------------------------------------
    for idx, a in enumerate(SUB_ACCOUNTS):
        r = region(idx + 1)
        anomaly_idx = 3 if a["name"] == "acme-data" else None
        resources.append(Resource(
            resource_id=f"arn:aws:lambda:{r['id']}:{a['id']}:function:{a['name']}-etl-{idx+1:02d}",
            resource_name=f"{a['name']}-etl-{idx+1:02d}",
            resource_type="Function",
            service_category="Compute",
            service_name="AWS Lambda",
            service_subcategory="Serverless Compute",
            sku_id="LAMBDA-GB-SECONDS",
            sku_meter="Lambda-GB-Second",
            pricing_unit="GB-Seconds",
            consumed_unit="GB-Seconds",
            list_unit_price=0.0000166667,
            base_quantity=150000.0,
            growth_per_day=800.0,
            sub_account=a,
            region=r,
            az=None,
            weekday_dampening=True,
            anomaly_month_index=anomaly_idx,
            anomaly_multiplier=4.2,
        ))

    # --- DynamoDB tables ----------------------------------------------
    a = SUB_ACCOUNTS[2]
    r = region(0)
    resources.append(Resource(
        resource_id=f"arn:aws:dynamodb:{r['id']}:{a['id']}:table/{a['name']}-events",
        resource_name=f"{a['name']}-events",
        resource_type="Table",
        service_category="Databases",
        service_name="Amazon DynamoDB",
        service_subcategory="NoSQL Databases",
        sku_id="DDB-RRU",
        sku_meter="ReadRequestUnits",
        pricing_unit="Requests",
        consumed_unit="Requests",
        list_unit_price=0.000000625,
        base_quantity=4_000_000.0,
        growth_per_day=60_000.0,
        sub_account=a,
        region=r,
        az=None,
        weekday_dampening=True,
    ))

    # --- CloudFront + Data Transfer ------------------------------------
    a = SUB_ACCOUNTS[0]
    r = region(0)
    resources.append(Resource(
        resource_id=f"arn:aws:cloudfront::{a['id']}:distribution/E1ACMEPROD01",
        resource_name=f"{a['name']}-cdn",
        resource_type="Distribution",
        service_category="Networking",
        service_name="Amazon CloudFront",
        service_subcategory="Content Delivery",
        sku_id="CF-DATA-TRANSFER-OUT",
        sku_meter="DataTransfer-Out-Bytes",
        pricing_unit="GB",
        consumed_unit="GB",
        list_unit_price=0.085,
        base_quantity=800.0,
        growth_per_day=12.0,
        sub_account=a,
        region=r,
        az=None,
        weekday_dampening=False,
    ))
    resources.append(Resource(
        resource_id="",
        resource_name="",
        resource_type="",
        service_category="Networking",
        service_name="AWS Data Transfer",
        service_subcategory="Data Transfer",
        sku_id="DTO-INTER-REGION",
        sku_meter="DataTransfer-Regional-Bytes",
        pricing_unit="GB",
        consumed_unit="GB",
        list_unit_price=0.02,
        base_quantity=300.0,
        growth_per_day=4.0,
        sub_account=SUB_ACCOUNTS[1],
        region=REGIONS[1],
        az=None,
        weekday_dampening=False,
    ))

    return resources


def _daterange(start: date, end: date):
    d = start
    while d < end:
        yield d
        d += timedelta(days=1)


def _billing_period(d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, 1, tzinfo=timezone.utc)
    if d.month == 12:
        end = datetime(d.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(d.year, d.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _month_index(d: date, start: date) -> int:
    return (d.year - start.year) * 12 + (d.month - start.month)


def _round2(x: float) -> float:
    return round(x + 1e-9, 6)


def generate_rows(months_back: int = 6) -> list[dict]:
    resources = _build_resources()

    today = date.today()
    first_of_this_month = today.replace(day=1)
    start = first_of_this_month
    for _ in range(months_back - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    end = today  # exclusive: generate through yesterday

    rows: list[dict] = []

    for d in _daterange(start, end):
        bp_start, bp_end = _billing_period(d)
        cp_start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        cp_end = cp_start + timedelta(days=1)
        is_weekend = d.weekday() >= 5

        for res in resources:
            if not res.resource_id:
                # data-transfer style charge with no specific resource
                pass

            m_idx = _month_index(d, start)
            days_elapsed = (d - start).days
            qty = res.base_quantity + res.growth_per_day * days_elapsed
            qty *= random.uniform(0.94, 1.06)
            if res.weekday_dampening and is_weekend:
                qty *= 0.55
            if res.anomaly_month_index is not None and m_idx == res.anomaly_month_index:
                qty *= res.anomaly_multiplier
            if random.random() < 0.015:
                qty *= random.uniform(1.4, 2.2)  # rare usage spike
            qty = max(qty, 0.0)

            list_unit_price = res.list_unit_price
            list_cost = qty * list_unit_price

            contracted_unit_price = list_unit_price * (1 - ENTERPRISE_DISCOUNT)
            contracted_cost = qty * contracted_unit_price

            covered_qty = qty * res.commitment_coverage if res.committed else 0.0
            uncovered_qty = qty - covered_qty
            effective_cost = (
                covered_qty * contracted_unit_price * (1 - COMMITMENT_DISCOUNT)
                + uncovered_qty * contracted_unit_price
            )

            row = {
                "AllocatedMethodId": None,
                "AllocatedMethodDetails": None,
                "AllocatedResourceId": None,
                "AllocatedResourceName": None,
                "AllocatedTags": None,
                "AvailabilityZone": res.az,
                "RegionId": res.region["id"],
                "RegionName": res.region["name"],
                "BilledCost": _round2(effective_cost),
                "EffectiveCost": _round2(effective_cost),
                "ListCost": _round2(list_cost),
                "ContractedCost": _round2(contracted_cost),
                "BillingAccountId": BILLING_ACCOUNT_ID,
                "BillingAccountName": BILLING_ACCOUNT_NAME,
                "BillingAccountType": "Payer",
                "BillingCurrency": BILLING_CURRENCY,
                "PricingCurrency": BILLING_CURRENCY,
                "PricingCurrencyContractedUnitPrice": _round2(contracted_unit_price),
                "PricingCurrencyEffectiveCost": _round2(effective_cost),
                "PricingCurrencyListUnitPrice": _round2(list_unit_price),
                "BillingPeriodStart": bp_start.isoformat(),
                "BillingPeriodEnd": bp_end.isoformat(),
                "ChargePeriodStart": cp_start.isoformat(),
                "ChargePeriodEnd": cp_end.isoformat(),
                "ChargeCategory": "Usage",
                "ChargeClass": None,
                "ChargeFrequency": "Usage-Based",
                "ChargeDescription": f"{res.service_name} usage - {res.sku_meter}",
                "CapacityReservationId": None,
                "CapacityReservationStatus": None,
                "CommitmentDiscountCategory": "Usage" if res.committed else None,
                "CommitmentDiscountId": f"{res.commitment_type.lower()}-{res.resource_id[-8:]}" if res.committed else None,
                "CommitmentDiscountName": f"{res.commitment_type} coverage for {res.resource_name}" if res.committed else None,
                "CommitmentDiscountQuantity": _round2(covered_qty) if res.committed else None,
                "CommitmentDiscountStatus": "Used" if res.committed else None,
                "CommitmentDiscountType": res.commitment_type or None,
                "CommitmentDiscountUnit": res.pricing_unit if res.committed else None,
                "CommitmentProgramEligibilityDetails": None,
                "ConsumedQuantity": _round2(qty),
                "ConsumedUnit": res.consumed_unit,
                "PricingQuantity": _round2(qty),
                "PricingUnit": res.pricing_unit,
                "ContractApplied": True,
                "ContractedUnitPrice": _round2(contracted_unit_price),
                "HostProviderName": SERVICE_PROVIDER,
                "InvoiceDetailId": None,
                "InvoiceId": None,
                "InvoiceIssuerName": INVOICE_ISSUER,
                "ListUnitPrice": _round2(list_unit_price),
                "PricingCategory": "Committed" if res.committed else "Standard",
                "ResourceId": res.resource_id or None,
                "ResourceName": res.resource_name or None,
                "ResourceType": res.resource_type or None,
                "ServiceProviderName": SERVICE_PROVIDER,
                "ServiceCategory": res.service_category,
                "ServiceName": res.service_name,
                "ServiceSubcategory": res.service_subcategory,
                "SkuId": res.sku_id,
                "SkuMeter": res.sku_meter,
                "SkuPriceDetails": None,
                "SkuPriceId": f"{res.sku_id}-{res.region['id']}",
                "SubAccountId": res.sub_account["id"],
                "SubAccountName": res.sub_account["name"],
                "SubAccountType": "Linked",
                "Tags": res.sub_account["tags"],
            }
            rows.append(row)

    # --- Monthly Support + Tax + occasional credit, per sub-account ---
    m = start
    while m < end:
        bp_start, bp_end = _billing_period(m)
        for a in SUB_ACCOUNTS:
            acct_month_cost = sum(
                r["EffectiveCost"] for r in rows
                if r["SubAccountId"] == a["id"]
                and datetime.fromisoformat(r["ChargePeriodStart"]).date().replace(day=1) == m.replace(day=1)
            )
            support_amount = max(acct_month_cost * 0.03, 29.0)
            tax_amount = acct_month_cost * 0.0

            support_start = bp_start
            support_end = bp_start + timedelta(days=1)
            rows.append({
                **{k: None for k in ALL_FOCUS_IDS},
                "AvailabilityZone": None,
                "BilledCost": _round2(support_amount),
                "EffectiveCost": _round2(support_amount),
                "ListCost": _round2(support_amount),
                "ContractedCost": _round2(support_amount),
                "BillingAccountId": BILLING_ACCOUNT_ID,
                "BillingAccountName": BILLING_ACCOUNT_NAME,
                "BillingAccountType": "Payer",
                "BillingCurrency": BILLING_CURRENCY,
                "BillingPeriodStart": bp_start.isoformat(),
                "BillingPeriodEnd": bp_end.isoformat(),
                "ChargePeriodStart": support_start.isoformat(),
                "ChargePeriodEnd": support_end.isoformat(),
                "ChargeCategory": "Usage",
                "ChargeFrequency": "Recurring",
                "ChargeDescription": "AWS Business Support - monthly fee",
                "ContractApplied": False,
                "HostProviderName": SERVICE_PROVIDER,
                "InvoiceIssuerName": INVOICE_ISSUER,
                "PricingCategory": "Standard",
                "ServiceProviderName": SERVICE_PROVIDER,
                "ServiceCategory": "Other",
                "ServiceName": "AWS Support (Business)",
                "ServiceSubcategory": "Support",
                "SubAccountId": a["id"],
                "SubAccountName": a["name"],
                "SubAccountType": "Linked",
                "Tags": a["tags"],
            })

        total_month_cost = sum(
            r["EffectiveCost"] for r in rows
            if r["BillingAccountId"] == BILLING_ACCOUNT_ID
            and datetime.fromisoformat(r["ChargePeriodStart"]).date().replace(day=1) == m.replace(day=1)
        )
        tax_amount = total_month_cost * 0.0
        # ~7% sales tax on the payer's consolidated bill
        tax_amount = total_month_cost * 0.07
        rows.append({
            **{k: None for k in ALL_FOCUS_IDS},
            "BilledCost": _round2(tax_amount),
            "EffectiveCost": _round2(tax_amount),
            "ListCost": _round2(tax_amount),
            "ContractedCost": _round2(tax_amount),
            "BillingAccountId": BILLING_ACCOUNT_ID,
            "BillingAccountName": BILLING_ACCOUNT_NAME,
            "BillingAccountType": "Payer",
            "BillingCurrency": BILLING_CURRENCY,
            "BillingPeriodStart": bp_start.isoformat(),
            "BillingPeriodEnd": bp_end.isoformat(),
            "ChargePeriodStart": bp_start.isoformat(),
            "ChargePeriodEnd": bp_end.isoformat(),
            "ChargeCategory": "Tax",
            "ChargeFrequency": "Recurring",
            "ChargeDescription": "Estimated sales tax",
            "ContractApplied": False,
            "HostProviderName": SERVICE_PROVIDER,
            "InvoiceIssuerName": INVOICE_ISSUER,
            "ServiceProviderName": SERVICE_PROVIDER,
            "ServiceCategory": "Other",
            "ServiceName": "Tax",
            "BillingAccountType": "Payer",
        })

        # occasional one-off promotional credit
        if random.random() < 0.5:
            credit_amount = -round(random.uniform(50, 400), 2)
            a = random.choice(SUB_ACCOUNTS)
            rows.append({
                **{k: None for k in ALL_FOCUS_IDS},
                "BilledCost": credit_amount,
                "EffectiveCost": credit_amount,
                "ListCost": 0.0,
                "ContractedCost": credit_amount,
                "BillingAccountId": BILLING_ACCOUNT_ID,
                "BillingAccountName": BILLING_ACCOUNT_NAME,
                "BillingAccountType": "Payer",
                "BillingCurrency": BILLING_CURRENCY,
                "BillingPeriodStart": bp_start.isoformat(),
                "BillingPeriodEnd": bp_end.isoformat(),
                "ChargePeriodStart": bp_start.isoformat(),
                "ChargePeriodEnd": bp_end.isoformat(),
                "ChargeCategory": "Credit",
                "ChargeFrequency": "One-Time",
                "ChargeDescription": "Promotional service credit",
                "ContractApplied": False,
                "HostProviderName": SERVICE_PROVIDER,
                "InvoiceIssuerName": INVOICE_ISSUER,
                "ServiceProviderName": SERVICE_PROVIDER,
                "ServiceCategory": "Other",
                "ServiceName": "Credit",
                "SubAccountId": a["id"],
                "SubAccountName": a["name"],
                "SubAccountType": "Linked",
                "Tags": a["tags"],
            })

        m = (m.replace(day=1) + timedelta(days=32)).replace(day=1)

    return rows


def write_sample_csv(months_back: int = 6, out_path: Path | None = None) -> Path:
    rows = generate_rows(months_back=months_back)
    df = pd.DataFrame(rows)

    for col in ALL_FOCUS_IDS:
        if col not in df.columns:
            df[col] = None
    df = df[ALL_FOCUS_IDS]

    # Serialize dict-valued columns (Tags etc.) to compact JSON strings.
    for col in df.columns:
        df[col] = df[col].apply(lambda v: json.dumps(v) if isinstance(v, dict) else v)

    df = df.sort_values(["ChargePeriodStart", "ServiceCategory", "ResourceId"], na_position="last")

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        first_period = df["BillingPeriodStart"].min()[:7]
        last_period = df["BillingPeriodStart"].max()[:7]
        out_path = SAMPLES_DIR / f"focus_sample_{first_period}_to_{last_period}.csv"

    df.to_csv(out_path, index=False)
    return out_path


if __name__ == "__main__":
    path = write_sample_csv()
    print(f"Wrote {path}")
