-- ChurnGuard Governed Data Layer schema.
--
-- Deliberately modelled on a "legacy billing graph": most cross-entity
-- relationships (account->line, line->device financing, account->promo
-- eligibility, account->note, account->billing event) are NOT plain foreign
-- keys but rows in account_edges(from_node, edge_type, to_node). Repositories
-- must traverse edges to assemble a coherent picture, the way a real legacy
-- telecom billing system would force you to. usage_daily is the one
-- exception (direct line_id column) since it is high-cardinality time
-- series, not a relationship.
--
-- Every primary/foreign identifier here is a RAW internal identifier and
-- must never leave the data layer unmasked. See data/masking.py.

PRAGMA foreign_keys = ON;

CREATE TABLE plans (
    plan_code            TEXT PRIMARY KEY,
    plan_name            TEXT NOT NULL,
    monthly_base_price   REAL NOT NULL
);

CREATE TABLE accounts (
    account_id                 TEXT PRIMARY KEY,   -- raw 10-digit account number
    holder_full_name           TEXT NOT NULL,       -- PII, never selected by repos
    date_of_birth               TEXT,                -- protected-characteristic proxy, never selected
    zip_plus4                   TEXT,                -- protected-characteristic proxy, never selected
    marketing_segment           TEXT,                -- protected-characteristic proxy, never selected
    autopay_card_pan            TEXT,                -- PII, never selected
    autopay_card_expired        INTEGER NOT NULL DEFAULT 0,
    tenure_months                INTEGER NOT NULL,
    status                       TEXT NOT NULL,       -- active | delinquent | churned
    jurisdiction                 TEXT NOT NULL,
    contract_type                TEXT NOT NULL,
    contract_end_date            TEXT,
    plan_code                    TEXT NOT NULL REFERENCES plans(plan_code),
    payment_on_time_count        INTEGER NOT NULL DEFAULT 0,
    payment_late_count           INTEGER NOT NULL DEFAULT 0,
    payment_last_late_date       TEXT,
    payment_current_past_due     REAL NOT NULL DEFAULT 0.0,
    data_as_of                   TEXT NOT NULL,   -- last batch-sync timestamp; provenance as_of for
                                                    -- facts with no per-row date of their own
                                                    -- (plan profile, device financing, usage, payment history)
    created_at                   TEXT NOT NULL
);

CREATE TABLE lines (
    line_id       TEXT PRIMARY KEY,   -- raw internal id, e.g. '0000004471-1'
    line_index    INTEGER NOT NULL,   -- 1-based position within the account, drives masked LINE_****NN
    status        TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE billing_events (
    event_id       TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('bill_total', 'delta_attribution')),
    bill_period    TEXT NOT NULL CHECK (bill_period IN ('current', 'prior')),
    cause          TEXT,               -- NULL for bill_total rows
    amount         REAL NOT NULL,
    event_date     TEXT,
    reason         TEXT,
    reversible     INTEGER
);

CREATE TABLE usage_daily (
    usage_id      TEXT PRIMARY KEY,
    line_id       TEXT NOT NULL REFERENCES lines(line_id),
    usage_date    TEXT NOT NULL,
    usage_level   TEXT   -- e.g. 'low_data' | 'medium_data' | 'high_data'; NULL means no usage recorded
);

CREATE TABLE device_financing (
    device_fin_id           TEXT PRIMARY KEY,
    device_name             TEXT NOT NULL,
    remaining_balance       REAL NOT NULL,
    monthly_payment         REAL NOT NULL,
    months_remaining        INTEGER NOT NULL,
    early_termination_fee   REAL NOT NULL
);

CREATE TABLE promotions (
    promo_code       TEXT PRIMARY KEY,
    promo_name       TEXT NOT NULL,
    description      TEXT,
    monthly_value    REAL NOT NULL
);

CREATE TABLE promo_eligibility (
    elig_id           TEXT PRIMARY KEY,
    promo_code        TEXT NOT NULL REFERENCES promotions(promo_code),
    status            TEXT NOT NULL CHECK (status IN ('eligible', 'active', 'expired', 'ineligible')),
    effective_date    TEXT
);

CREATE TABLE account_notes (
    note_id       TEXT PRIMARY KEY,
    author_ref    TEXT NOT NULL,   -- already-masked agent ref, e.g. 'AGT_****0192'
    created_at    TEXT NOT NULL,
    note_text     TEXT NOT NULL
);

CREATE TABLE competitor_snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    geography         TEXT NOT NULL,
    carrier           TEXT NOT NULL,
    plan_name         TEXT NOT NULL,
    monthly_price     REAL NOT NULL,
    line_count        INTEGER NOT NULL,
    includes_json     TEXT NOT NULL,   -- JSON array of strings
    captured_at       TEXT NOT NULL,
    capture_method    TEXT NOT NULL
);

CREATE TABLE policy_packs (
    policy_pack_version    TEXT PRIMARY KEY,
    policy_pack_hash       TEXT NOT NULL,
    published_at           TEXT NOT NULL,
    description            TEXT
);

CREATE TABLE audit_log (
    audit_id       TEXT PRIMARY KEY,
    actor_ref      TEXT NOT NULL,
    action         TEXT NOT NULL,
    target_ref     TEXT NOT NULL,
    occurred_at    TEXT NOT NULL,
    detail         TEXT
);

-- The legacy billing graph. (from_node, edge_type, to_node) triples wire
-- accounts to lines, lines to device financing, accounts to billing events,
-- promo eligibility and notes.
CREATE TABLE account_edges (
    from_node    TEXT NOT NULL,
    edge_type    TEXT NOT NULL,
    to_node      TEXT NOT NULL,
    PRIMARY KEY (from_node, edge_type, to_node)
);

CREATE INDEX idx_account_edges_from ON account_edges (from_node, edge_type);
CREATE INDEX idx_account_edges_to ON account_edges (to_node, edge_type);
CREATE INDEX idx_usage_daily_line ON usage_daily (line_id);
CREATE INDEX idx_promo_eligibility_promo_code ON promo_eligibility (promo_code);
