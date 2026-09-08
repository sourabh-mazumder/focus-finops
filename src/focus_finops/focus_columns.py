"""Single source of truth mapping FOCUS v1.4 column IDs <-> DB columns.

Used by both the sample data generator (writes CSVs with official FOCUS
column headers) and the ingest script (maps a FOCUS CSV's headers, in any
column order/subset, onto the `focus_cost_and_usage` table) -- so a real
AWS Cost & Usage Report exported in FOCUS format uses the same mapping as
the synthetic sample data.

Each entry: (FOCUS column ID, db column name, value kind).
`kind` drives CSV formatting on write and type coercion on read/ingest.
"""
from __future__ import annotations

# (focus_id, db_column, kind) -- kind in {text, numeric, timestamp, json, boolean}
FOCUS_COLUMNS: list[tuple[str, str, str]] = [
    ("AllocatedMethodId", "allocated_method_id", "text"),
    ("AllocatedMethodDetails", "allocated_method_details", "json"),
    ("AllocatedResourceId", "allocated_resource_id", "text"),
    ("AllocatedResourceName", "allocated_resource_name", "text"),
    ("AllocatedTags", "allocated_tags", "json"),
    ("AvailabilityZone", "availability_zone", "text"),
    ("RegionId", "region_id", "text"),
    ("RegionName", "region_name", "text"),
    ("BilledCost", "billed_cost", "numeric"),
    ("EffectiveCost", "effective_cost", "numeric"),
    ("ListCost", "list_cost", "numeric"),
    ("ContractedCost", "contracted_cost", "numeric"),
    ("BillingAccountId", "billing_account_id", "text"),
    ("BillingAccountName", "billing_account_name", "text"),
    ("BillingAccountType", "billing_account_type", "text"),
    ("BillingCurrency", "billing_currency", "text"),
    ("PricingCurrency", "pricing_currency", "text"),
    ("PricingCurrencyContractedUnitPrice", "pricing_currency_contracted_unit_price", "numeric"),
    ("PricingCurrencyEffectiveCost", "pricing_currency_effective_cost", "numeric"),
    ("PricingCurrencyListUnitPrice", "pricing_currency_list_unit_price", "numeric"),
    ("BillingPeriodStart", "billing_period_start", "timestamp"),
    ("BillingPeriodEnd", "billing_period_end", "timestamp"),
    ("ChargePeriodStart", "charge_period_start", "timestamp"),
    ("ChargePeriodEnd", "charge_period_end", "timestamp"),
    ("ChargeCategory", "charge_category", "text"),
    ("ChargeClass", "charge_class", "text"),
    ("ChargeFrequency", "charge_frequency", "text"),
    ("ChargeDescription", "charge_description", "text"),
    ("CapacityReservationId", "capacity_reservation_id", "text"),
    ("CapacityReservationStatus", "capacity_reservation_status", "text"),
    ("CommitmentDiscountCategory", "commitment_discount_category", "text"),
    ("CommitmentDiscountId", "commitment_discount_id", "text"),
    ("CommitmentDiscountName", "commitment_discount_name", "text"),
    ("CommitmentDiscountQuantity", "commitment_discount_quantity", "numeric"),
    ("CommitmentDiscountStatus", "commitment_discount_status", "text"),
    ("CommitmentDiscountType", "commitment_discount_type", "text"),
    ("CommitmentDiscountUnit", "commitment_discount_unit", "text"),
    ("CommitmentProgramEligibilityDetails", "commitment_program_eligibility_details", "json"),
    ("ConsumedQuantity", "consumed_quantity", "numeric"),
    ("ConsumedUnit", "consumed_unit", "text"),
    ("PricingQuantity", "pricing_quantity", "numeric"),
    ("PricingUnit", "pricing_unit", "text"),
    ("ContractApplied", "contract_applied", "boolean"),
    ("ContractedUnitPrice", "contracted_unit_price", "numeric"),
    ("HostProviderName", "host_provider_name", "text"),
    ("InvoiceDetailId", "invoice_detail_id", "text"),
    ("InvoiceId", "invoice_id", "text"),
    ("InvoiceIssuerName", "invoice_issuer_name", "text"),
    ("ListUnitPrice", "list_unit_price", "numeric"),
    ("PricingCategory", "pricing_category", "text"),
    ("ResourceId", "resource_id", "text"),
    ("ResourceName", "resource_name", "text"),
    ("ResourceType", "resource_type", "text"),
    ("ServiceProviderName", "service_provider_name", "text"),
    ("ServiceCategory", "service_category", "text"),
    ("ServiceName", "service_name", "text"),
    ("ServiceSubcategory", "service_subcategory", "text"),
    ("SkuId", "sku_id", "text"),
    ("SkuMeter", "sku_meter", "text"),
    ("SkuPriceDetails", "sku_price_details", "json"),
    ("SkuPriceId", "sku_price_id", "text"),
    ("SubAccountId", "sub_account_id", "text"),
    ("SubAccountName", "sub_account_name", "text"),
    ("SubAccountType", "sub_account_type", "text"),
    ("Tags", "tags", "json"),
]

FOCUS_ID_TO_DB = {focus_id: db_col for focus_id, db_col, _ in FOCUS_COLUMNS}
DB_TO_FOCUS_ID = {db_col: focus_id for focus_id, db_col, _ in FOCUS_COLUMNS}
FOCUS_ID_TO_KIND = {focus_id: kind for focus_id, _, kind in FOCUS_COLUMNS}
ALL_FOCUS_IDS = [c[0] for c in FOCUS_COLUMNS]

# Columns enforced as NOT NULL in schema.sql (see the comment there for why
# this is a pragmatic subset of the spec's "Mandatory" columns rather than
# all of them).
NOT_NULL_FOCUS_IDS = [
    "BilledCost", "EffectiveCost", "ListCost",
    "BillingAccountId", "BillingCurrency",
    "BillingPeriodStart", "BillingPeriodEnd",
    "ChargePeriodStart", "ChargePeriodEnd",
    "ChargeCategory", "ChargeFrequency",
    "ServiceProviderName", "ServiceCategory", "ServiceName",
]
