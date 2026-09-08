"""Generates a realistic, synthetic multi-cloud (AWS + Azure + GCP)
cost/usage dataset in FOCUS v1.4 CSV format
(data/samples/focus_sample_<start>_<end>.csv).

This is sample data for exercising the ingest + report pipeline, not a
real billing export. It models a small organization running workloads
across all three major clouds -- one billing account per provider (AWS
payer account / Azure EA billing account / GCP billing account), with
several linked accounts (AWS sub-accounts, Azure subscriptions, GCP
projects) each running provider-native compute/storage/database/
networking services, over the last N months, with:
  - daily usage-based charges per resource (with weekday/weekend and
    growth patterns, plus one deliberate cost anomaly per provider to
    make trend reports interesting),
  - a flat enterprise "contracted" discount applied to (almost)
    everything (List Cost -> Contracted Cost), to demonstrate FOCUS's
    List/Contracted/Effective cost distinction,
  - a subset of compute/database resources covered by commitment
    discounts (Savings Plans/Reserved Instances on AWS, Reserved VM
    Instances on Azure, Committed Use Discounts on GCP), which populate
    the Commitment Discount * columns and further reduce Effective Cost,
  - monthly provider support and tax/VAT charges per account,
  - a couple of one-off promotional credits.

Each linked account is tagged (FOCUS `Tags` column) with Environment,
Team, CostCenter, and -- since FOCUS has no dedicated columns for
these -- **Application** and **Owner**, following the same
Tags-based-extension pattern as the rest of the tag set. Several
applications deliberately span more than one cloud provider (e.g.
"data-platform" runs on AWS, Azure and GCP) to make cross-cloud,
per-application/per-owner cost views meaningful in the dashboard.
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
    commitment_type: str = ""       # e.g. "SavingsPlan" | "Reserved" | "CUD"
    commitment_coverage: float = 0.0  # fraction of usage covered
    weekday_dampening: bool = True   # lower usage on weekends (prod web-style workload)
    anomaly_month_index: int | None = None
    anomaly_multiplier: float = 1.0


@dataclass
class CloudProfile:
    key: str                      # "aws" | "azure" | "gcp"
    service_provider_name: str    # FOCUS ServiceProviderName / HostProviderName
    invoice_issuer: str
    billing_account_id: str
    billing_account_name: str
    billing_currency: str
    sub_accounts: list[dict]
    resources: list[Resource]
    enterprise_discount: float    # flat negotiated discount: List -> Contracted
    commitment_discount: float    # additional discount for committed-usage coverage
    tax_rate: float
    support_service_name: str
    support_description: str
    tax_description: str
    sub_account_type: str         # "Linked" | "Subscription" | "Project"


# --- Applications -----------------------------------------------------
# Shared application/owner catalog. An application can span multiple
# cloud providers (e.g. a primary region on AWS, a DR region on GCP) --
# that's deliberate, so "group by Application" in the dashboard shows
# genuinely cross-cloud spend.
APPLICATIONS = {
    "checkout-service": "Priya Nair",
    "data-platform": "Marcus Chen",
    "mobile-backend": "Diego Fernandez",
    "internal-tools": "Sara Ahmed",
}


def _tags(env: str, team: str, cost_center: str, application: str) -> dict:
    return {
        "Environment": env,
        "Team": team,
        "CostCenter": cost_center,
        "Application": application,
        "Owner": APPLICATIONS[application],
    }


# --- AWS ----------------------------------------------------------------
AWS_BILLING_ACCOUNT_ID = "111111111111"
AWS_BILLING_ACCOUNT_NAME = "acme-payer"

AWS_SUB_ACCOUNTS = [
    {"id": "222222222222", "name": "acme-prod", "type": "Linked",
     "tags": _tags("production", "platform-eng", "CC-1001", "checkout-service")},
    {"id": "333333333333", "name": "acme-staging", "type": "Linked",
     "tags": _tags("staging", "platform-eng", "CC-1001", "checkout-service")},
    {"id": "444444444444", "name": "acme-data", "type": "Linked",
     "tags": _tags("production", "data-eng", "CC-2044", "data-platform")},
]

AWS_REGIONS = [
    {"id": "us-east-1", "name": "US East (N. Virginia)", "azs": ["us-east-1a", "us-east-1b", "us-east-1c"]},
    {"id": "us-west-2", "name": "US West (Oregon)", "azs": ["us-west-2a", "us-west-2b"]},
    {"id": "eu-west-1", "name": "Europe (Ireland)", "azs": ["eu-west-1a", "eu-west-1b"]},
]


# --- Azure ----------------------------------------------------------------
AZURE_BILLING_ACCOUNT_ID = "8f14e45f-ceea-467e-adde-9a2c00000000"
AZURE_BILLING_ACCOUNT_NAME = "Acme Corp (EA)"

AZURE_SUB_ACCOUNTS = [
    {"id": "8f14e45f-ceea-467e-adde-9a2c000001", "name": "acme-azure-prod", "type": "Subscription",
     "tags": _tags("production", "mobile-eng", "CC-3050", "mobile-backend")},
    {"id": "8f14e45f-ceea-467e-adde-9a2c000002", "name": "acme-azure-staging", "type": "Subscription",
     "tags": _tags("staging", "mobile-eng", "CC-3050", "mobile-backend")},
    {"id": "8f14e45f-ceea-467e-adde-9a2c000003", "name": "acme-azure-analytics", "type": "Subscription",
     "tags": _tags("production", "data-eng", "CC-2044", "data-platform")},
]

AZURE_REGIONS = [
    {"id": "eastus", "name": "East US", "azs": ["1", "2", "3"]},
    {"id": "westeurope", "name": "West Europe", "azs": ["1", "2"]},
    {"id": "southeastasia", "name": "Southeast Asia", "azs": ["1", "2"]},
]


# --- GCP ----------------------------------------------------------------
GCP_BILLING_ACCOUNT_ID = "012345-6789AB-CDEF01"
GCP_BILLING_ACCOUNT_NAME = "Acme Corp GCP Billing"

GCP_SUB_ACCOUNTS = [
    {"id": "acme-gcp-prod-a1b2c3", "name": "acme-gcp-prod", "type": "Project",
     "tags": _tags("production", "platform-eng", "CC-1001", "checkout-service")},
    {"id": "acme-gcp-tools-d4e5f6", "name": "acme-gcp-tools", "type": "Project",
     "tags": _tags("production", "platform-eng", "CC-1005", "internal-tools")},
    {"id": "acme-gcp-ml-g7h8i9", "name": "acme-gcp-ml", "type": "Project",
     "tags": _tags("production", "data-eng", "CC-2044", "data-platform")},
]

GCP_REGIONS = [
    {"id": "us-central1", "name": "us-central1 (Iowa)", "azs": ["us-central1-a", "us-central1-b", "us-central1-c"]},
    {"id": "europe-west1", "name": "europe-west1 (Belgium)", "azs": ["europe-west1-b", "europe-west1-c"]},
    {"id": "asia-southeast1", "name": "asia-southeast1 (Singapore)", "azs": ["asia-southeast1-a", "asia-southeast1-b"]},
]


def _build_resources_aws(sub_accounts: list[dict], regions: list[dict]) -> list[Resource]:
    resources: list[Resource] = []

    def acct(i: int) -> dict:
        return sub_accounts[i % len(sub_accounts)]

    def region(i: int) -> dict:
        return regions[i % len(regions)]

    ec2_specs = [
        ("t3.medium", 0.0416), ("m5.large", 0.096), ("m5.xlarge", 0.192),
        ("c5.2xlarge", 0.34), ("r5.large", 0.126),
    ]
    for idx, (itype, price) in enumerate(ec2_specs):
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
            pricing_unit="Hours", consumed_unit="Hours",
            list_unit_price=price, base_quantity=24.0, growth_per_day=0.0,
            sub_account=a, region=r, az=random.choice(r["azs"]),
            committed=committed,
            commitment_type="SavingsPlan" if idx % 4 != 1 else "Reserved",
            commitment_coverage=0.85 if committed else 0.0,
            weekday_dampening=(a["tags"]["Environment"] != "production"),
        ))

    rds_specs = [("db.m5.large", 0.171), ("db.r5.xlarge", 0.48)]
    for idx, (itype, price) in enumerate(rds_specs):
        r = region(idx + 1)
        a = acct(idx + 1)
        resources.append(Resource(
            resource_id=f"db-{RNG_SEED:08x}{idx:02x}",
            resource_name=f"{a['name']}-rds-{itype.replace('db.', '')}-{idx+1:02d}",
            resource_type="DB Instance", service_category="Databases",
            service_name="Amazon Relational Database Service",
            service_subcategory="Relational Databases",
            sku_id=f"RDS-{itype.upper()}", sku_meter=f"InstanceUsage:{itype}",
            pricing_unit="Hours", consumed_unit="Hours",
            list_unit_price=price, base_quantity=24.0, growth_per_day=0.0,
            sub_account=a, region=r, az=random.choice(r["azs"]),
            committed=(idx == 0), commitment_type="Reserved",
            commitment_coverage=1.0 if idx == 0 else 0.0, weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"arn:aws:s3:::{a['name']}-app-data-{idx+1:02d}",
            resource_name=f"{a['name']}-app-data-{idx+1:02d}",
            resource_type="Bucket", service_category="Storage",
            service_name="Amazon Simple Storage Service", service_subcategory="Object Storage",
            sku_id="S3-STANDARD-STORAGE", sku_meter="TimedStorage-ByteHrs",
            pricing_unit="GB-Month", consumed_unit="GB",
            list_unit_price=0.023, base_quantity=500.0 + idx * 300, growth_per_day=1.8,
            sub_account=a, region=r, az=None, weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts[:2]):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"vol-0{RNG_SEED:08x}{idx:02x}",
            resource_name=f"{a['name']}-ebs-gp3-{idx+1:02d}",
            resource_type="Volume", service_category="Storage",
            service_name="Amazon Elastic Block Store", service_subcategory="Block Storage",
            sku_id="EBS-GP3", sku_meter="VolumeUsage.gp3",
            pricing_unit="GB-Month", consumed_unit="GB",
            list_unit_price=0.08, base_quantity=200.0, growth_per_day=0.3,
            sub_account=a, region=r, az=random.choice(r["azs"]), weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts):
        r = region(idx + 1)
        anomaly_idx = 3 if a["name"] == "acme-data" else None
        resources.append(Resource(
            resource_id=f"arn:aws:lambda:{r['id']}:{a['id']}:function:{a['name']}-etl-{idx+1:02d}",
            resource_name=f"{a['name']}-etl-{idx+1:02d}",
            resource_type="Function", service_category="Compute",
            service_name="AWS Lambda", service_subcategory="Serverless Compute",
            sku_id="LAMBDA-GB-SECONDS", sku_meter="Lambda-GB-Second",
            pricing_unit="GB-Seconds", consumed_unit="GB-Seconds",
            list_unit_price=0.0000166667, base_quantity=150000.0, growth_per_day=800.0,
            sub_account=a, region=r, az=None, weekday_dampening=True,
            anomaly_month_index=anomaly_idx, anomaly_multiplier=4.2,
        ))

    a = sub_accounts[2]
    r = region(0)
    resources.append(Resource(
        resource_id=f"arn:aws:dynamodb:{r['id']}:{a['id']}:table/{a['name']}-events",
        resource_name=f"{a['name']}-events",
        resource_type="Table", service_category="Databases",
        service_name="Amazon DynamoDB", service_subcategory="NoSQL Databases",
        sku_id="DDB-RRU", sku_meter="ReadRequestUnits",
        pricing_unit="Requests", consumed_unit="Requests",
        list_unit_price=0.000000625, base_quantity=4_000_000.0, growth_per_day=60_000.0,
        sub_account=a, region=r, az=None, weekday_dampening=True,
    ))

    a = sub_accounts[0]
    r = region(0)
    resources.append(Resource(
        resource_id=f"arn:aws:cloudfront::{a['id']}:distribution/E1ACMEPROD01",
        resource_name=f"{a['name']}-cdn",
        resource_type="Distribution", service_category="Networking",
        service_name="Amazon CloudFront", service_subcategory="Content Delivery",
        sku_id="CF-DATA-TRANSFER-OUT", sku_meter="DataTransfer-Out-Bytes",
        pricing_unit="GB", consumed_unit="GB",
        list_unit_price=0.085, base_quantity=800.0, growth_per_day=12.0,
        sub_account=a, region=r, az=None, weekday_dampening=False,
    ))
    resources.append(Resource(
        resource_id="", resource_name="", resource_type="",
        service_category="Networking", service_name="AWS Data Transfer",
        service_subcategory="Data Transfer",
        sku_id="DTO-INTER-REGION", sku_meter="DataTransfer-Regional-Bytes",
        pricing_unit="GB", consumed_unit="GB",
        list_unit_price=0.02, base_quantity=300.0, growth_per_day=4.0,
        sub_account=sub_accounts[1], region=regions[1], az=None, weekday_dampening=False,
    ))

    return resources


def _build_resources_azure(sub_accounts: list[dict], regions: list[dict]) -> list[Resource]:
    resources: list[Resource] = []

    def acct(i: int) -> dict:
        return sub_accounts[i % len(sub_accounts)]

    def region(i: int) -> dict:
        return regions[i % len(regions)]

    vm_specs = [
        ("Standard_B2s", 0.0416), ("Standard_D2s_v3", 0.096), ("Standard_D4s_v3", 0.192),
        ("Standard_F8s_v2", 0.34), ("Standard_E4s_v3", 0.126),
    ]
    for idx, (vmsize, price) in enumerate(vm_specs):
        r = region(idx)
        a = acct(idx)
        committed = idx % 2 == 0
        resources.append(Resource(
            resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.Compute/virtualMachines/{a['name']}-vm-{idx+1:02d}",
            resource_name=f"{a['name']}-vm-{vmsize.lower()}-{idx+1:02d}",
            resource_type="Virtual Machine", service_category="Compute",
            service_name="Azure Virtual Machines", service_subcategory="Compute Instances",
            sku_id=f"VM-{vmsize.upper()}", sku_meter=f"{vmsize} Compute Hours",
            pricing_unit="Hours", consumed_unit="Hours",
            list_unit_price=price, base_quantity=24.0, growth_per_day=0.0,
            sub_account=a, region=r, az=random.choice(r["azs"]),
            committed=committed,
            commitment_type="Reserved" if idx % 4 != 1 else "SavingsPlan",
            commitment_coverage=0.80 if committed else 0.0,
            weekday_dampening=(a["tags"]["Environment"] != "production"),
        ))

    sql_specs = [("GP_Gen5_2", 0.174), ("GP_Gen5_4", 0.485)]
    for idx, (tier, price) in enumerate(sql_specs):
        r = region(idx + 1)
        a = acct(idx + 1)
        resources.append(Resource(
            resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.Sql/servers/{a['name']}-sql/databases/{a['name']}-db-{idx+1:02d}",
            resource_name=f"{a['name']}-sql-{tier.lower()}-{idx+1:02d}",
            resource_type="SQL Database", service_category="Databases",
            service_name="Azure SQL Database", service_subcategory="Relational Databases",
            sku_id=f"SQLDB-{tier.upper()}", sku_meter=f"{tier} vCore Hours",
            pricing_unit="Hours", consumed_unit="Hours",
            list_unit_price=price, base_quantity=24.0, growth_per_day=0.0,
            sub_account=a, region=r, az=random.choice(r["azs"]),
            committed=(idx == 0), commitment_type="Reserved",
            commitment_coverage=1.0 if idx == 0 else 0.0, weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.Storage/storageAccounts/{a['name']}data{idx+1:02d}",
            resource_name=f"{a['name']}-blob-{idx+1:02d}",
            resource_type="Storage Account", service_category="Storage",
            service_name="Azure Blob Storage", service_subcategory="Object Storage",
            sku_id="BLOB-HOT-STORAGE", sku_meter="Hot LRS Data Stored",
            pricing_unit="GB-Month", consumed_unit="GB",
            list_unit_price=0.0184, base_quantity=500.0 + idx * 300, growth_per_day=1.8,
            sub_account=a, region=r, az=None, weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts[:2]):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.Compute/disks/{a['name']}-disk-{idx+1:02d}",
            resource_name=f"{a['name']}-premium-ssd-{idx+1:02d}",
            resource_type="Managed Disk", service_category="Storage",
            service_name="Azure Managed Disks", service_subcategory="Block Storage",
            sku_id="DISK-PREMIUM-SSD", sku_meter="P10 Premium SSD Managed Disk",
            pricing_unit="GB-Month", consumed_unit="GB",
            list_unit_price=0.135, base_quantity=200.0, growth_per_day=0.3,
            sub_account=a, region=r, az=random.choice(r["azs"]), weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts):
        r = region(idx + 1)
        anomaly_idx = 3 if a["name"] == "acme-azure-analytics" else None
        resources.append(Resource(
            resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.Web/sites/{a['name']}-func-{idx+1:02d}",
            resource_name=f"{a['name']}-func-{idx+1:02d}",
            resource_type="Function App", service_category="Compute",
            service_name="Azure Functions", service_subcategory="Serverless Compute",
            sku_id="FUNC-CONSUMPTION-GBS", sku_meter="Execution GB-Seconds",
            pricing_unit="GB-Seconds", consumed_unit="GB-Seconds",
            list_unit_price=0.000016, base_quantity=150000.0, growth_per_day=800.0,
            sub_account=a, region=r, az=None, weekday_dampening=True,
            anomaly_month_index=anomaly_idx, anomaly_multiplier=4.2,
        ))

    a = sub_accounts[2]
    r = region(0)
    resources.append(Resource(
        resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.DocumentDB/databaseAccounts/{a['name']}-cosmos",
        resource_name=f"{a['name']}-cosmos",
        resource_type="Cosmos DB Account", service_category="Databases",
        service_name="Azure Cosmos DB", service_subcategory="NoSQL Databases",
        sku_id="COSMOS-RU", sku_meter="Request Units",
        pricing_unit="Request Units", consumed_unit="Request Units",
        list_unit_price=0.0000006, base_quantity=4_000_000.0, growth_per_day=60_000.0,
        sub_account=a, region=r, az=None, weekday_dampening=True,
    ))

    a = sub_accounts[0]
    r = region(0)
    resources.append(Resource(
        resource_id=f"/subscriptions/{a['id']}/resourceGroups/{a['name']}-rg/providers/Microsoft.Cdn/profiles/{a['name']}-cdn",
        resource_name=f"{a['name']}-cdn",
        resource_type="CDN Profile", service_category="Networking",
        service_name="Azure Content Delivery Network", service_subcategory="Content Delivery",
        sku_id="CDN-DATA-TRANSFER-OUT", sku_meter="Data Transfer Out",
        pricing_unit="GB", consumed_unit="GB",
        list_unit_price=0.081, base_quantity=800.0, growth_per_day=12.0,
        sub_account=a, region=r, az=None, weekday_dampening=False,
    ))
    resources.append(Resource(
        resource_id="", resource_name="", resource_type="",
        service_category="Networking", service_name="Azure Bandwidth",
        service_subcategory="Data Transfer",
        sku_id="BW-INTER-REGION", sku_meter="Inter-Region Data Transfer",
        pricing_unit="GB", consumed_unit="GB",
        list_unit_price=0.02, base_quantity=300.0, growth_per_day=4.0,
        sub_account=sub_accounts[1], region=regions[1], az=None, weekday_dampening=False,
    ))

    return resources


def _build_resources_gcp(sub_accounts: list[dict], regions: list[dict]) -> list[Resource]:
    resources: list[Resource] = []

    def acct(i: int) -> dict:
        return sub_accounts[i % len(sub_accounts)]

    def region(i: int) -> dict:
        return regions[i % len(regions)]

    vm_specs = [
        ("e2-medium", 0.0416), ("n2-standard-2", 0.097), ("n2-standard-4", 0.194),
        ("c2-standard-8", 0.34), ("n2-highmem-2", 0.128),
    ]
    for idx, (mtype, price) in enumerate(vm_specs):
        r = region(idx)
        a = acct(idx)
        committed = idx % 2 == 0
        resources.append(Resource(
            resource_id=f"projects/{a['id']}/zones/{random.choice(r['azs'])}/instances/{a['name']}-vm-{idx+1:02d}",
            resource_name=f"{a['name']}-gce-{mtype}-{idx+1:02d}",
            resource_type="Instance", service_category="Compute",
            service_name="Compute Engine", service_subcategory="Compute Instances",
            sku_id=f"GCE-{mtype.upper()}", sku_meter=f"{mtype} instance hour",
            pricing_unit="Hours", consumed_unit="Hours",
            list_unit_price=price, base_quantity=24.0, growth_per_day=0.0,
            sub_account=a, region=r, az=random.choice(r["azs"]),
            committed=committed,
            commitment_type="CUD" if idx % 4 != 1 else "CUD-3yr",
            commitment_coverage=0.75 if committed else 0.0,
            weekday_dampening=(a["tags"]["Environment"] != "production"),
        ))

    sql_specs = [("db-custom-2-7680", 0.170), ("db-custom-4-15360", 0.475)]
    for idx, (tier, price) in enumerate(sql_specs):
        r = region(idx + 1)
        a = acct(idx + 1)
        resources.append(Resource(
            resource_id=f"projects/{a['id']}/instances/{a['name']}-cloudsql-{idx+1:02d}",
            resource_name=f"{a['name']}-cloudsql-{tier}-{idx+1:02d}",
            resource_type="Cloud SQL Instance", service_category="Databases",
            service_name="Cloud SQL for PostgreSQL", service_subcategory="Relational Databases",
            sku_id=f"CLOUDSQL-{tier.upper()}", sku_meter=f"{tier} instance hour",
            pricing_unit="Hours", consumed_unit="Hours",
            list_unit_price=price, base_quantity=24.0, growth_per_day=0.0,
            sub_account=a, region=r, az=random.choice(r["azs"]),
            committed=(idx == 0), commitment_type="CUD",
            commitment_coverage=1.0 if idx == 0 else 0.0, weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"projects/{a['id']}/buckets/{a['name']}-app-data-{idx+1:02d}",
            resource_name=f"{a['name']}-gcs-{idx+1:02d}",
            resource_type="Bucket", service_category="Storage",
            service_name="Cloud Storage", service_subcategory="Object Storage",
            sku_id="GCS-STANDARD-STORAGE", sku_meter="Standard Storage US",
            pricing_unit="GB-Month", consumed_unit="GB",
            list_unit_price=0.020, base_quantity=500.0 + idx * 300, growth_per_day=1.8,
            sub_account=a, region=r, az=None, weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts[:2]):
        r = region(idx)
        resources.append(Resource(
            resource_id=f"projects/{a['id']}/zones/{random.choice(r['azs'])}/disks/{a['name']}-pd-ssd-{idx+1:02d}",
            resource_name=f"{a['name']}-pd-ssd-{idx+1:02d}",
            resource_type="Persistent Disk", service_category="Storage",
            service_name="Persistent Disk", service_subcategory="Block Storage",
            sku_id="PD-SSD", sku_meter="SSD backed PD Capacity",
            pricing_unit="GB-Month", consumed_unit="GB",
            list_unit_price=0.17, base_quantity=200.0, growth_per_day=0.3,
            sub_account=a, region=r, az=random.choice(r["azs"]), weekday_dampening=False,
        ))

    for idx, a in enumerate(sub_accounts):
        r = region(idx + 1)
        anomaly_idx = 3 if a["name"] == "acme-gcp-ml" else None
        resources.append(Resource(
            resource_id=f"projects/{a['id']}/locations/{r['id']}/functions/{a['name']}-func-{idx+1:02d}",
            resource_name=f"{a['name']}-cf-{idx+1:02d}",
            resource_type="Cloud Function", service_category="Compute",
            service_name="Cloud Functions", service_subcategory="Serverless Compute",
            sku_id="CF-GB-SECONDS", sku_meter="Invocation GB-Second",
            pricing_unit="GB-Seconds", consumed_unit="GB-Seconds",
            list_unit_price=0.0000025, base_quantity=150000.0, growth_per_day=800.0,
            sub_account=a, region=r, az=None, weekday_dampening=True,
            anomaly_month_index=anomaly_idx, anomaly_multiplier=4.2,
        ))

    a = sub_accounts[2]
    r = region(0)
    resources.append(Resource(
        resource_id=f"projects/{a['id']}/datasets/{a['name']}_analytics",
        resource_name=f"{a['name']}-bigquery",
        resource_type="Dataset", service_category="Analytics",
        service_name="BigQuery", service_subcategory="Data Warehousing",
        sku_id="BQ-ACTIVE-STORAGE", sku_meter="Active Logical Storage",
        pricing_unit="GB-Month", consumed_unit="GB",
        list_unit_price=0.02, base_quantity=2000.0, growth_per_day=25.0,
        sub_account=a, region=r, az=None, weekday_dampening=True,
    ))

    a = sub_accounts[0]
    r = region(0)
    resources.append(Resource(
        resource_id=f"projects/{a['id']}/cdnPolicies/{a['name']}-cdn",
        resource_name=f"{a['name']}-cloud-cdn",
        resource_type="CDN Policy", service_category="Networking",
        service_name="Cloud CDN", service_subcategory="Content Delivery",
        sku_id="CDN-DATA-TRANSFER-OUT", sku_meter="Cache Egress",
        pricing_unit="GB", consumed_unit="GB",
        list_unit_price=0.08, base_quantity=800.0, growth_per_day=12.0,
        sub_account=a, region=r, az=None, weekday_dampening=False,
    ))
    resources.append(Resource(
        resource_id="", resource_name="", resource_type="",
        service_category="Networking", service_name="Network Egress",
        service_subcategory="Data Transfer",
        sku_id="EGRESS-INTER-REGION", sku_meter="Inter-Region Egress",
        pricing_unit="GB", consumed_unit="GB",
        list_unit_price=0.02, base_quantity=300.0, growth_per_day=4.0,
        sub_account=sub_accounts[1], region=regions[1], az=None, weekday_dampening=False,
    ))

    return resources


def _build_profiles() -> list[CloudProfile]:
    return [
        CloudProfile(
            key="aws", service_provider_name="AWS",
            invoice_issuer="Amazon Web Services, Inc.",
            billing_account_id=AWS_BILLING_ACCOUNT_ID, billing_account_name=AWS_BILLING_ACCOUNT_NAME,
            billing_currency="USD", sub_accounts=AWS_SUB_ACCOUNTS,
            resources=_build_resources_aws(AWS_SUB_ACCOUNTS, AWS_REGIONS),
            enterprise_discount=0.03, commitment_discount=0.32, tax_rate=0.07,
            support_service_name="AWS Support (Business)",
            support_description="AWS Business Support - monthly fee",
            tax_description="Estimated sales tax",
            sub_account_type="Linked",
        ),
        CloudProfile(
            key="azure", service_provider_name="Azure",
            invoice_issuer="Microsoft Corporation",
            billing_account_id=AZURE_BILLING_ACCOUNT_ID, billing_account_name=AZURE_BILLING_ACCOUNT_NAME,
            billing_currency="USD", sub_accounts=AZURE_SUB_ACCOUNTS,
            resources=_build_resources_azure(AZURE_SUB_ACCOUNTS, AZURE_REGIONS),
            enterprise_discount=0.05, commitment_discount=0.28, tax_rate=0.20,
            support_service_name="Azure Support Plan (Standard)",
            support_description="Azure Standard Support - monthly fee",
            tax_description="Estimated VAT",
            sub_account_type="Subscription",
        ),
        CloudProfile(
            key="gcp", service_provider_name="GCP",
            invoice_issuer="Google LLC",
            billing_account_id=GCP_BILLING_ACCOUNT_ID, billing_account_name=GCP_BILLING_ACCOUNT_NAME,
            billing_currency="USD", sub_accounts=GCP_SUB_ACCOUNTS,
            resources=_build_resources_gcp(GCP_SUB_ACCOUNTS, GCP_REGIONS),
            enterprise_discount=0.02, commitment_discount=0.25, tax_rate=0.09,
            support_service_name="Google Cloud Support (Standard)",
            support_description="Google Cloud Standard Support - monthly fee",
            tax_description="Estimated GST",
            sub_account_type="Project",
        ),
    ]


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


def _generate_provider_rows(profile: CloudProfile, start: date, end: date) -> list[dict]:
    rows: list[dict] = []

    for d in _daterange(start, end):
        bp_start, bp_end = _billing_period(d)
        cp_start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        cp_end = cp_start + timedelta(days=1)
        is_weekend = d.weekday() >= 5

        for res in profile.resources:
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

            contracted_unit_price = list_unit_price * (1 - profile.enterprise_discount)
            contracted_cost = qty * contracted_unit_price

            covered_qty = qty * res.commitment_coverage if res.committed else 0.0
            uncovered_qty = qty - covered_qty
            effective_cost = (
                covered_qty * contracted_unit_price * (1 - profile.commitment_discount)
                + uncovered_qty * contracted_unit_price
            )

            rows.append({
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
                "BillingAccountId": profile.billing_account_id,
                "BillingAccountName": profile.billing_account_name,
                "BillingAccountType": "Payer",
                "BillingCurrency": profile.billing_currency,
                "PricingCurrency": profile.billing_currency,
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
                "HostProviderName": profile.service_provider_name,
                "InvoiceDetailId": None,
                "InvoiceId": None,
                "InvoiceIssuerName": profile.invoice_issuer,
                "ListUnitPrice": _round2(list_unit_price),
                "PricingCategory": "Committed" if res.committed else "Standard",
                "ResourceId": res.resource_id or None,
                "ResourceName": res.resource_name or None,
                "ResourceType": res.resource_type or None,
                "ServiceProviderName": profile.service_provider_name,
                "ServiceCategory": res.service_category,
                "ServiceName": res.service_name,
                "ServiceSubcategory": res.service_subcategory,
                "SkuId": res.sku_id,
                "SkuMeter": res.sku_meter,
                "SkuPriceDetails": None,
                "SkuPriceId": f"{res.sku_id}-{res.region['id']}",
                "SubAccountId": res.sub_account["id"],
                "SubAccountName": res.sub_account["name"],
                "SubAccountType": profile.sub_account_type,
                "Tags": res.sub_account["tags"],
            })

    # --- Monthly Support + Tax + occasional credit, per sub-account ---
    m = start
    while m < end:
        bp_start, bp_end = _billing_period(m)
        for a in profile.sub_accounts:
            acct_month_cost = sum(
                r["EffectiveCost"] for r in rows
                if r["SubAccountId"] == a["id"]
                and datetime.fromisoformat(r["ChargePeriodStart"]).date().replace(day=1) == m.replace(day=1)
            )
            support_amount = max(acct_month_cost * 0.03, 29.0)

            support_start = bp_start
            support_end = bp_start + timedelta(days=1)
            rows.append({
                **{k: None for k in ALL_FOCUS_IDS},
                "AvailabilityZone": None,
                "BilledCost": _round2(support_amount),
                "EffectiveCost": _round2(support_amount),
                "ListCost": _round2(support_amount),
                "ContractedCost": _round2(support_amount),
                "BillingAccountId": profile.billing_account_id,
                "BillingAccountName": profile.billing_account_name,
                "BillingAccountType": "Payer",
                "BillingCurrency": profile.billing_currency,
                "BillingPeriodStart": bp_start.isoformat(),
                "BillingPeriodEnd": bp_end.isoformat(),
                "ChargePeriodStart": support_start.isoformat(),
                "ChargePeriodEnd": support_end.isoformat(),
                "ChargeCategory": "Usage",
                "ChargeFrequency": "Recurring",
                "ChargeDescription": profile.support_description,
                "ContractApplied": False,
                "HostProviderName": profile.service_provider_name,
                "InvoiceIssuerName": profile.invoice_issuer,
                "PricingCategory": "Standard",
                "ServiceProviderName": profile.service_provider_name,
                "ServiceCategory": "Other",
                "ServiceName": profile.support_service_name,
                "ServiceSubcategory": "Support",
                "SubAccountId": a["id"],
                "SubAccountName": a["name"],
                "SubAccountType": profile.sub_account_type,
                "Tags": a["tags"],
            })

        total_month_cost = sum(
            r["EffectiveCost"] for r in rows
            if r["BillingAccountId"] == profile.billing_account_id
            and datetime.fromisoformat(r["ChargePeriodStart"]).date().replace(day=1) == m.replace(day=1)
        )
        tax_amount = total_month_cost * profile.tax_rate
        rows.append({
            **{k: None for k in ALL_FOCUS_IDS},
            "BilledCost": _round2(tax_amount),
            "EffectiveCost": _round2(tax_amount),
            "ListCost": _round2(tax_amount),
            "ContractedCost": _round2(tax_amount),
            "BillingAccountId": profile.billing_account_id,
            "BillingAccountName": profile.billing_account_name,
            "BillingAccountType": "Payer",
            "BillingCurrency": profile.billing_currency,
            "BillingPeriodStart": bp_start.isoformat(),
            "BillingPeriodEnd": bp_end.isoformat(),
            "ChargePeriodStart": bp_start.isoformat(),
            "ChargePeriodEnd": bp_end.isoformat(),
            "ChargeCategory": "Tax",
            "ChargeFrequency": "Recurring",
            "ChargeDescription": profile.tax_description,
            "ContractApplied": False,
            "HostProviderName": profile.service_provider_name,
            "InvoiceIssuerName": profile.invoice_issuer,
            "ServiceProviderName": profile.service_provider_name,
            "ServiceCategory": "Other",
            "ServiceName": "Tax",
        })

        # occasional one-off promotional credit
        if random.random() < 0.5:
            credit_amount = -round(random.uniform(50, 400), 2)
            a = random.choice(profile.sub_accounts)
            rows.append({
                **{k: None for k in ALL_FOCUS_IDS},
                "BilledCost": credit_amount,
                "EffectiveCost": credit_amount,
                "ListCost": 0.0,
                "ContractedCost": credit_amount,
                "BillingAccountId": profile.billing_account_id,
                "BillingAccountName": profile.billing_account_name,
                "BillingAccountType": "Payer",
                "BillingCurrency": profile.billing_currency,
                "BillingPeriodStart": bp_start.isoformat(),
                "BillingPeriodEnd": bp_end.isoformat(),
                "ChargePeriodStart": bp_start.isoformat(),
                "ChargePeriodEnd": bp_end.isoformat(),
                "ChargeCategory": "Credit",
                "ChargeFrequency": "One-Time",
                "ChargeDescription": "Promotional service credit",
                "ContractApplied": False,
                "HostProviderName": profile.service_provider_name,
                "InvoiceIssuerName": profile.invoice_issuer,
                "ServiceProviderName": profile.service_provider_name,
                "ServiceCategory": "Other",
                "ServiceName": "Credit",
                "SubAccountId": a["id"],
                "SubAccountName": a["name"],
                "SubAccountType": profile.sub_account_type,
                "Tags": a["tags"],
            })

        m = (m.replace(day=1) + timedelta(days=32)).replace(day=1)

    return rows


def generate_rows(months_back: int = 6) -> list[dict]:
    today = date.today()
    first_of_this_month = today.replace(day=1)
    start = first_of_this_month
    for _ in range(months_back - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    end = today  # exclusive: generate through yesterday

    rows: list[dict] = []
    for profile in _build_profiles():
        rows.extend(_generate_provider_rows(profile, start, end))
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

    df = df.sort_values(["ChargePeriodStart", "ServiceProviderName", "ServiceCategory", "ResourceId"], na_position="last")

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
