# ThreatGraph

**AI-powered threat intelligence and investigation platform.**

Submit a domain or URL. ThreatGraph collects evidence about it, has an AI analyst interpret that evidence, and returns a classification where **every claim is traceable to a specific piece of collected data**.

```bash
curl -X POST localhost:8000/investigations \
  -H 'Content-Type: application/json' \
  -d '{"indicator": "suspicious-domain.com"}'
```

Built with FastAPI, PostgreSQL, Redis Streams, and Claude. Runs entirely in Docker Compose.

📄 Full architecture and design rationale: [ThreatGraph_Design_Document.md](ThreatGraph_Design_Document.md)

---

## What problem this solves

Most "AI security analyst" tools ask you to trust the model. This one doesn't.

Deterministic services collect the evidence — DNS records, HTTP headers, redirect chains — and store it in PostgreSQL. The AI analyst can only *interpret* that evidence, and its conclusion has to cite the specific observation records it relied on. Those citations are checked against the database before anything is saved.

If the model invents a fact, there's no record to cite, and the result is rejected. You can always click through from a conclusion to the raw data behind it.

---

## Quickstart

**Prerequisites:** Docker, Docker Compose, and an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone https://github.com/xinyuhwang/ThreatGraph.git
cd ThreatGraph

cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY

docker compose up
```

That's it. On first start the database is migrated and seeded with four prior investigations, so the correlation features have history to work with.

When the logs settle:

| | |
|---|---|
| **API** | http://localhost:8000 |
| **Interactive docs (Swagger)** | http://localhost:8000/docs |
| **Health** | http://localhost:8000/health |
| **Metrics** | http://localhost:8000/metrics |

> **A note on cost.** Each investigation makes roughly 4–5 Claude API calls. The system prompt and tool definitions are cached across requests, so repeat investigations cost a fraction of the first. Running the demo walkthrough below a few times costs cents, not dollars — but it isn't free, so the AI evaluation tests are excluded from the default test run.

---

## Walkthrough

### 1. Submit an indicator

```bash
curl -X POST localhost:8000/investigations \
  -H 'Content-Type: application/json' \
  -d '{"indicator": "suspicious-domain.com"}'
```

```json
{
  "id": "3f9a1c84-7b22-4e51-9d0a-6c8e4b1f2a77",
  "indicator": "suspicious-domain.com",
  "indicator_type": "domain",
  "status": "pending",
  "created_at": "2026-09-21T14:03:11Z"
}
```

The response comes back immediately. Nothing blocks on enrichment — the indicator type is derived server-side, the record is saved, a task is published to Redis, and the API is done.

### 2. Poll for the result

```bash
curl localhost:8000/investigations/3f9a1c84-7b22-4e51-9d0a-6c8e4b1f2a77
```

While it runs, `status` moves through `enriching` → `analyzing` → `correlating`. A complete investigation looks like this:

```json
{
  "id": "3f9a1c84-7b22-4e51-9d0a-6c8e4b1f2a77",
  "indicator": "suspicious-domain.com",
  "indicator_type": "domain",
  "status": "complete",
  "enrichment_status": { "dns": "ok", "http": "ok" },
  "result": {
    "classification": "suspicious",
    "confidence": 0.78,
    "explanation": "The domain resolves to 203.0.113.42, which also hosts three domains from earlier investigations, two of which were classified as malicious. The HTTP response redirects twice before landing on a login page whose title impersonates a payment provider. Registration and DNS records are consistent with recently provisioned infrastructure.",
    "recommended_action": "escalate",
    "entity_ids": [
      "8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f",
      "b7e4f2a1-9c3d-4e5f-8a7b-1c2d3e4f5a6b"
    ],
    "evidence_refs": [
      "d4c3b2a1-5f6e-7d8c-9b0a-1e2f3a4b5c6d",
      "a1b2c3d4-6e5f-8d7c-0b9a-2f1e4a3b6c5d"
    ]
  },
  "created_at": "2026-09-21T14:03:11Z",
  "updated_at": "2026-09-21T14:03:29Z"
}
```

### 3. Check the evidence behind the conclusion

Those `evidence_refs` are real observation IDs. Look them up:

```bash
curl localhost:8000/investigations/3f9a1c84-7b22-4e51-9d0a-6c8e4b1f2a77/evidence
```

```json
[
  {
    "id": "d4c3b2a1-5f6e-7d8c-9b0a-1e2f3a4b5c6d",
    "source": "dns",
    "status": "ok",
    "collected_at": "2026-09-21T14:03:13Z",
    "data": {
      "A": ["203.0.113.42"],
      "NS": ["ns1.example-registrar.net", "ns2.example-registrar.net"],
      "MX": [],
      "TXT": ["v=spf1 -all"]
    }
  },
  {
    "id": "a1b2c3d4-6e5f-8d7c-0b9a-2f1e4a3b6c5d",
    "source": "http",
    "status": "ok",
    "collected_at": "2026-09-21T14:03:13Z",
    "data": {
      "status_code": 200,
      "title": "Secure Account Verification",
      "redirect_chain": [
        "http://suspicious-domain.com/",
        "https://suspicious-domain.com/login",
        "https://suspicious-domain.com/verify"
      ],
      "headers": { "server": "nginx", "x-powered-by": "PHP/7.4.3" }
    }
  }
]
```

This is the point of the whole system: the explanation's claims about redirects and the resolved IP map directly onto records you can read yourself.

### 4. See the infrastructure graph

```bash
curl localhost:8000/investigations/3f9a1c84-7b22-4e51-9d0a-6c8e4b1f2a77/entities
```

Returns the entities discovered during enrichment and how they connect — including the shared IP that links this domain to the seeded prior investigations.

### 5. Search across everything

```bash
# Fuzzy indicator lookup — matches substrings, not just exact values
curl 'localhost:8000/search?q=suspicious'

