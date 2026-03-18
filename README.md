# Invoice Pipeline

A production-grade, event-driven data pipeline that syncs invoice data from QuickBooks into Supabase in real time and surfaces it on a live Next.js dashboard. Built as a consulting-ready solution for financial data monitoring at Outpost.

---

## Architecture

![Invoice Pipeline Architecture](docs/architecture.svg)

---

## How it works

1. An invoice changes in QuickBooks
2. QuickBooks fires a webhook to the Render-hosted endpoint instantly
3. The endpoint verifies the HMAC-SHA256 signature and triggers the pipeline in the background
4. The pipeline fetches only changed records (incremental sync via watermark), transforms them, and upserts into Supabase
5. Supabase Realtime broadcasts the change to the Next.js dashboard via websocket
6. The dashboard updates without any page refresh — end-to-end latency under 5 seconds

---

## Project Structure

```
invoice-pipeline/
├── pipeline/
│   ├── __init__.py
│   ├── fetch.py          # QuickBooks OAuth + paginated fetch + exponential backoff
│   ├── sync.py           # Transform + upsert + watermark + sync run tracking
│   ├── validate.py       # Post-sync count reconciliation
│   └── webhook.py        # FastAPI webhook endpoint (deployed on Render)
├── dashboard/            # Next.js app
│   ├── app/
│   │   └── page.tsx      # Main dashboard — sorting, search, export, aging buckets
│   └── lib/
│       └── supabase.ts   # Supabase client
├── docs/
│   └── architecture.svg  # System architecture diagram
├── schema.sql            # Complete production database schema
├── Procfile              # Render start command
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
├── .gitignore
└── README.md
```

---

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- QuickBooks Developer account (sandbox)
- Supabase project
- Render account (for webhook deployment)

### 1. Clone the repository

```bash
git clone https://github.com/WassayS/invoice-pipeline.git
cd invoice-pipeline
```

### 2. Set up Python environment

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Fill in your values in `.env`:

```
QB_CLIENT_ID=your_quickbooks_client_id
QB_CLIENT_SECRET=your_quickbooks_client_secret
QB_REALM_ID=your_quickbooks_realm_id
QB_ACCESS_TOKEN=your_quickbooks_access_token
QB_REFRESH_TOKEN=your_quickbooks_refresh_token
QB_WEBHOOK_VERIFIER_TOKEN=your_quickbooks_webhook_verifier_token
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_KEY=your_supabase_service_role_key
ENVIRONMENT=development
```

### 4. Set up the database

Run `schema.sql` in your Supabase SQL editor. This creates all tables, indexes, triggers, RLS policies, and the audit log in a single atomic transaction.

### 5. Run the pipeline manually

```bash
python -m pipeline.sync
```

### 6. Deploy the webhook to Render

Push to GitHub — Render auto-deploys on every commit. Set all environment variables in the Render dashboard. Register the webhook URL in your QuickBooks developer app:

```
https://your-render-url.onrender.com/webhook/quickbooks
```

### 7. Run the dashboard

```bash
cd dashboard
npm install
npm run dev
```

Open `http://localhost:3000`

---

## Pipeline Design Decisions

### Webhook-driven over polling

The pipeline is triggered by QuickBooks webhook events rather than a scheduled cron job. When an invoice changes in QuickBooks, the event arrives at the Render endpoint within seconds. Every webhook payload is verified using HMAC-SHA256 against the verifier token — unsigned payloads are rejected with 401 before any processing occurs.

**Why not polling?** Polling wastes API quota on unchanged data and introduces artificial latency. A webhook delivers the event the moment it happens. For a finance team monitoring receivables, the difference between 5 seconds and 5 minutes matters.

### Incremental sync via watermark

Every run fetches only invoices where `MetaData.LastUpdatedTime > last_synced_at`. The watermark is stored in the `sync_watermarks` table and only advances on a successful run. A failed run never moves the watermark forward — ensuring no data is silently skipped on retry.

**Why not full sync every run?** QuickBooks enforces a 500 requests/minute rate limit. Full sync on a large account with thousands of invoices would hit this ceiling and degrade pipeline reliability significantly.

### Exponential backoff with full jitter

Failed API calls are retried up to 4 times with exponentially increasing wait times. Full jitter (randomised within `[0, exponential_wait]`) prevents the thundering herd problem — multiple retrying clients spreading their retries across time rather than all hitting simultaneously. The `Retry-After` header from QuickBooks 429 responses is respected directly.

**Why not a fixed retry interval?** Fixed intervals don't respect the server's state. If QuickBooks is under load, hitting it at fixed intervals makes the problem worse. Exponential backoff with jitter is the industry standard for API resilience.

### UPSERT over DELETE + INSERT

Every write uses `INSERT ... ON CONFLICT DO UPDATE`. This guarantees idempotency — running the pipeline twice on the same data produces exactly the same result with no duplicates.

**Why not DELETE + INSERT?** Creates a window where the table is empty, breaks the audit trail, and is not atomic. A network failure mid-delete would leave the table in a corrupt state.

### Status derivation (Paid / Partial / Unpaid)

QuickBooks does not have an explicit status field. Status is derived from the `Balance` field:
- `Balance == 0` → Paid
- `0 < Balance < TotalAmt` → Partial
- `Balance == TotalAmt` → Unpaid

**Why three states?** A partially paid invoice is materially different from an unpaid one. Collapsing them into a single "Unpaid" state would mislead the finance team about actual cash position.

### `id` stored as `text` not `integer`

