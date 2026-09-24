-- Demo data: four completed investigations, three of which share a host.
--
-- Without this, a freshly cloned database has no history — and two of the
-- analyst's five tools (search_indicators, get_related_entities) query
-- history. A reviewer's first run would show the pipeline working but never
-- exercise the capability that makes the graph worth building: noticing that
-- separate indicators sit on the same infrastructure.
--
-- Addresses are from RFC 5737 documentation ranges. They are never fetched —
-- these rows describe investigations that already happened — and the SSRF
-- guard would refuse them anyway, which is the correct behaviour.
--
-- Fixed UUIDs so the data is stable across runs and can be referenced in
-- documentation.

INSERT INTO entities (id, type, value, metadata) VALUES
    ('e0000000-0000-4000-8000-000000000001', 'domain', 'secure-paypal-login.example',  '{"registrar": "Example Registrar", "registered_days_ago": 3}'),
    ('e0000000-0000-4000-8000-000000000002', 'domain', 'paypal-verify-account.example','{"registrar": "Example Registrar", "registered_days_ago": 5}'),
    ('e0000000-0000-4000-8000-000000000003', 'domain', 'account-update-paypal.example','{"registrar": "Example Registrar", "registered_days_ago": 2}'),
    ('e0000000-0000-4000-8000-000000000004', 'domain', 'corner-bakery.example',        '{"registrar": "Longstanding Registrar", "registered_days_ago": 2190}'),
    ('e0000000-0000-4000-8000-00000000000a', 'ip',     '198.51.100.17',                '{"asn": "AS64496", "hosting": "Example Bulletproof Hosting"}'),
    ('e0000000-0000-4000-8000-00000000000b', 'ip',     '203.0.113.9',                  '{"asn": "AS64497", "hosting": "Example Shared Hosting"}')
ON CONFLICT (type, value) DO NOTHING;


INSERT INTO investigations (id, indicator, indicator_type, status, enrichment_status, created_at, updated_at) VALUES
    ('11111111-0000-4000-8000-000000000001', 'secure-paypal-login.example',  'domain', 'complete', '{"dns":"ok","http":"ok"}',     now() - interval '9 days',  now() - interval '9 days'),
    ('11111111-0000-4000-8000-000000000002', 'paypal-verify-account.example','domain', 'complete', '{"dns":"ok","http":"ok"}',     now() - interval '6 days',  now() - interval '6 days'),
    ('11111111-0000-4000-8000-000000000003', 'account-update-paypal.example','domain', 'complete', '{"dns":"ok","http":"failed"}', now() - interval '2 days',  now() - interval '2 days'),
    ('11111111-0000-4000-8000-000000000004', 'corner-bakery.example',        'domain', 'complete', '{"dns":"ok","http":"ok"}',     now() - interval '14 days', now() - interval '14 days')
ON CONFLICT (id) DO NOTHING;


INSERT INTO observations (id, investigation_id, entity_id, source, status, data, collected_at) VALUES
    ('0b5e0000-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-000000000001', 'dns', 'ok',
     '{"host":"secure-paypal-login.example","nxdomain":false,"resolved_ips":["198.51.100.17"],"nameservers":["ns1.example-registrar.net"],"records":{"A":["198.51.100.17"],"MX":[],"NS":["ns1.example-registrar.net"],"TXT":["v=spf1 -all"]},"spf":["v=spf1 -all"]}', now() - interval '9 days'),
    ('0b5e0000-0000-4000-8000-000000000002', '11111111-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-000000000001', 'http', 'ok',
     '{"status_code":200,"title":"Sign in to your account","server":"nginx","final_url":"http://secure-paypal-login.example/signin","redirect_chain":["http://secure-paypal-login.example/","http://secure-paypal-login.example/signin"],"redirect_count":1,"headers":{"server":"nginx","content-type":"text/html"}}', now() - interval '9 days'),

    ('0b5e0000-0000-4000-8000-000000000003', '11111111-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000002', 'dns', 'ok',
     '{"host":"paypal-verify-account.example","nxdomain":false,"resolved_ips":["198.51.100.17"],"nameservers":["ns1.example-registrar.net"],"records":{"A":["198.51.100.17"],"MX":[],"NS":["ns1.example-registrar.net"],"TXT":[]},"spf":[]}', now() - interval '6 days'),
    ('0b5e0000-0000-4000-8000-000000000004', '11111111-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000002', 'http', 'ok',
     '{"status_code":200,"title":"Verify your account details","server":"nginx","final_url":"http://paypal-verify-account.example/","redirect_chain":["http://paypal-verify-account.example/"],"redirect_count":0,"headers":{"server":"nginx","content-type":"text/html"}}', now() - interval '6 days'),

    ('0b5e0000-0000-4000-8000-000000000005', '11111111-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000003', 'dns', 'ok',
     '{"host":"account-update-paypal.example","nxdomain":false,"resolved_ips":["198.51.100.17"],"nameservers":["ns1.example-registrar.net"],"records":{"A":["198.51.100.17"],"MX":[],"NS":["ns1.example-registrar.net"],"TXT":[]},"spf":[]}', now() - interval '2 days'),
    -- A failed source, kept as evidence. The analyst must be able to tell this
    -- apart from a source that was never attempted.
    ('0b5e0000-0000-4000-8000-000000000006', '11111111-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000003', 'http', 'failed',
     '{"error":"ServerTimeoutError","detail":"Connection timed out after 10 seconds"}', now() - interval '2 days'),

    ('0b5e0000-0000-4000-8000-000000000007', '11111111-0000-4000-8000-000000000004', 'e0000000-0000-4000-8000-000000000004', 'dns', 'ok',
     '{"host":"corner-bakery.example","nxdomain":false,"resolved_ips":["203.0.113.9"],"nameservers":["ns1.example-shared.net"],"records":{"A":["203.0.113.9"],"MX":["10 mail.corner-bakery.example"],"NS":["ns1.example-shared.net"],"TXT":["v=spf1 include:example-shared.net ~all"]},"spf":["v=spf1 include:example-shared.net ~all"]}', now() - interval '14 days'),
    ('0b5e0000-0000-4000-8000-000000000008', '11111111-0000-4000-8000-000000000004', 'e0000000-0000-4000-8000-000000000004', 'http', 'ok',
     '{"status_code":200,"title":"Corner Bakery — Fresh Bread Daily","server":"Apache","final_url":"http://corner-bakery.example/","redirect_chain":["http://corner-bakery.example/"],"redirect_count":0,"headers":{"server":"Apache","content-type":"text/html"}}', now() - interval '14 days')
