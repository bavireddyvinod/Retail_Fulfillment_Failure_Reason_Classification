# Retail Fulfillment Failure Reason Classification

An operations case desk and multi-agent investigation API for retail fulfillment exceptions. The local demo works without an OpenAI key and uses deterministic operational adapters; OpenAI classification and Chroma-backed policy retrieval activate when `OPENAI_API_KEY` is configured.

## Run locally

Start the full stack with Docker Compose:

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

Open the case desk at `http://localhost:3000` and the API docs at `http://localhost:8000/docs`. Without an OpenAI key, the investigation graph uses evidence rules and the bundled SOP passages. The demo order `ORD-10482` reproduces a carrier weather delay; `ORD-10999` exercises inventory unavailability.

For frontend-only work, run `cd web && npm ci && npm run dev`. For the API without containers, install `backend/requirements.txt`, set `DATABASE_URL` to a local SQLite URL, then run `uvicorn app.main:app --reload` from `backend`.

## Architecture

- `web/`: responsive Next.js case desk, deployable as a Vercel project with its root directory set to `web` and `NEXT_PUBLIC_API_URL` set to the public API origin.
- `backend/app/workflow.py`: shared typed state and LangGraph supervisor, triage, order, inventory, shipment, policy RAG, classification, human approval, recommendation, validation, and response nodes.
- `backend/app/tools.py`: LangChain tools for order/customer/events, inventory/movements/reservations, carrier tracking/exceptions, tickets, escalation, notification, and reports.
- `backend/app/rag.py`: SOP ingestion and metadata, chunking, Chroma/OpenAI embeddings, metadata filters, MMR + lexical rank fusion, reranking, context compression, and citation records.
- `backend/app/models.py`: PostgreSQL/SQLAlchemy tables for users, sessions, messages, workflows, agent runs, tool calls, failures, reports, approvals, audit logs, documents, chunks, and evaluations.
- Redis stores a short-lived shared dashboard snapshot. Workflow state, tool results, approvals, and audit events are persisted to PostgreSQL.
- `/api/evaluations/run` executes five golden classification cases and records scores; `/api/evaluations/summary` exposes operational quality aggregates.

## API

- `GET /api/health`
- `GET /api/dashboard`
- `POST /api/investigations` with `{ "query": "...", "order_id": "ORD-10482" }`
- `GET /api/investigations/{workflow_id}`
- `POST /api/investigations/{workflow_id}/approval` with `approved` or `rejected`
- `GET /api/evaluations/summary`
- `POST /api/evaluations/run`

## Validation

```bash
cd backend && python -m unittest discover -s tests -v
cd web && npm ci && npm run build
```

## Deployment boundary

The included operational tools return deterministic sample data and simulated ticket/notification references. Replace these adapters with authenticated OMS/WMS/ERP/carrier integrations and idempotent action APIs before connecting live systems. Configure identity, role-based access, secret management, database backups, retention, rate limits, and deployment-specific CORS at the API gateway before production use. Do not expose service credentials in `NEXT_PUBLIC_*` variables. Financial, compensation, SLA-penalty, and low-confidence paths must remain reviewer-gated.