# Filter by what the analyst concluded
curl 'localhost:8000/search?q=payment&classification=malicious'
```

Indicator search uses trigram matching, so `paypal` finds `paypal-secure-login.com`. Full-text search runs over the analyst's explanations.

---

## How it works

```
                  ┌─────────┐
   POST ─────────>│ FastAPI │──── insert ─────┐
   (returns       └────┬────┘                 │
    immediately)       │ XADD                 ▼
                       ▼                ┌──────────────┐
              ┌────────────────┐        │  PostgreSQL  │
              │ tasks:enrich   │        │              │
              └───────┬────────┘        │ investigations│
                      ▼                 │ entities      │
            ┌───────────────────┐       │ observations  │
            │ Enrichment worker │──────>│ relationships │
            │  DNS + HTTP       │       │ results       │
            └───────┬───────────┘       │ result_evidence│
                    │ XADD              └──────▲───────┘
                    ▼                          │
            ┌────────────────┐                 │
            │ tasks:analyze  │                 │
            └───────┬────────┘                 │
                    ▼                          │
            ┌───────────────────┐              │
            │  AI analyst       │──── verify ──┤
            │  Claude + 5 tools │    evidence  │
            └───────┬───────────┘              │
                    │ XADD                     │
                    ▼                          │
            ┌────────────────┐                 │
            │tasks:correlate │                 │
            └───────┬────────┘                 │
                    ▼                          │
            ┌───────────────────┐              │
            │ Correlation worker│──────────────┘
            │  relationships    │
            └───────────────────┘
```

Four stages, each a separate process. Workers never call each other — they communicate by writing to PostgreSQL and publishing the next task to Redis.

| Stage | What it does |
|---|---|
| **Ingest** | Validates the indicator, derives its type, saves it, publishes an enrich task. Returns in milliseconds. |
| **Enrich** | Runs DNS and HTTP lookups in parallel. Upserts the entities it finds. Saves every result as an observation — *including failures*, so the analyst knows what's missing. |
| **Analyze** | Loads the evidence, gives Claude five read-only tools plus one write tool, then verifies the returned evidence references against the database before saving. |
| **Correlate** | Creates relationship records between entities (`resolves_to`, `hosted_on`, …) with rule-based confidence scores. Marks the investigation complete. |

### Two things worth reading the code for

**Evidence grounding** — [`analyst/grounding.py`](analyst/grounding.py)

Schema validation confirms the *shape* of the AI's answer. It can't confirm the *truth* of it: a well-formed UUID that the model invented passes any schema check. So before a result is saved, its evidence references are queried against the observations table and checked to belong to this investigation. Invalid references come back to the model as a tool error naming exactly what was rejected, and it re-answers. The final storage layer is a foreign key, so an ungrounded result is impossible to write even if every earlier check were bypassed.

**Idempotency through constraints** — [`migrations/`](migrations/)

Redis gives at-least-once delivery, so every worker will occasionally process the same task twice. Rather than checking "does this row already exist?" before writing — which is a race two redelivered messages can both win — every table carries a uniqueness constraint and every write is an `INSERT ... ON CONFLICT`. Duplicate delivery becomes a no-op at the database level, with no locks and no coordination.

The same idea drives the ordering elsewhere: workers publish the next task *before* acknowledging the current one. A crash in between causes a harmless duplicate; the reverse ordering would strand the investigation permanently.

---

## API

Full interactive documentation at `/docs` once running.

| Method | Path | Description |
|---|---|---|
| `POST` | `/investigations` | Submit an indicator. Returns an ID immediately. |
| `GET` | `/investigations/{id}` | Status, result, and per-source enrichment outcome. |
| `GET` | `/investigations/{id}/evidence` | Raw observations. Filter with `?source=` and `?status=`. |
| `GET` | `/investigations/{id}/entities` | Discovered entities and relationships. |
| `GET` | `/search` | Indicator and full-text search. Requires `q`. |
| `DELETE` | `/investigations/{id}` | Cancel. Workers stop at the next stage boundary. |
| `GET` | `/health` | Per-dependency status for PostgreSQL and Redis. |
| `GET` | `/metrics` | Prometheus text format. |

Errors are consistent across every endpoint:

```json
{
  "detail": "Investigation not found",
  "code": "NOT_FOUND",
  "request_id": "req_7f3a9c21"
}
```

Every log line carries `request_id`, and `investigation_id` once one exists — so a single investigation can be traced across the API and all three workers.

---

## Project layout

```
.
├── api/               FastAPI app — routes, request/response models, dependencies
├── workers/           enrichment.py, analyst.py, correlation.py, sweeper.py
├── enrichment/
│   ├── dns.py         DNS record collection and normalisation
│   ├── http.py        HTTP fetching, redirect chains, fingerprinting
│   └── guards.py      SSRF protection — see note below
├── analyst/
│   ├── tools.py       The five Claude tools, wired to real queries
│   ├── prompts.py     System prompt
│   └── grounding.py   Evidence verification
├── core/
│   ├── db.py          Connection pool, query helpers
│   ├── streams.py     Redis Streams: publish, consume, reclaim, DLQ
│   ├── logging.py     Structured JSON logging with trace IDs
│   └── config.py      Environment configuration
├── migrations/        Schema and indexes
├── seeds/             Demo data — four prior investigations
├── tests/             unit, integration, reliability, e2e, ai_eval
└── docker-compose.yml
```

### A note on `enrichment/guards.py`

HTTP enrichment fetches URLs submitted by untrusted users, from a worker running inside the application's private network. Without protection, submitting `http://redis:6379` or `http://169.254.169.254/` would make the worker attack its own infrastructure.

