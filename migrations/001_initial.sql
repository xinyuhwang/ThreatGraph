-- ThreatGraph initial schema.
--
-- Design note: every table that a worker writes to carries a uniqueness
-- constraint, and every worker write is an INSERT ... ON CONFLICT. Redis gives
-- at-least-once delivery, so duplicate processing is normal rather than
-- exceptional; enforcing idempotency here makes it a no-op at the storage layer
-- instead of a race the workers have to win.

CREATE EXTENSION IF NOT EXISTS pg_trgm;


CREATE TABLE investigations (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    indicator         text NOT NULL,
    indicator_type    text NOT NULL
                      CHECK (indicator_type IN ('domain', 'url')),
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'enriching', 'analyzing',
                                        'correlating', 'complete', 'failed',
                                        'cancelled')),
    -- Per-source outcome, e.g. {"dns": "ok", "http": "failed"}. The AI analyst
    -- reads this so it can tell "HTTP returned nothing" from "HTTP was never
    -- attempted" — the distinction that should drive a low-confidence unknown.
    enrichment_status jsonb NOT NULL DEFAULT '{}',
    error             text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Indicator lookup is substring and fuzzy matching ('paypal' should find
-- 'paypal-secure-login.com'), which is a trigram job. Full-text search is
-- reserved for the analyst's explanations, the only natural language we store.
CREATE INDEX idx_investigations_indicator_trgm
    ON investigations USING gin (indicator gin_trgm_ops);

CREATE INDEX idx_investigations_status ON investigations (status);

-- Supports the stalled-investigation sweeper, which looks for non-terminal
-- investigations that stopped advancing.
CREATE INDEX idx_investigations_active
    ON investigations (updated_at)
    WHERE status NOT IN ('complete', 'failed', 'cancelled');


CREATE TABLE entities (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type       text NOT NULL
               CHECK (type IN ('domain', 'ip', 'url', 'certificate')),
    value      text NOT NULL,
    metadata   jsonb NOT NULL DEFAULT '{}',
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),

    -- Unique on the pair, not on value alone: the same string can legitimately
    -- be more than one entity type, and the upsert needs a constraint to target.
    UNIQUE (type, value)
);

CREATE INDEX idx_entities_value_trgm ON entities USING gin (value gin_trgm_ops);


CREATE TABLE observations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id uuid NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    entity_id        uuid REFERENCES entities(id),
    source           text NOT NULL
                     CHECK (source IN ('dns', 'http', 'tls', 'whois')),
    -- Failed enrichment is recorded, not discarded. A 'failed' row carries an
    -- error payload in data.
    status           text NOT NULL DEFAULT 'ok'
                     CHECK (status IN ('ok', 'failed')),
    data             jsonb NOT NULL,
    collected_at     timestamptz NOT NULL DEFAULT now(),

    UNIQUE (investigation_id, source)
);

CREATE INDEX idx_observations_investigation ON observations (investigation_id);
CREATE INDEX idx_observations_entity ON observations (entity_id);


CREATE TABLE relationships (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id  uuid NOT NULL REFERENCES entities(id),
    target_entity_id  uuid NOT NULL REFERENCES entities(id),
    relationship_type text NOT NULL
                      CHECK (relationship_type IN ('resolves_to', 'uses_certificate',
                                                   'redirects_to', 'hosted_on')),
    -- Assigned by deterministic rules during correlation, never by the AI.
    -- A DNS A record yields resolves_to at 1.0; inferred links score lower.
    confidence        real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at       timestamptz NOT NULL DEFAULT now(),

    UNIQUE (source_entity_id, target_entity_id, relationship_type)
);

CREATE INDEX idx_relationships_source ON relationships (source_entity_id);
CREATE INDEX idx_relationships_target ON relationships (target_entity_id);


CREATE TABLE investigation_results (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- One result per investigation. This is also the backstop that keeps a
    -- duplicated analyst run from writing a second conclusion.
    investigation_id   uuid NOT NULL UNIQUE
                       REFERENCES investigations(id) ON DELETE CASCADE,
    classification     text NOT NULL
                       CHECK (classification IN ('benign', 'suspicious',
                                                 'malicious', 'unknown')),
    confidence         real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    explanation        text NOT NULL,
    entity_ids         jsonb NOT NULL DEFAULT '[]',
    recommended_action text NOT NULL
                       CHECK (recommended_action IN ('monitor', 'block', 'escalate',
                                                     'no_action', 'request_review')),
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_results_explanation_fts
    ON investigation_results USING gin (to_tsvector('english', explanation));

CREATE INDEX idx_results_classification ON investigation_results (classification);


-- The grounding link, as real foreign keys rather than a JSON array of UUIDs.
--
-- A jsonb array can hold a UUID that does not exist; a foreign key cannot. This
-- makes an ungrounded result structurally impossible to store, rather than
-- merely discouraged — and turns "every evidence reference resolves to a real
-- observation" from a test you have to remember into an invariant Postgres
-- checks on every insert.
CREATE TABLE result_evidence (
    result_id      uuid NOT NULL REFERENCES investigation_results(id) ON DELETE CASCADE,
    observation_id uuid NOT NULL REFERENCES observations(id),
    PRIMARY KEY (result_id, observation_id)
);
