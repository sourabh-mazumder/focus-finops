-- FOCUS v1.4 "Cost and Usage" dataset table
--
-- Column set follows the FinOps Open Cost and Usage Specification (FOCUS)
-- v1.4 core dataset (https://focus.finops.org/focus-specification/v1-4/),
-- section 3.1 "Cost and Usage" (65 columns). Column names below are
-- snake_case versions of the spec's PascalCase FOCUS column IDs (e.g.
-- BilledCost -> billed_cost); the mapping back to the official column ID
-- is used by the ingest script when matching a FOCUS export's CSV headers.
--
-- Requirement levels from the spec (Mandatory / Conditional / Recommended /
-- Optional) are noted per column. NOTE: "Mandatory" in FOCUS means the
-- *column* must be present in a conformant dataset -- it does not always
-- mean every row's value is non-null (e.g. ResourceId is Mandatory but is
-- legitimately null for account-level charges like taxes). To keep this
-- sample schema usable with realistic data, NOT NULL is only enforced on
-- the small set of fields that are always populated in practice (the core
-- cost amounts, dates, currency, and charge/service classification). Adjust
-- to taste if you need stricter validation.

CREATE TABLE IF NOT EXISTS focus_cost_and_usage (
    id                                      BIGSERIAL PRIMARY KEY,

    -- Allocation (Conditional / Optional)
    allocated_method_id                     TEXT,
    allocated_method_details                JSONB,
    allocated_resource_id                   TEXT,
    allocated_resource_name                 TEXT,
    allocated_tags                          JSONB,

    -- Location (Recommended)
    availability_zone                       TEXT,
    region_id                               TEXT,
    region_name                             TEXT,

    -- Core cost amounts (Mandatory)
    billed_cost                             NUMERIC(38, 10) NOT NULL,
    effective_cost                          NUMERIC(38, 10) NOT NULL,
    list_cost                               NUMERIC(38, 10) NOT NULL,
    contracted_cost                         NUMERIC(38, 10),          -- Conditional

    -- Billing account (Mandatory / Recommended)
    billing_account_id                      TEXT NOT NULL,
    billing_account_name                    TEXT,
    billing_account_type                    TEXT,

    -- Currency (Mandatory / Conditional)
    billing_currency                        CHAR(3) NOT NULL,
    pricing_currency                        CHAR(3),
    pricing_currency_contracted_unit_price  NUMERIC(38, 10),
    pricing_currency_effective_cost         NUMERIC(38, 10),
    pricing_currency_list_unit_price        NUMERIC(38, 10),

    -- Billing period (Mandatory)
    billing_period_start                    TIMESTAMPTZ NOT NULL,
    billing_period_end                      TIMESTAMPTZ NOT NULL,

    -- Charge period (Mandatory)
    charge_period_start                     TIMESTAMPTZ NOT NULL,
    charge_period_end                       TIMESTAMPTZ NOT NULL,

    -- Charge classification (Mandatory / Recommended)
    charge_category                         TEXT NOT NULL,
    charge_class                            TEXT,                    -- Mandatory column, nullable value (null = not a correction)
    charge_frequency                        TEXT NOT NULL,
    charge_description                      TEXT,

    -- Capacity reservations (Conditional)
    capacity_reservation_id                 TEXT,
    capacity_reservation_status             TEXT,

    -- Commitment-based discounts (Conditional / Optional)
    commitment_discount_category            TEXT,
    commitment_discount_id                  TEXT,
    commitment_discount_name                TEXT,
    commitment_discount_quantity            NUMERIC(38, 10),
    commitment_discount_status              TEXT,
    commitment_discount_type                TEXT,
    commitment_discount_unit                TEXT,
    commitment_program_eligibility_details  JSONB,

    -- Usage quantities (Recommended / Mandatory)
    consumed_quantity                       NUMERIC(38, 10),
    consumed_unit                           TEXT,
    pricing_quantity                        NUMERIC(38, 10),
    pricing_unit                            TEXT,

    -- Contract (Conditional)
    contract_applied                        BOOLEAN,
    contracted_unit_price                   NUMERIC(38, 10),

    -- Hosting (Conditional)
    host_provider_name                      TEXT,

    -- Invoicing (Conditional / Recommended)
    invoice_detail_id                       TEXT,
    invoice_id                              TEXT,
    invoice_issuer_name                     TEXT,

    -- Pricing (Mandatory)
    list_unit_price                         NUMERIC(38, 10),
    pricing_category                        TEXT,

    -- Resource (Mandatory / Recommended)
    resource_id                             TEXT,
    resource_name                           TEXT,
    resource_type                           TEXT,

    -- Service (Mandatory / Recommended)
    service_provider_name                   TEXT NOT NULL,
    service_category                        TEXT NOT NULL,
    service_name                            TEXT NOT NULL,
    service_subcategory                     TEXT,

    -- SKU (Recommended / Optional)
    sku_id                                  TEXT,
    sku_meter                               TEXT,
    sku_price_details                       JSONB,
    sku_price_id                            TEXT,

    -- Sub account (Recommended)
    sub_account_id                          TEXT,
    sub_account_name                        TEXT,
    sub_account_type                        TEXT,

    -- Tags (Recommended)
    tags                                    JSONB,

    -- Ingest metadata (not part of FOCUS spec)
    source_file                             TEXT,
    loaded_at                               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_focus_billing_period_start ON focus_cost_and_usage (billing_period_start);
CREATE INDEX IF NOT EXISTS idx_focus_charge_period_start   ON focus_cost_and_usage (charge_period_start);
CREATE INDEX IF NOT EXISTS idx_focus_service_category      ON focus_cost_and_usage (service_category);
CREATE INDEX IF NOT EXISTS idx_focus_service_name          ON focus_cost_and_usage (service_name);
CREATE INDEX IF NOT EXISTS idx_focus_billing_account_id    ON focus_cost_and_usage (billing_account_id);
CREATE INDEX IF NOT EXISTS idx_focus_region_id              ON focus_cost_and_usage (region_id);
CREATE INDEX IF NOT EXISTS idx_focus_resource_id            ON focus_cost_and_usage (resource_id);
CREATE INDEX IF NOT EXISTS idx_focus_service_provider_name  ON focus_cost_and_usage (service_provider_name);
CREATE INDEX IF NOT EXISTS idx_focus_tags                   ON focus_cost_and_usage USING GIN (tags);