ON CONFLICT (investigation_id, source) DO NOTHING;


-- Three unrelated-looking domains converge on one address. This is the edge
-- that makes get_related_entities worth calling.
INSERT INTO relationships (source_entity_id, target_entity_id, relationship_type, confidence, observed_at) VALUES
    ('e0000000-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-00000000000a', 'resolves_to', 1.0, now() - interval '9 days'),
    ('e0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-00000000000a', 'resolves_to', 1.0, now() - interval '6 days'),
    ('e0000000-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-00000000000a', 'resolves_to', 1.0, now() - interval '2 days'),
    ('e0000000-0000-4000-8000-000000000004', 'e0000000-0000-4000-8000-00000000000b', 'resolves_to', 1.0, now() - interval '14 days')
ON CONFLICT (source_entity_id, target_entity_id, relationship_type) DO NOTHING;


INSERT INTO investigation_results (id, investigation_id, classification, confidence, explanation, entity_ids, recommended_action, created_at) VALUES
    ('4e500000-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000001', 'malicious', 0.88,
     'The domain impersonates a payment provider in both its name and its page title ("Sign in to your account"), resolves to 198.51.100.17, and was registered three days before submission. The combination of brand impersonation, a credential-entry page and newly provisioned infrastructure is consistent with credential harvesting.',
     '["e0000000-0000-4000-8000-000000000001","e0000000-0000-4000-8000-00000000000a"]', 'block', now() - interval '9 days'),

    ('4e500000-0000-4000-8000-000000000002', '11111111-0000-4000-8000-000000000002', 'malicious', 0.91,
     'This domain resolves to 198.51.100.17, the same address serving secure-paypal-login.example, which was classified malicious six days ago. It shares that host, the same nameserver and the same registrar, and its page invites the visitor to verify account details. Shared infrastructure with a confirmed malicious domain raises confidence beyond what this domain''s own content would support.',
     '["e0000000-0000-4000-8000-000000000002","e0000000-0000-4000-8000-00000000000a"]', 'block', now() - interval '6 days'),

    ('4e500000-0000-4000-8000-000000000003', '11111111-0000-4000-8000-000000000003', 'suspicious', 0.64,
     'The domain resolves to 198.51.100.17, an address already serving two domains classified as malicious. HTTP enrichment was attempted and timed out, so there is no page content to corroborate that signal. The infrastructure overlap is strong enough to warrant action, but without a response the case is not conclusive on its own.',
     '["e0000000-0000-4000-8000-000000000003","e0000000-0000-4000-8000-00000000000a"]', 'escalate', now() - interval '2 days'),

    ('4e500000-0000-4000-8000-000000000004', '11111111-0000-4000-8000-000000000004', 'benign', 0.82,
     'A long-registered domain on shared hosting with ordinary mail and SPF records, serving a bakery''s website. Nothing in the collected DNS or HTTP evidence indicates abuse, and its address is not shared with any previously flagged entity.',
     '["e0000000-0000-4000-8000-000000000004","e0000000-0000-4000-8000-00000000000b"]', 'no_action', now() - interval '14 days')
ON CONFLICT (investigation_id) DO NOTHING;


INSERT INTO result_evidence (result_id, observation_id) VALUES
    ('4e500000-0000-4000-8000-000000000001', '0b5e0000-0000-4000-8000-000000000001'),
    ('4e500000-0000-4000-8000-000000000001', '0b5e0000-0000-4000-8000-000000000002'),
    ('4e500000-0000-4000-8000-000000000002', '0b5e0000-0000-4000-8000-000000000003'),
    ('4e500000-0000-4000-8000-000000000002', '0b5e0000-0000-4000-8000-000000000004'),
    ('4e500000-0000-4000-8000-000000000003', '0b5e0000-0000-4000-8000-000000000005'),
    ('4e500000-0000-4000-8000-000000000003', '0b5e0000-0000-4000-8000-000000000006'),
    ('4e500000-0000-4000-8000-000000000004', '0b5e0000-0000-4000-8000-000000000007'),
    ('4e500000-0000-4000-8000-000000000004', '0b5e0000-0000-4000-8000-000000000008')
ON CONFLICT DO NOTHING;