Every request therefore resolves the hostname first, rejects loopback, private, link-local, and multicast ranges, and **re-runs both checks after every redirect** — a safe first hop can still redirect somewhere internal. Redirects are capped at 5, response bodies at 1 MB, and total request time at 10 seconds.

---

## Configuration

All settings come from environment variables. `.env.example` has working defaults for everything except the API key.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Model used by the analyst |
| `DATABASE_URL` | `postgresql://…@postgres:5432/threatgraph` | PostgreSQL connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `LOG_LEVEL` | `INFO` | `DEBUG` prints full tool-call traces |
| `ANALYST_MAX_ITERATIONS` | `10` | Hard cap on the agent loop |
| `ANALYST_TIMEOUT_SECONDS` | `180` | Per-investigation ceiling for the Claude call |
| `RECLAIM_IDLE_ENRICH_MS` | `30000` | When to reclaim a stalled enrichment task |
| `RECLAIM_IDLE_ANALYZE_MS` | `300000` | Same, for analysis — LLM work legitimately takes longer |
| `RATE_LIMIT_PER_MINUTE` | `30` | Submissions per client |
| `SEED_DEMO_DATA` | `true` | Load the demo investigations on first start |

The two reclaim thresholds are deliberately different. Enrichment finishes in seconds; an agentic Claude loop making several tool calls routinely runs past thirty. A single shared threshold would keep reclaiming healthy analysis work and paying for it twice.

---

## Testing

```bash
# Everything except the live-API tests
docker compose run --rm api pytest

# By tier
docker compose run --rm api pytest tests/unit          # pure functions, no I/O
docker compose run --rm api pytest tests/integration   # real Postgres and Redis
docker compose run --rm api pytest tests/reliability   # crash recovery, retries
docker compose run --rm api pytest tests/e2e           # full stack, real domain

# Calls the live Claude API — costs money, excluded by default
docker compose run --rm api pytest -m ai_eval
```

The reliability tier is the one worth looking at. "Idempotent, safe to restart" is easy to claim, so these tests prove it: the enrichment worker is killed mid-investigation and the investigation must still reach `complete` with no duplicated observations; a task is acknowledged without publishing its successor and the sweeper must notice and republish it; three consecutive failures must land the message in the dead-letter stream and stop it being reclaimed forever.

The AI evaluation tier checks that the analyst behaves predictably against fixed evidence sets — a known-malicious set produces `malicious` with confidence ≥ 0.7, an incomplete set produces `unknown` with confidence < 0.5, and no fixture ever yields an evidence reference that doesn't exist. These call the real API, so they're marked and kept out of CI.

---

## Scope

This is a focused engineering demonstration, not a complete security product. It handles **domains and URLs**, using two evidence sources that need no external API keys.

Deliberately left out, with reasoning in [§2 of the design document](ThreatGraph_Design_Document.md#2-mvp-scope--what-was-cut-and-why):

- Kubernetes, Terraform, and cloud deployment — Docker Compose describes the same architecture at zero setup cost
- Elasticsearch — PostgreSQL's trigram and full-text indexes cover the search requirements at this scale
- A web frontend — the backend is the artifact; Swagger exercises it fully
- Email and social-profile indicators — one complete vertical slice beats several partial ones

Planned extensions: TLS certificate inspection, WHOIS enrichment, and content-based signals. The `jsonb` evidence payload means new sources need no schema change.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