QuickBooks returns IDs as strings in their API response. Storing as `integer` would silently break if QuickBooks ever uses alphanumeric IDs — a change entirely outside our control.

### Service role key for pipeline, anon key for dashboard

Supabase has two key types. The service role key bypasses Row Level Security and is used exclusively by the backend pipeline on Render. The anon key is subject to RLS policies and is the only key exposed to the Next.js frontend. The service role key never appears in any frontend code or git history.

### Soft deletes only

Financial records are never hard deleted. When an invoice is deleted in QuickBooks, `deleted_at` is set. The record remains in the database indefinitely for audit and compliance purposes. The dashboard queries the `active_invoices` view which filters `WHERE deleted_at IS NULL`.

### Audit log immutability

The `audit_log` table has a database trigger that raises an exception on any `UPDATE` or `DELETE` attempt. `TRUNCATE` is revoked from all non-superuser roles. Every write is captured with a SHA-256 checksum using sorted key=value pairs — stable across Postgres versions and tamper-evident.

---

## Database Schema

Six tables with full production constraints:

| Table | Purpose |
|---|---|
| `invoices` | Core invoice records synced from QuickBooks |
| `invoice_line_items` | Individual line items per invoice |
| `sync_runs` | One row per pipeline execution — full observability |
| `sync_watermarks` | Incremental sync state per entity type |
| `audit_log` | Immutable change log with SHA-256 checksums |
| `failed_records` | Dead letter queue — no record is silently dropped |

Key constraints and design choices:
- `CHECK (balance <= amount)` — financial integrity enforced at DB level
- `CHECK (due_date >= issue_date)` — logical consistency
- `CHECK (status IN ('Paid','Unpaid','Partial','Voided'))` — strict enum enforcement
- `CHECK (detail_type != 'SubTotalLineDetail')` — prevents QB subtotal rows in line items
- `created_at` is immutable after insert — enforced by trigger, not application code
- `synced_at` auto-updates on every upsert — enforced by trigger
- Partial indexes on active records — queries for active unpaid invoices use a smaller, faster index

---

## Dashboard Features

- Live summary stats — total, collected, outstanding, paid, overdue, collection rate
- Alert bar — automatically appears when 90+ day overdue invoices exist
- Top 3 debtors — customers with highest outstanding balances
- Sortable columns — click any column header to sort ascending or descending
- Search — filter by customer name or invoice number in real time
- Aging buckets — Current / 1–30d / 31–60d / 61–90d / 90d+ (standard AR format)
- Risk tiers — low / medium / high / critical derived from days overdue
- Export CSV — one click downloads current view as a dated CSV file
- Realtime — Supabase websocket subscription, no page refresh needed

---

## Observability

Every pipeline run is recorded in `sync_runs`:

```sql
SELECT run_id, status, records_fetched, records_upserted,
       duration_ms, watermark_from, watermark_to, triggered_by
FROM sync_runs
ORDER BY started_at DESC
LIMIT 10;
```

This answers:
- When did the pipeline last run?
- Was it triggered by a webhook or manually?
- How long did it take?
- How many records were processed?
- What time window did it cover?
- Did it succeed, fail, or partially complete?

---

## Validation

After every sync run, the pipeline automatically compares QuickBooks total invoice count against Supabase:

```
QuickBooks count : 31
Supabase count   : 31
Validation PASSED — counts match
```

A mismatch triggers a failure log and blocks the watermark from advancing — the next run will re-fetch the full window.

---

## Security

- All credentials in environment variables — never in code or git history
- `.env` listed in `.gitignore` — cannot be accidentally committed
- Webhook signature verified with HMAC-SHA256 before any processing
- Service role key used only in Render backend — never in frontend
- Anon key for dashboard — restricted by RLS policies
- RLS enabled on all six tables with explicit anon block policies
- Audit log immutable at database level — triggers block mutation
- SHA-256 checksums on every audit entry — tamper detection
- `TRUNCATE` revoked from all non-superuser roles

---

## Known Limitations and Future Work

**Refresh token rotation**
Refresh tokens expire after 101 days. Currently requires manual regeneration via the OAuth Playground. Automatic rotation — using each refresh token to generate a new one and persisting it back to a secrets manager — is planned for production deployment.

**Full test suite**
Transformation logic is tested manually against live sandbox data. A full suite of unit tests (mocked QB responses), integration tests (against sandbox), and contract tests (QB API schema) is planned before any production client deployment.

**Environment separation**
The pipeline supports `development`, `staging`, and `production` via the `ENVIRONMENT` variable. Separate Supabase projects and QB apps per environment are recommended before onboarding a real client.

**Natural language querying**
A planned AI layer will allow the finance team to type queries in plain English — "show unpaid invoices from Amy's Bird Sanctuary over $500" — converted to SQL via an LLM call and executed against Supabase. This is where the AI-native aspect of the role becomes directly applicable.

**Scheduled full reconciliation**
The current watermark-based sync can miss backdated invoices in QuickBooks. A nightly full count reconciliation (comparing QB total vs Supabase total) would catch these edge cases automatically.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Data source | QuickBooks Online API (OAuth 2.0) |
| Webhook server | FastAPI on Render (free tier) |
| Pipeline | Python 3, requests, supabase-py |
| Database | Supabase (Postgres 15 + Realtime) |
| Dashboard | Next.js 14, TypeScript, JetBrains Mono |
| Auth | Supabase RLS + QuickBooks OAuth 2.0 |
| Deployment | Render (webhook), Vercel (dashboard) |
