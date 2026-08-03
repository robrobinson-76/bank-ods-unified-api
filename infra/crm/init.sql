-- "Legacy CRM" application database — the source system that only has a database.
--
-- Deliberately source-flavoured: lowercase snake_case, its own column names and
-- code values, no knowledge of the ODS. Everything that turns these rows into
-- ODS accounts lives in ods_ingest/curation/crm_accounts.py.
--
-- Rows are NOT seeded here — scripts/crm_seed.py populates the tables from the
-- seeded Mongo accounts so CDC curation converges with existing ODS data.

CREATE TABLE clients (
    client_id           TEXT PRIMARY KEY,
    client_name         TEXT        NOT NULL,
    lei                 TEXT,
    country_domicile    TEXT,
    country_incorp      TEXT,
    tax_residencies     TEXT,               -- comma-separated; the legacy app never normalised this
    classification      TEXT,               -- retail / professional / eligible_counterparty
    kyc_status          TEXT,               -- approved / pending_review / expired
    risk_rating         TEXT,               -- low / medium / high
    legal_entity_type   TEXT,               -- corporation / partnership / fund / trust / government / individual
    parent_client_id    TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    account_nbr         TEXT PRIMARY KEY,
    client_id           TEXT        NOT NULL REFERENCES clients (client_id),
    account_name        TEXT        NOT NULL,
    account_type        TEXT,               -- custody / proprietary / omnibus
    base_ccy            TEXT,
    status              TEXT,               -- active / suspended / closed
    open_date           DATE,
    close_date          DATE,
    branch              TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX accounts_client_id_idx ON accounts (client_id);

-- REPLICA IDENTITY FULL makes Debezium emit the complete `before` image on
-- UPDATE and DELETE. Without it deletes carry only the primary key, and the
-- raw-tier change-event log would lose the state that was deleted.
ALTER TABLE clients  REPLICA IDENTITY FULL;
ALTER TABLE accounts REPLICA IDENTITY FULL;

-- Debezium (pgoutput) publishes through this publication.
CREATE PUBLICATION dbz_publication FOR TABLE clients, accounts;
