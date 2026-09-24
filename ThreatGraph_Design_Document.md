# ThreatGraph

**AI-Powered Threat Intelligence & Investigation Platform**

Design Document — 3-Day Build Specification
Version 2.1 | September 2026

*Version 2.1 rewrites §12 from a plan into a record of what was built, and
corrects the data model to match the implementation.*

ThreatGraph turns a submitted digital indicator into structured, searchable, connected intelligence. This document defines the architecture, data model, AI integration, and build plan for a focused 3-day MVP that demonstrates a complete, production-oriented engineering stack.

| Layer | Technology |
|---|---|
| API framework | FastAPI + Python 3.12 |
| Async processing | Redis Streams + asyncio workers |
| Database | PostgreSQL 16 (pg_trgm + tsvector search) |
| AI integration | Anthropic Claude (`claude-opus-5`) with structured tool calling |
| Deployment | Docker Compose |
| Build target | 3 days — single vertical slice, production practices |

---

## Contents

1. [Project Overview](#1-project-overview)
2. [MVP Scope — What Was Cut and Why](#2-mvp-scope--what-was-cut-and-why)
3. [Technology Stack](#3-technology-stack)
4. [System Architecture](#4-system-architecture)
5. [Data Model](#5-data-model)
6. [Investigation Lifecycle](#6-investigation-lifecycle)
7. [Enrichment Pipeline](#7-enrichment-pipeline)
8. [AI Analyst](#8-ai-analyst)
9. [Event-Driven Processing](#9-event-driven-processing)
10. [Reliability](#10-reliability)
11. [API Reference](#11-api-reference)
12. [Build Record](#12-build-record)
13. [Testing Strategy](#13-testing-strategy)

---

## 1. Project Overview

ThreatGraph accepts digital indicators — domains and URLs — and builds a structured, searchable view of the infrastructure behind them.

This is a focused engineering demonstration, not a complete enterprise security product. Its purpose is to show how an AI reasoning layer can operate reliably inside an observable, production-oriented backend.

### Core design principle

**AI is used for reasoning, not as the source of truth.**

Deterministic enrichment services collect the evidence. The AI analyst receives that structured evidence and uses it to explain findings, identify relationships, and reach a conclusion. Every AI claim must cite a specific enrichment observation that exists in PostgreSQL — and that citation is verified against the database before the result is stored.

### Engineering disciplines demonstrated

- Async backend development with FastAPI and asyncio
- Event-driven processing with Redis Streams consumer groups
- Relational data modeling — entities, relationships, observations
- AI agent orchestration with structured tool calling and schema-validated output
- **Evidence grounding**: AI conclusions verified against real observation records before persistence
- Reliability engineering: database-enforced idempotency, staged retry, partial-failure classification
- Defensive input handling (SSRF protection on a service that fetches untrusted URLs)
- Containerised deployment with Docker Compose
- Structured logging with trace IDs propagated across every service

---

## 2. MVP Scope — What Was Cut and Why

The full product vision is a 3–6 month effort. For a 3-day build, scope was reduced by removing *infrastructure* complexity while keeping every architectural feature that demonstrates engineering depth.

### What was cut

| Component | Reason for removal |
|---|---|
| Kubernetes + Terraform + AWS | Docker Compose describes the same architecture at zero setup cost. K8s-readiness is shown through stateless service design, not by running a local cluster. |
| Elasticsearch | Replaced with PostgreSQL full-text search. Removes an operational dependency without losing the search engineering. The architecture stays Elasticsearch-ready as an optional read layer. |
| Prometheus + Grafana + OpenTelemetry | Replaced with structured JSON logging, trace IDs on every log line, and a `/metrics` endpoint in Prometheus text format. |
| Next.js frontend | FastAPI's Swagger UI fully exercises the API. The backend is the artifact. |
| Email and social-profile investigations | Domain and URL provide one complete, polished vertical slice. Other indicator types are documented as planned extensions. |

### What was kept

These carry the signal that makes ThreatGraph worth reading:

- **AI analyst with structured tool calling** — the defining feature.
- **Verified evidence grounding** — AI conclusions checked against real observation records, not just schema-validated.
- **Redis Streams** — real event-driven architecture with consumer groups, acknowledgment, and stalled-message recovery.
- **PostgreSQL relationship model** — entities, relationships, and investigations with explicit relationship types.
- **Investigation lifecycle state machine** — durable, resumable status transitions.
- **Idempotent workers** — safe to restart, enforced by database constraints rather than by convention.
- **Structured logging with trace IDs** — one investigation traceable across every service.

**The vertical slice principle:** one indicator enters the platform, evidence is collected and persisted, background workers process the investigation, and an AI analyst produces an evidence-grounded structured result. That single flow, executed reliably, shows more than a partial implementation of a sprawling stack.

---

## 3. Technology Stack

| Layer | Technology | Role |
|---|---|---|
| API | FastAPI | REST API, request validation, async route handlers |
| Async processing | Redis Streams | `XADD`/`XREADGROUP`, consumer groups, stalled-message recovery |
| Database | PostgreSQL 16 | Durable state, relationship model, search |
| Search | pg_trgm + tsvector | Indicator lookup and narrative search — no separate cluster |
| AI | Anthropic Claude (`claude-opus-5`) | Structured tool calling, schema-validated output |
| Workers | Python asyncio | Enrichment, analysis, and correlation workers |
| Containers | Docker Compose | API, workers, Redis, PostgreSQL in one file |
| CI/CD | GitHub Actions | Lint, test, and build on push |

### Key decisions

**PostgreSQL instead of Elasticsearch.** Two index types cover two distinct jobs. `pg_trgm` handles indicator lookup, where substring and fuzzy matching matter (`paypal` should match `paypal-secure-login.com`); `tsvector` handles the AI analyst's `explanation` field, which is the only genuine natural-language text in the system. Neither needs a separate cluster at this scale. The architecture stays Elasticsearch-ready if throughput demands grow.

**Docker Compose instead of Kubernetes.** The application is stateless (API and workers), with stateful components (PostgreSQL, Redis) treated as persistent infrastructure. That is the same separation Kubernetes enforces, described at zero overhead.

**Redis Streams instead of Kafka or RabbitMQ.** Redis Streams provides consumer groups, acknowledgment, and pending-entry inspection with minimal operational complexity — the right tool for this scale.

**Claude Opus 5.** Named explicitly rather than left as "an LLM," because model choice affects tool-calling reliability and cost. The system prompt and tool definitions are identical on every request, so they are marked for prompt caching; only per-investigation evidence varies.

---

## 4. System Architecture

Five responsibilities, each with a dedicated component: ingestion, enrichment, analysis, correlation, and persistence.

### Components

| Component | Responsibility |
|---|---|
| **FastAPI (API service)** | Accepts submissions, serves status and results, exposes search. Publishes tasks to Redis. Stateless — scales horizontally. |
| **Redis Streams** | Asynchronous task transport. Decouples the API from workers. Durable delivery with at-least-once semantics. |
| **Enrichment worker** | Runs DNS and HTTP enrichment in parallel. Upserts entities, writes observations (including failures), then publishes an `analyze` task. |
| **AI analyst worker** | Loads evidence, calls Claude with a 5-tool set, verifies the result against the database, persists it, then publishes a `correlate` task. |
| **Correlation worker** | Creates relationship records between entities discovered during enrichment. Marks the investigation complete. |
| **PostgreSQL** | Single source of truth for all durable state. |

Workers never call each other. They communicate by writing to PostgreSQL and publishing the next task to Redis.

### Request flow

```
Client                API              Redis           Workers            PostgreSQL
  │                    │                 │                │                    │
  ├─ POST ────────────>│                 │                │                    │
  │                    ├─ insert (pending) ──────────────────────────────────> │
  │<── investigation_id┤                 │                │                    │
  │                    ├─ XADD enrich ──>│                │                    │
  │                    │                 ├─ enrich ──────>│                    │
  │                    │                 │                ├─ entities + obs ──>│
  │                    │                 │<── XADD analyze┤                    │
  │                    │                 ├─ analyze ─────>│                    │
  │                    │                 │                ├─ Claude + verify ─>│
  │                    │                 │<─ XADD correlate                    │
  │                    │                 ├─ correlate ───>│                    │
  │                    │                 │                ├─ relationships ───>│
  │                    │                 │                ├─ complete ────────>│
  ├─ GET (poll) ──────>│                 │                │                    │
  │<── full result ────┤                 │                │                    │
```

1. Client sends `POST /investigations` with an indicator, e.g. `suspicious-domain.com`.
2. FastAPI validates the request, **derives `indicator_type` server-side**, saves the investigation with `status = pending`, and returns the ID immediately. No blocking.
3. FastAPI publishes an enrich task to the `tasks:enrich` stream.
4. The enrichment worker sets `status = enriching`, runs DNS and HTTP enrichment in parallel, upserts the entities it discovers, and saves every result — success or failure — as an observation.
5. The enrichment worker publishes an analyze task. The AI analyst loads the observations, calls Claude with its tool set, verifies the returned evidence references against the database, and persists the validated result.
6. The correlation worker creates relationship records between entities, then marks the investigation complete.
7. The client polls `GET /investigations/{id}` and receives the result, its supporting evidence, and all discovered relationships.

---

## 5. Data Model

All application state lives in PostgreSQL. Six tables take the system from a single indicator to a graph of connected infrastructure.

A note on the design: **uniqueness constraints do the work that would otherwise be left to worker discipline.** Every write in the system is an `INSERT ... ON CONFLICT`, so re-delivering a task can never create a duplicate row. Idempotency is a property of the schema, not of the code path.

### `investigations`

Primary record for each submitted indicator.

| Column | Description |
|---|---|
| `id` | `uuid` PK. Also the trace ID in all logs and worker messages. |
| `indicator` | The submitted value, e.g. `suspicious-domain.com`. |
| `indicator_type` | `domain` or `url`. **Derived server-side**, never trusted from the client. (`email`, `social` are future extensions.) |
| `status` | `pending`, `enriching`, `analyzing`, `correlating`, `complete`, `failed`, `cancelled`. |
| `enrichment_status` | `jsonb`. Per-source outcome, e.g. `{"dns": "ok", "http": "failed"}`. Read by the AI analyst so it knows what is missing. |
| `error` | Why the investigation failed, if it did. It stays queryable either way. |
| `created_at` / `updated_at` | Creation time and last status change. |

### `entities`

Deduplicated registry of observed indicators and infrastructure, shared across investigations. **Created during enrichment**, so observations and the AI analyst can reference them.

| Column | Description |
|---|---|
| `id` | `uuid` PK. |
| `type` | `domain`, `ip`, `url`, `certificate`. |
| `value` | `text`. |
| `metadata` | `jsonb`. Flexible extra data (ASN, registrar) without schema changes. |
| `first_seen` / `last_seen` | When this entity was first and most recently observed. |

**Unique on `(type, value)`** — not on `value` alone, so the same string can exist as different entity types, and so upserts have a constraint to target.

### `observations`

Every piece of enrichment evidence. The layer that AI conclusions must cite.

| Column | Description |
|---|---|
| `id` | `uuid` PK. Referenced by `result_evidence` to ground AI conclusions. |
| `investigation_id` | `uuid` FK → `investigations`. |
| `entity_id` | `uuid` FK → `entities`, nullable. The entity this evidence describes. |
| `source` | `dns`, `http`, `tls`, or `whois`. |
| `status` | `ok` or `failed`. Failed enrichment is recorded, not discarded. |
| `data` | `jsonb`. Normalised evidence payload, or an error payload when `status = failed`. |
| `collected_at` | When the evidence was collected. |

**Unique on `(investigation_id, source)`** — one observation per source per investigation, so redelivery is a no-op.

### `relationships`

Durable connections between entities. Enables infrastructure correlation across investigations.

| Column | Description |
|---|---|
| `id` | `uuid` PK. |
| `source_entity_id` | `uuid` FK → `entities`. |
| `target_entity_id` | `uuid` FK → `entities`. |
| `relationship_type` | `resolves_to`, `uses_certificate`, `redirects_to`, `hosted_on`. |
| `confidence` | `float` 0.0–1.0. **Assigned by deterministic rules, not by the AI** — a DNS A record yields `resolves_to` at 1.0; an inferred shared-hosting link scores lower. |
| `observed_at` | When this relationship was first recorded. |

**Unique on `(source_entity_id, target_entity_id, relationship_type)`.**

### `investigation_results`

The AI analyst's validated output. One per investigation.

| Column | Description |
|---|---|
| `id` | `uuid` PK. |
| `investigation_id` | `uuid` FK, **UNIQUE**. One-to-one with investigations. |
| `classification` | `benign`, `suspicious`, `malicious`, `unknown`. |
| `confidence` | `float` 0.0–1.0. |
| `explanation` | AI-generated narrative, indexed with `tsvector` for search. |
| `entity_ids` | `jsonb` array. UUIDs of entities relevant to this conclusion. |
| `recommended_action` | `monitor`, `block`, `escalate`, `no_action`, `request_review`. |
| `created_at` | When the result was persisted after verification. |

### `result_evidence`

The grounding link, as a junction table with real foreign keys.

| Column | Description |
|---|---|
| `result_id` | `uuid` FK → `investigation_results`, `ON DELETE CASCADE`. |
| `observation_id` | `uuid` FK → `observations`. |

Primary key `(result_id, observation_id)`.

> **Why a table instead of a JSON array of UUIDs:** a `jsonb` array can hold a UUID that does not exist. A foreign key cannot. Storing evidence references this way makes an ungrounded result *structurally impossible* rather than merely discouraged, and it turns "verify every evidence reference resolves to a real observation" from a test you have to remember to write into an invariant the database enforces on every insert.

### Schema (abbreviated)

```sql
CREATE TABLE entities (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type        text NOT NULL,
    value       text NOT NULL,
    metadata    jsonb NOT NULL DEFAULT '{}',
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (type, value)
);

CREATE TABLE observations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id uuid NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    entity_id        uuid REFERENCES entities(id),
    source           text NOT NULL,
    status           text NOT NULL DEFAULT 'ok',
    data             jsonb NOT NULL,
    collected_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (investigation_id, source)
);

CREATE TABLE relationships (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id uuid NOT NULL REFERENCES entities(id),
    target_entity_id uuid NOT NULL REFERENCES entities(id),
    relationship_type text NOT NULL,
    confidence       real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_entity_id, target_entity_id, relationship_type)
);

CREATE TABLE result_evidence (
    result_id      uuid NOT NULL REFERENCES investigation_results(id) ON DELETE CASCADE,
    observation_id uuid NOT NULL REFERENCES observations(id),
    PRIMARY KEY (result_id, observation_id)
);

-- Indicator lookup: substring and fuzzy matching
CREATE INDEX idx_entities_value_trgm
    ON entities USING gin (value gin_trgm_ops);

-- Narrative search over AI explanations
CREATE INDEX idx_results_explanation_fts
    ON investigation_results USING gin (to_tsvector('english', explanation));

-- Stalled-investigation sweep (see §10)
CREATE INDEX idx_investigations_active
    ON investigations (updated_at)
    WHERE status NOT IN ('complete', 'failed', 'cancelled');
```

---

## 6. Investigation Lifecycle

Every investigation moves through a defined set of statuses stored in PostgreSQL. Transitions are durable, so the system survives worker restarts.

### Status transitions

| Status | Set by | Trigger | Output |
|---|---|---|---|
| `pending` | API | `POST /investigations` | Investigation ID returned to client |
| `enriching` | Enrichment worker | `enrich` task consumed | Entities upserted; DNS + HTTP observations written |
| `analyzing` | AI analyst | `analyze` task consumed | Claude called; result verified and persisted |
| `correlating` | Correlation worker | `correlate` task consumed | Relationship records created |
| `complete` | Correlation worker | All stages successful | Result available via `GET /investigations/{id}` |
| `failed` | Any worker | Unrecoverable error, or retries exhausted | Error logged; investigation remains queryable |
| `cancelled` | API | `DELETE /investigations/{id}` | Workers stop at the next stage boundary |

### Conditional transitions

Status updates are conditional, which prevents a redelivered task from moving an investigation backwards:

```sql
UPDATE investigations
   SET status = 'enriching', updated_at = now()
 WHERE id = $1 AND status = 'pending';
```

**When this matches zero rows, the investigation has already moved on.** The worker logs the skip, acknowledges the message, and drops it. This is the normal, expected outcome of at-least-once delivery — not an error.

### Cancellation

`DELETE /investigations/{id}` sets `status = 'cancelled'` from any non-terminal state. Each worker re-reads the status at the start of its stage; if it is `cancelled`, the worker acknowledges the message and stops without publishing the next task. Work already in flight is allowed to finish, so cancellation takes effect at the next stage boundary rather than instantly.

### Partial failure handling

A failed enrichment step does not invalidate the investigation. Three failure modes are distinguished:

| Mode | Example | Handling |
|---|---|---|
| **Transient** | Network timeout, DNS SERVFAIL | Retried up to 3 times with exponential backoff |
| **Incomplete** | One source unavailable | Investigation continues with available evidence; the missing source is recorded in `enrichment_status` and passed to the AI analyst |
| **Permanent** | Invalid indicator format, schema violation | Marked `failed` immediately, no retry |

---

## 7. Enrichment Pipeline

Enrichment converts a raw indicator into structured evidence. The MVP uses two deterministic sources that need no external API keys.

### Evidence sources

| Source | Data collected | Library | Signal value |
|---|---|---|---|
| DNS | A, AAAA, MX, NS, TXT, CNAME records | `dnspython` | Resolved IPs, nameservers, SPF/DMARC policy |
| HTTP | Status code, headers, redirect chain, server fingerprint, page title | `aiohttp` | Technology stack, hosting provider, redirect behaviour |

### Parallel execution with recorded failures

DNS and HTTP run concurrently. If one fails, the other continues — and **the failure itself is written as an observation**, so the AI analyst can tell "HTTP returned nothing" apart from "HTTP was never attempted."

```python
dns_result, http_result = await asyncio.gather(
    enrich_dns(indicator),
    enrich_http(indicator),
    return_exceptions=True,
)

enrichment_status = {}
for source, result in (("dns", dns_result), ("http", http_result)):
    if isinstance(result, Exception):
        # Failures are evidence too. The analyst needs to know.
        await save_observation(db, investigation_id, source,
                               status="failed",
                               data={"error": type(result).__name__,
                                     "detail": str(result)})
        enrichment_status[source] = "failed"
    else:
        await save_observation(db, investigation_id, source,
                               status="ok", data=result)
        enrichment_status[source] = "ok"

await set_enrichment_status(db, investigation_id, enrichment_status)
```

`save_observation` is an `INSERT ... ON CONFLICT (investigation_id, source) DO UPDATE`, so a redelivered task overwrites rather than duplicates.

### Entity creation

The enrichment worker upserts every entity it discovers — the submitted indicator itself, plus each resolved IP address:

```sql
INSERT INTO entities (type, value, metadata)
VALUES ($1, $2, $3)
ON CONFLICT (type, value)
DO UPDATE SET last_seen = now(),
              metadata = entities.metadata || EXCLUDED.metadata
RETURNING id;
```

Entities exist before observations reference them and before the AI analyst runs. The correlation worker creates *relationships* between these entities; it does not create the entities themselves.

### SSRF protection

HTTP enrichment fetches URLs submitted by untrusted users, from a worker running inside the application's private network. Without guards, `http://redis:6379` or `http://169.254.169.254/` would be fetched happily. Every request is therefore checked:

1. Resolve the hostname before connecting.
2. Reject loopback, private, link-local, and multicast address ranges.
3. Re-run both checks after **every** redirect — not just the first request.
4. Cap the redirect chain at 5 hops.
5. Cap the response body at 1 MB and the total request at 10 seconds.
6. Allow only the `http` and `https` schemes.

### Future sources

The `jsonb` `data` column means new sources need no schema change. Planned: TLS certificate inspection (SANs, issuer, validity), WHOIS (registrar, registration date), and content-based signals.

---

## 8. AI Analyst

The AI analyst is the reasoning layer. It does not collect data — it interprets data collected by the deterministic pipeline, then produces a conclusion that is verified against the database before it is stored.

### Design principles

**Verified evidence grounding.** Every claim must cite specific observation UUIDs. Those UUIDs are checked against PostgreSQL — they must exist *and* belong to the current investigation. A conclusion citing an invented UUID is rejected and the model is asked to correct it.

**Constrained tool set.** Exactly 5 tools. A smaller surface produces cleaner decisions, easier auditing, and more predictable behaviour.

**No fact invention.** The system prompt prohibits stating facts absent from the enrichment data. Missing information produces a low-confidence `unknown`, not a fabricated answer.

**Bounded execution.** The agent loop is capped at 10 iterations and the Claude client has an explicit timeout, so a looping agent cannot occupy a worker indefinitely or silently double LLM spend.

**Single result call.** `create_investigation_result` is expected exactly once. Parallel tool use is disabled so the model cannot emit it alongside another call, and a second attempt returns an error result rather than crashing the worker.

### Tool set

| Tool | Purpose | When to call |
|---|---|---|
| `search_indicators` | Search existing entities and past investigations by value or keyword | First — detect previously seen infrastructure |
| `get_enrichment` | Retrieve all observations for an entity, including failed ones | Before drawing any conclusion about an entity |
| `get_related_entities` | Traverse the relationship graph from prior investigations | To detect shared infrastructure |
| `inspect_observation` | Retrieve full raw data for one observation | When summary data is insufficient |
| `create_investigation_result` | Write the final verified result | Exactly once, after sufficient evidence |

### Structured output schema

```python
class InvestigationResult(BaseModel):
    classification: Literal["benign", "suspicious", "malicious", "unknown"]
    confidence:     float     = Field(ge=0.0, le=1.0)
    explanation:    str       = Field(min_length=20, max_length=1000)
    entity_ids:     list[UUID] = Field(min_length=1)
    evidence_refs:  list[UUID] = Field(min_length=1)
    recommended_action: Literal[
        "monitor", "block", "escalate", "no_action", "request_review"
    ]
```

The tool definition is declared with `strict: true`, so the API guarantees the arguments match this schema. Pydantic then acts as a second, independent check rather than as the only line of defence.

### The grounding check

Schema validation confirms the shape of the data. It cannot confirm the *truth* of it: `list[UUID]` accepts any well-formed UUID, including one the model invented. The database check is what makes grounding real.

```python
async def create_investigation_result(db, investigation_id, **kwargs):
    result = InvestigationResult(**kwargs)          # 1. shape

    valid = await db.fetch_ids(                     # 2. existence + ownership
        "SELECT id FROM observations "
        "WHERE investigation_id = $1 AND id = ANY($2)",
        investigation_id, result.evidence_refs,
    )
    invalid = set(result.evidence_refs) - set(valid)
    if invalid:
        # Return an error tool_result. The model sees which refs were
        # rejected and re-answers using real evidence.
        return tool_error(
            f"These evidence_refs do not belong to this investigation: "
            f"{sorted(invalid)}. Cite only observation IDs returned by "
            f"get_enrichment."
        )

    await persist_result(db, investigation_id, result)   # 3. FK-backed insert
```

Three layers, each catching what the previous cannot:

| Layer | Catches |
|---|---|
| `strict: true` on the tool | Malformed arguments, wrong types, missing fields |
| Pydantic model | Value-range violations, enum violations |
| **Database verification** | **Hallucinated or cross-investigation evidence references** |

The `result_evidence` foreign key is the final backstop: even if all three checks were bypassed, an invalid reference cannot be written.

### Request construction

**Prompt caching.** The API renders requests as `tools` → `system` → `messages`. The 5 tool definitions and the system prompt are byte-identical on every investigation, so a cache breakpoint is placed after the system prompt and all per-investigation evidence goes after it. Cache effectiveness is verified by asserting `usage.cache_read_input_tokens > 0` across consecutive runs; a zero means something volatile (a timestamp, an unsorted dict) leaked into the prefix.

**Refusal handling.** An agent reasoning about malicious infrastructure can trip a safety classifier. A refusal returns HTTP 200 with `stop_reason: "refusal"` and no usable content, so `stop_reason` is checked *before* reading `content`. Server-side fallbacks are enabled, and a refused investigation is recorded as `unknown` with the refusal category logged — not as a crash.

### Typical tool-call sequence

A standard investigation uses 3–5 tool calls:

1. `search_indicators` — has this domain been seen before?
2. `get_enrichment` — retrieve DNS and HTTP observations for the primary entity.
3. `get_related_entities` — do the resolved IPs appear in earlier investigations?
4. `inspect_observation` *(optional)* — retrieve full raw header or certificate data.
5. `create_investigation_result` — write the verified conclusion.

### Demo seeding

`search_indicators` and `get_related_entities` both query history. On a freshly cloned database that history is empty, so the first investigation degrades to a two-call sequence and the most interesting capability — spotting shared infrastructure across investigations — never fires.

The repository therefore ships a seed fixture of four prior investigations sharing hosting infrastructure, loaded on first startup. A reviewer following the README sees the correlation behaviour on their first run.

---

## 9. Event-Driven Processing

Redis Streams provides the asynchronous layer. The API publishes tasks; workers consume them independently and chain the pipeline by publishing the next task.

### One stream per stage

| Stream | Consumer group | Consumed by | Reclaim threshold |
|---|---|---|---|
| `tasks:enrich` | `enrichment-cg` | Enrichment worker | 30s |
| `tasks:analyze` | `analyst-cg` | AI analyst worker | 300s |
| `tasks:correlate` | `correlation-cg` | Correlation worker | 30s |
| `tasks:dlq` | — | Manual inspection | — |

> **Why not a single stream with a `type` field?** Because Redis consumer groups do not filter by message content. **Every consumer group on a stream receives every message in that stream** — groups isolate *progress*, consumers within a group split *work*. With one shared stream, the analyst group would receive every enrich message, the correlation group would receive them too, and each would have to acknowledge and discard them. Any message a worker discarded without acknowledging would sit pending, be reclaimed, retried, and eventually land in the DLQ — so every task would generate spurious DLQ entries.
>
> Separate streams give real routing, and — just as importantly — a **per-stage reclaim threshold**. That matters because the stages have very different latency profiles: DNS and HTTP enrichment finish in seconds, while an agentic Claude loop making 3–5 tool calls routinely runs past 30. A single global threshold tuned for enrichment would reclaim healthy analyst tasks, start a second LLM call for the same investigation, and double the cost.

### Pipeline chaining

Each worker publishes the next stage's task when its own stage succeeds. No worker needs to know the overall pipeline shape beyond its immediate successor, and neither does the API.

### Recovering stalled messages

`XPENDING` lists messages delivered but not acknowledged. Each worker runs a reclaim pass against its own stream before each read:

1. Worker calls `XREADGROUP` to receive a task.
2. Worker processes the task and calls `XACK` on success.
3. If the worker crashes mid-processing, no `XACK` is sent and the message stays pending.
4. The reclaim pass calls `XPENDING` for messages idle beyond the stage threshold.
5. `XCLAIM` reassigns the message to an available worker in the same group. `XCLAIM` is atomic, so concurrent workers cannot both win the same message.
6. After 3 delivery attempts, the message is copied to `tasks:dlq` **and acknowledged on its original stream**, so it stops being reclaimed. The investigation is marked `failed`.

---

## 10. Reliability

### Idempotency is enforced by the schema

Workers use the investigation ID as the idempotency key, but they do not implement idempotency by checking whether a row exists first — that is a race, and two redelivered copies of a message can both pass the check and both insert.

Instead, every write targets a uniqueness constraint and uses `ON CONFLICT`:

| Write | Constraint |
|---|---|
| Entity upsert | `UNIQUE (type, value)` |
| Observation | `UNIQUE (investigation_id, source)` |
| Relationship | `UNIQUE (source_entity_id, target_entity_id, relationship_type)` |
| Investigation result | `UNIQUE (investigation_id)` |

Redelivery becomes a no-op at the database level. No coordination, no locks, no check-then-write window.

### Publish before acknowledge

The ordering of "publish the next task" and "acknowledge this one" decides whether failures lose work or duplicate it:

- **Acknowledge first, then publish** — a crash in between leaves no pending message and no next task. The investigation is stranded silently, forever.
- **Publish first, then acknowledge** — a crash in between causes the current task to be redelivered, which republishes the next task. A duplicate, which the constraints above absorb harmlessly.

ThreatGraph publishes first. Duplicates are safe here; lost work is not.

### Stalled-investigation sweeper

Redis recovers messages that are pending. It cannot recover an investigation whose message was already acknowledged but whose next stage never started — for instance if Redis itself dropped a write.

A periodic sweeper closes the gap:

```sql
SELECT id, status FROM investigations
 WHERE status NOT IN ('complete', 'failed', 'cancelled')
   AND updated_at < now() - interval '10 minutes';
```

Each match is republished to the stream for its current stage. Because every write is idempotent, replaying a stage that partly succeeded is safe.

### Observability

**Two trace IDs.** `investigation_id` traces an investigation across every service. But errors can occur before an investigation exists — a validation failure on `POST /investigations`, for example — so every request also carries a `request_id`, generated by middleware and attached to every log line and every error response. `investigation_id` is added once it exists.

**Metrics.** `/metrics` exposes investigation counts by status and per-stream queue depth as counters and gauges, and per-stage processing time as a **histogram** — latency has a distribution, and a counter cannot express one.

**Input limits.** `POST /investigations` is rate-limited per client. Without it, queue depth is unbounded and a single client can starve the workers.

---

## 11. API Reference

Eight endpoints across three concerns: investigation management, intelligence search, and operations. All responses use typed Pydantic models with a consistent error format.

| Method | Path | Description |
|---|---|---|
| `POST` | `/investigations` | Submit an indicator. Body: `{ indicator }`; `indicator_type` is derived server-side. Returns `investigation_id` immediately. Rate limited. |
| `GET` | `/investigations/{id}` | Status, result, and metadata. Poll until `complete` or `failed`. Includes `enrichment_status` so clients can see which sources succeeded. |
| `GET` | `/investigations/{id}/evidence` | All observations, successful and failed. Optional `?source=dns\|http\|tls\|whois`, `?status=ok\|failed`. |
| `GET` | `/investigations/{id}/entities` | Entities discovered during enrichment. Optional `?relationship_type=resolves_to\|...`. |
| `GET` | `/search` | Trigram indicator search plus full-text search over AI explanations. Params: `q` (required), `type`, `status`, `classification`, `limit`, `offset`. |
| `GET` | `/health` | Liveness check. Pings PostgreSQL and Redis; returns per-dependency status. Used by the Compose healthcheck. |
| `GET` | `/metrics` | Prometheus text format. Counts by status, queue depth per stream, stage-latency histogram. |
| `DELETE` | `/investigations/{id}` | Cancel an investigation. Workers stop at the next stage boundary. |

### Error handling

All endpoints return `{ "detail": "message", "code": "ERROR_CODE", "request_id": "..." }`. Validation errors return 422 with field-level detail; not found returns 404. Responses also carry `investigation_id` once one exists.

---

## 12. Build Record

Three phases, each ending at a testable milestone. This section was written as a
plan and has been rewritten as a record: every item below was built, and the
notes describe what the plan got wrong.

Commits: `7e1dd0f` (Day 1), `33d9b9f` and `ca62063` (Day 2), `3a752c5` (Day 3).

### Day 1 — Foundation

*Goal: the backend skeleton runs in Docker Compose. An investigation can be submitted and its status retrieved. No enrichment or AI yet.*

- [x] PostgreSQL schema: 6 tables, all uniqueness constraints, trigram and FTS indexes
- [x] FastAPI application: routes, Pydantic models, dependency injection for DB and Redis
- [x] `POST /investigations` and `GET /investigations/{id}` end to end
- [x] Server-side `indicator_type` derivation and Redis-backed rate limiting
- [x] Redis plumbing: three streams, three consumer groups
- [x] Lifecycle state machine with conditional transitions and the zero-rows-matched skip path
- [x] Docker Compose with health checks and a one-shot migration service
- [x] Structured JSON logging with `request_id` and `investigation_id`

**Milestone met:** submit a domain, watch the task chain through all three stages to `complete` in ~360 ms, with one investigation ID tying together log lines from the API and every worker.

**What running it exposed.** `redis-py`'s default socket timeout is shorter than
`XREADGROUP BLOCK 5000`, so every idle poll raised `TimeoutError` — an error with
a full traceback every five seconds, per worker. Nothing was functionally broken,
which is what made it worth fixing immediately: it would have buried every real
error in noise. The socket timeout is now sized to the block duration plus a
margin.

### Day 2 — Intelligence

*Goal: a submitted domain is enriched, analysed, and correlated. The full result is queryable with verified evidence references.*

- [x] DNS enrichment via `dnspython`; NXDOMAIN recorded as evidence, not as failure
- [x] HTTP enrichment via `aiohttp` with manual redirect following and SSRF guards
- [x] Parallel enrichment; failures written as observations and into `enrichment_status`
- [x] Entity upsert during enrichment
- [x] AI analyst: 5 strict-mode tools wired to real queries, prompt caching, iteration cap, refusal handling
- [x] Grounding verification in `create_investigation_result`, with the error-and-retry path
- [x] Correlation worker: relationships with rule-based confidence
- [x] `GET /investigations/{id}/evidence` and `/entities`

**Milestone met:** a URL investigation collects DNS and HTTP evidence, the analyst cites both, and every citation resolves to a real observation belonging to that investigation.

**Deviation: the analyst was built against a swappable client.** The project was
developed without an API key, so the analyst sits behind a `ClaudeClient`
protocol with two implementations — the Anthropic SDK, and a scripted client
that replays turns. The loop, tools, grounding check and persistence are
identical under both. This began as a workaround and turned out to be the right
structure regardless: failure paths that are awkward to elicit from a live model
on demand — a hallucinated citation, a refusal, a model that never concludes —
became ordinary deterministic tests. Scripted conclusions are prefixed `[stub]`
so they cannot be mistaken for model output.

**Consequence: the Anthropic client has never run.** It is written and wired, and
its request shape follows the documented Messages API, but the first live request
is the real test of it. `§13`'s AI evaluation tier is the only place that gap
closes.

**What running it exposed.**

- The connection pool installs a `jsonb` codec that encodes on the way out, so
  passing `json.dumps()` output stored JSON strings *containing* JSON text. Every
  `enrichment_status` read returned a 500.
- `get_enrichment` returned only the requested entity's observations. On a URL
  investigation DNS attaches to the domain entity and HTTP to the URL entity, so
  the analyst cited one source while `enrichment_status` showed two — it could
  silently miss evidence collected for its own investigation. The tool now always
  returns the complete citable set.
- Migration `002`: `result_evidence.observation_id` needed `ON DELETE CASCADE`
  (deleting an investigation deadlocked against its own cascade), and `confidence`
  was `real`, serving 0.6 to clients as `0.6000000238418579`.

### Day 3 — Reliability and Demonstration

*Goal: a reviewer clones the repo, runs `docker compose up`, submits an investigation, and sees a grounded result with supporting evidence.*

- [x] Reclaim loop: `XPENDING` + `XCLAIM` with per-stage thresholds, DLQ after 3 attempts
- [x] Stalled-investigation sweeper
- [x] Search endpoint: trigram indicator lookup plus FTS over explanations
- [x] `/health` and `/metrics`, with stage latency as a histogram
- [x] Seed fixture: 4 prior investigations, 3 sharing one address
- [x] End-to-end test: submit, poll to `complete`, verify every citation resolves
- [x] AI evaluation fixtures (marked `ai_eval`, excluded from the default run)
- [x] GitHub Actions CI: lint, test against real PostgreSQL and Redis, image build, full-stack end-to-end
- [x] README with architecture overview, setup, and a walkthrough
- [x] Code cleanup: consistent error handling, type hints throughout, `ruff check` and `ruff format` clean

**Milestone met:** verified from an empty volume — `docker compose down -v`, then
`up`, submit, poll, receive a classification whose citations all resolve.

**The plan under-specified recovery.** It treated stalled work as one problem;
it is two, and they need different mechanisms.

A worker can die *holding* a message — delivered but never acknowledged, so
`XREADGROUP >` will never offer it again. That is what `XPENDING` + `XCLAIM`
recover. But a worker can also die *after* acknowledging and before publishing
the next stage's task: nothing is pending, nothing is in any stream, and Redis
cannot see the problem at all. Only the database knows, which is why the sweeper
reads `investigations` rather than any queue. Both were verified against a real
investigation stranded in `correlating` for 23 hours, not a contrived one.

**What running it exposed.**

- Dead-lettering a task left its investigation non-terminal, so the sweeper
  republished it every 60 seconds forever. Giving up on a task now marks the
  investigation failed too, and the DLQ decision moved from `TaskStream` to the
  worker — the only layer holding both Redis and the database.
- A malformed stored payload crash-looped through all three retries. Correlation
  now raises `PermanentError` on structurally invalid observation data; retrying
  cannot fix data that is already written.
- `pip install .` failed outside the container. The Dockerfile copies
  `pyproject.toml` before the source, so setuptools had nothing to auto-discover;
  CI checks out everything first and hit the flat-layout error. ThreatGraph is an
  application rather than a library, so it now declares no packages and runs from
  the repository root, as the container does.

### Definition of done

A reviewer runs `docker compose up`, submits `POST /investigations` with a
domain, polls `GET /investigations/{id}` until `status = complete`, and receives
a classification whose evidence references resolve to real enrichment
observations.

**Met, and enforced.** The CI pipeline's end-to-end job performs exactly that
sequence on every push and fails the build if any citation does not resolve to an
observation belonging to that investigation.

---

## 13. Testing Strategy

Four levels, plus deterministic fixtures for the AI component.

### Unit tests

*Individual functions, no I/O.*

- DNS record parsing and HTTP header normalisation
- Pydantic schema validation: valid and invalid result structures
- Status transition logic: valid transitions pass, invalid ones are rejected
- SSRF guard: private, loopback, and link-local addresses are refused — **including after a redirect**
- `indicator_type` derivation from raw input

### Integration tests

*Service interactions against real test databases.*

- PostgreSQL: write and retrieve observations; trigram and FTS search return expected results
- Redis: `XADD`, `XREADGROUP`, `XACK`, `XPENDING` detection after a missed acknowledgment
- **Stream isolation**: a message on `tasks:enrich` is never delivered to `analyst-cg`
- **Idempotency**: delivering the same task twice produces exactly one observation row
- Grounding rejection: a result citing a foreign investigation's observation UUID is refused

### Reliability tests

*The claims that most need proof.*

- **Crash recovery**: kill the enrichment worker mid-investigation; assert the investigation still reaches `complete` and that observations are not duplicated
- **Stalled sweep**: acknowledge a task without publishing the next one; assert the sweeper republishes it
- **DLQ**: force 3 consecutive failures; assert the message lands in `tasks:dlq` and stops being reclaimed

> "Idempotent workers — safe to restart" is one of the headline claims in §2. These are the tests that make it a demonstrated property rather than an assertion.

### End-to-end test

*Full flow against a running Compose stack.*

- Submit `POST /investigations` with a controlled test domain
- Poll until `status = complete` (60s timeout)
- Assert `classification` is one of the four valid values
- Assert evidence references are non-empty and all resolve to real observations
- Assert `GET /investigations/{id}/evidence` returns records matching those references

### AI evaluation fixtures

*Deterministic validation of agent behaviour.*

- Known-malicious evidence set → `classification = malicious`, `confidence >= 0.7`
- Known-benign evidence set → `classification != malicious`
- Incomplete evidence (HTTP recorded as failed) → `classification = unknown`, `confidence < 0.5`
- All fixtures → every evidence reference exists in the fixture observation set; no invention
- Grounding retry → a forced invalid reference produces a tool error and a corrected second attempt

These fixtures call the live API, so they are nondeterministic and cost money. They run under a `pytest` marker excluded from the default CI run and execute on demand or on a schedule, with responses recorded for regression comparison.

---

## Appendix: Changes from Version 1.0

| Area | v1.0 | v2.0 | Reason |
|---|---|---|---|
| Stream topology | One `tasks` stream, three consumer groups | Three streams, one group each | Consumer groups do not filter by content — every group received every message |
| Retry threshold | 30s, global | Per stage: 30s enrich, 300s analyze | A Claude agent loop exceeds 30s routinely; the global threshold reclaimed healthy work and doubled LLM cost |
| Entity creation | Correlation worker | Enrichment worker | Observations carry an entity FK and the analyst needs entity IDs — both run before correlation |
| Evidence grounding | Pydantic `min_length=1` | Database existence + ownership check, with FK-backed storage | Schema validation cannot detect a well-formed but invented UUID |
| Failed enrichment | Discarded | Written as an observation with an error payload | v1.0 promised the analyst would be told which sources were missing, but discarded the evidence of failure |
| Idempotency | Check-then-write in the worker | Uniqueness constraints + `ON CONFLICT` | Check-then-write is a race under concurrent redelivery |
| Ack/publish order | Unspecified | Publish, then acknowledge, plus a stalled sweeper | Acknowledging first strands investigations permanently on a crash |
| `entities` uniqueness | `UNIQUE (value)` | `UNIQUE (type, value)` | The same string may be more than one entity type, and upsert needs a constraint to target |
| Model | "Anthropic Claude" | `claude-opus-5`, with prompt caching, `strict: true`, bounded iterations, refusal handling | Model choice and request shape affect reliability and cost |
| HTTP enrichment | No input restrictions | SSRF guards, redirect cap, size and time limits | The worker fetches untrusted URLs from inside the private network |
| Search | "tsvector + pg_trgm" | Trigram for indicators, FTS for explanations | Each index has a distinct job; indicators tokenize poorly under FTS |
| Trace ID | `investigation_id` only | `request_id` + `investigation_id` | Pre-insert errors have no investigation to reference |
| Latency metric | Counter | Histogram | Latency has a distribution |
| Retry implementation | Day 2 | Day 3 | Day 2 held the entire AI layer; thresholds cannot be tuned before the analyst runs |
| Demo data | None | Seed fixture of 4 prior investigations | Two of five tools return nothing on a cold database |
| `investigations.search_vector` | tsvector column | Removed; trigram index on `indicator` | §3 already argued trigram for indicators and FTS only for explanations — the column contradicted it. Corrected in v2.1 during Day 1. |
