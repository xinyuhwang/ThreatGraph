-- Two corrections found by running the system.

-- 1. Deleting an investigation cascades to its observations, but the evidence
--    links referencing those observations had no cascade of their own, so the
--    foreign key blocked the delete.
--
--    Cascading here is safe because of an invariant the grounding check
--    enforces: a result may only cite observations from its own
--    investigation. So an observation is only ever deleted together with the
--    result that cites it, and the link should go with them. Without the
--    cascade the delete order decides whether it works, which is not a thing
--    to leave to chance.
ALTER TABLE result_evidence
    DROP CONSTRAINT result_evidence_observation_id_fkey;

ALTER TABLE result_evidence
    ADD CONSTRAINT result_evidence_observation_id_fkey
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE;


-- 2. `real` is single precision, so a confidence of 0.6 came back out of the
--    database as 0.6000000238418579 and was served to API clients that way.
--    These values are small and few; double precision costs nothing here and
--    round-trips the numbers people actually enter.
ALTER TABLE investigation_results
    ALTER COLUMN confidence TYPE double precision;

ALTER TABLE relationships
    ALTER COLUMN confidence TYPE double precision;
