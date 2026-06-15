# PanelReview

A multi-agent code review platform. Paste code, upload a file, or link a GitHub URL — five AI agents, each embodying a distinct senior engineer persona, independently review your code and stream their verdicts live. A Synthesis Agent reconciles their findings, surfaces conflicts where agents disagree, and produces a severity-ranked verdict.

---

![PanelReview Landing Page](docs/screenshot-landing.png)
<!-- Replace with an actual screenshot once available -->

---

**Contents**

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Agent Personas](#agent-personas)
- [Architecture](#architecture)
- [Architectural Decisions](#architectural-decisions)
- [Tech Stack](#tech-stack)
- [Features](#features)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Project Structure](#project-structure)
- [Deployment (Railway)](#deployment-railway)
- [Known Limitations](#known-limitations)
- [License](#license)

## Overview

PanelReview treats a code review as a structured debate between five opinionated engineers who never met. Each agent works from the same code independently, reports their findings without influence from the others, and streams results the moment they finish. The Synthesis Agent then steps in as a tech lead — identifying where multiple agents agree (high signal), where only one flags something (worth considering), and where they explicitly contradict each other (interesting conflict worth understanding).

The result is a conflict-first severity-ranked issue list and a permanent shareable link — all without requiring an account.

## How It Works

1. **Submit** — paste code, upload a file (up to 100KB), or provide a GitHub URL (single file, or best file auto-selected from a repo)
2. **Watch** — five agents run sequentially and stream their verdicts live over WebSocket as each one finishes
3. **Read** — the Synthesis Agent merges findings, marks issues agreed on by 2+ agents as Critical, and surfaces explicit conflicts
4. **Share** — copy a permanent public link to any review

## Agent Personas

| Agent | Persona | What They Look For |
|---|---|---|
| **Pragmatist** | Staff Backend Engineer | Scalability, production readiness, architecture smells |
| **Paranoid** | Penetration Tester | Injection, hardcoded secrets, auth flaws, info leakage |
| **Minimalist** | Clean Code Evangelist | Dead code, SRP violations, complexity, poor naming |
| **Optimizer** | Performance Engineer | N+1 queries, O(n²) loops, missing indexes, memory leaks |
| **Mentor** | Senior Onboarding Engineer | Teachable anti-patterns, unclear intent, missing tests |
| **Synthesis** | Tech Lead | Cross-agent reconciliation, severity ranking, conflict surfacing |

## Architecture

### System Overview

```
Browser
  │
  ├── HTTP (REST)  ──► Django / DRF  ──► PostgreSQL
  │                        │
  │                    Celery Task (Redis broker)
  │                        │
  │                   LangGraph Graph
  │              (sequential — one agent at a time)
  │    Pragmatist → Paranoid → Minimalist → Optimizer → Mentor
  │                        │
  │                   Synthesis Agent
  │                        │
  └── WebSocket ◄── Django Channels (Redis channel layer)
```

Each agent broadcasts its verdict the moment it finishes. The frontend renders each agent card as it arrives — one by one in real time. Synthesis runs after all five and sends the final verdict.

### Request Lifecycle

```
POST /api/reviews/
  → Input handler (paste / file / GitHub)
  → Review row created (status: pending)
  → Celery task dispatched
  → 201 {"review_id": "..."}

WebSocket ws/reviews/{id}/
  → Consumer subscribes to group review_{id}
  → Replays event_log for late connections

Celery worker
  → status: running
  → LangGraph graph.invoke()
  → 5 agents run sequentially
  → Each agent: calls Groq, parses JSON, broadcasts agent_done
  → Synthesis: merges results, broadcasts synthesis_done + done
  → status: done, completed_at set
```

### Data Flow

```
raw_code + language + filename
      │
      ▼
ReviewState (TypedDict)
      │
   pragmatist → paranoid → minimalist → optimizer → mentor
                                                       │
                                                  synthesis
                          │
                    Review.synthesis (JSONB)
                    Review.agent_results (JSONB)
                    Review.event_log (JSONB list)
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| `apps/input_handler/` | Sanitize input, enforce line limits, detect language, fetch GitHub files |
| `pipeline/graph.py` | Build and compile the LangGraph state machine |
| `pipeline/agents/` | Per-agent LLM invocation, result parsing, WebSocket broadcast, DB write |
| `worker/tasks.py` | Celery entry point; owns review lifecycle (status transitions, error handling) |
| `ws/consumers.py` | WebSocket lifecycle; event replay for late-connecting clients |
| `ws/middleware.py` | JWT authentication for WebSocket connections (reads `?token=` query param) |
| `apps/reviews/views.py` | DRF views for submit, detail, share, history |

## Architectural Decisions

### Why LangGraph for the agent pipeline?

The five-agent pipeline is a directed acyclic graph: five sequential nodes feeding into one aggregation node. LangGraph's `StateGraph` expresses this directly — a chain of `add_edge` calls, compiled once at startup and reused for every review. Alternatives (raw loops, Celery chains, custom orchestration) require manual state passing and error propagation that LangGraph handles structurally.

The pipeline runs sequentially (not in parallel) to stay within Groq's free-tier TPM limits and to give the frontend a live one-card-at-a-time update experience. If billing is enabled, switching back to parallel fan-out is a one-line change in `graph.py`.

### Agent Execution Model: Parallel → Sequential

The pipeline was initially designed to run all five agents in parallel. In practice it immediately hit Groq's 12,000 TPM limit — five agents firing simultaneously consumed roughly 4,000–5,000 tokens per second, exhausting the per-minute bucket and causing the 4th and 5th agents to 429 on every run.

Switching to sequential execution spaces requests across 30–60 seconds, well within the TPM window. There is a side benefit: the frontend renders agent cards one at a time as each agent finishes, giving the impression of a live debate rather than a simultaneous data dump.

### Rate Limit Resilience: SDK-Level Retry

Even with sequential execution, the Groq free tier (5 RPM / 12,000 TPM) can still be exceeded — particularly by the synthesis node, which receives all five agent results as input.

Measured token usage for a typical review:

| Call | Input tokens | Output tokens |
|---|---|---|
| Agent 1 (pragmatist) | ~798 | ~673 |
| Agent 2 (paranoid)   | ~830 | ~650 |
| Agent 3 (minimalist) | ~835 | ~622 |
| Agent 4 (optimizer)  | ~853 | ~494 |
| Agent 5 (mentor)     | ~871 | ~796 |
| Synthesis            | ~3,550 | ~1,405 |
| **Total**            | **~7,737** | **~4,640** |

The Groq Python SDK handles 429s transparently: on a rate-limit response it reads the `retry-after` header and re-issues the request automatically. The Celery task retry (`max_retries=2`) acts as a final safety net.

### LLM Provider: Gemini → Groq

The pipeline was originally written against the Google Gemini API (`gemini-1.5-flash`). In production, every call returned a `limit: 0` error; upgrading to `gemini-2.0-flash` hit a deprecation wall (June 1 2026). Both are free-tier access restrictions.

Groq was chosen as the replacement: its free tier is the most generous available for production use (14,400 RPD, no waitlist), and `llama-3.3-70b-versatile` reliably produces structured JSON, which is the primary requirement for a pipeline where every agent must return a parseable schema.

### Why Celery, not Django async views?

The pipeline takes 10–30 seconds to complete. Holding an HTTP connection open for this duration is wasteful and hits proxy timeouts. Celery decouples submission (fast 201 response) from execution (background worker), allows retries, and separates the ASGI web process from LLM work. The frontend connects via WebSocket for live updates.

### Why Redis as both channel layer and Celery broker?

Railway provides Redis as a managed plugin. For this workload (task fan-out, no complex routing), Redis is fully sufficient as a Celery broker — eliminating the need for a separate RabbitMQ service and reducing the Railway service count.

### Why per-agent WebSocket broadcasts from within agent nodes?

Each agent node writes to the channel layer immediately after getting its LLM response. This gives the browser a card to render within 3–5 seconds of pipeline start. If all results were held until synthesis, the user would stare at a loading screen for 15–30 seconds.

### Why anonymous reviews without login?

Requiring login before showing any value creates a large drop-off. The anonymous flow (5 reviews/day, 200-line limit) lets a user experience the full product immediately. Google OAuth is positioned as an upgrade: review history and higher line limits.

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Pipeline | LangGraph 0.2+ (sequential chain) |
| LLM | Groq — llama-3.3-70b-versatile |
| Backend | Django 5.0 + Django REST Framework |
| WebSockets | Django Channels 4 + Redis channel layer |
| Task Queue | Celery 5 (Redis broker) |
| Database | PostgreSQL 16 |
| Cache / Broker | Redis 7 |
| GitHub Integration | PyGithub |
| Language Detection | pygments |
| Auth | django-allauth (Google OAuth) + simplejwt |
| Frontend | Next.js 14, TypeScript, TailwindCSS, shadcn/ui |
| Deployment | Railway |

## Features

- **Three input modes** — paste, file upload, GitHub URL
- **Live streaming** — agent verdicts appear as they complete via WebSocket
- **Conflict detection** — where agents disagree is surfaced explicitly; you see both stances
- **Severity ranking** — issues flagged by 2+ agents are marked Critical; single-agent findings listed separately
- **Shareable links** — every review gets a permanent public URL (`/r/{slug}`)
- **Review history** — Google OAuth unlocks a dashboard of past reviews
- **Rate limiting** — 5 reviews/day anonymous, 20/day authenticated
- **Language detection** — automatic via pygments; all mainstream languages supported

## Getting Started

### Prerequisites

- Docker and Docker Compose
- A [Groq API key](https://console.groq.com/keys) (free tier works)
- Optionally: Google OAuth credentials and a GitHub personal access token

### 1. Clone and configure

```bash
git clone https://github.com/JukaleManmath/panel-review.git
cd panel-review
cp .env .env.local   # or create a fresh .env from the table below
```

Set at minimum:

```env
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(50))">
DATABASE_URL=postgres://postgres:postgres@db:5432/panelreview
REDIS_URL=redis://redis:6379/0
REDIS_CELERY_BROKER=redis://redis:6379/1
REDIS_CELERY_BACKEND=redis://redis:6379/2
REDIS_CACHE_URL=redis://redis:6379/3
GROQ_API_KEY=<your-groq-api-key>
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

### 2. Start services

```bash
docker compose up --build
```

This starts:

- `db` — PostgreSQL 16 on port 5432
- `redis` — Redis 7 on port 6379
- `web` — Django + Daphne (ASGI) on port 8000
- `worker` — Celery worker
- `frontend` — Next.js dev server on port 3000

### 3. Run migrations

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

### 4. Open

Navigate to [http://localhost:3000](http://localhost:3000).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Django secret key |
| `DEBUG` | No | `True` for local dev |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis for channel layer |
| `REDIS_CELERY_BROKER` | Yes | Redis for Celery broker |
| `REDIS_CELERY_BACKEND` | Yes | Redis for Celery results |
| `REDIS_CACHE_URL` | Yes | Redis for Django cache |
| `GROQ_API_KEY` | Yes | Groq Console key |
| `GROQ_MODEL` | No | Default: `llama-3.3-70b-versatile` |
| `GITHUB_TOKEN` | No | Raises GitHub API rate limit from 60 to 5,000 req/hr |
| `GOOGLE_CLIENT_ID` | No | Required for Google OAuth login |
| `GOOGLE_CLIENT_SECRET` | No | Required for Google OAuth login |
| `ALLOWED_HOSTS` | No | Comma-separated hosts (production); defaults to `.railway.app` |
| `CORS_ALLOWED_ORIGINS` | No | Comma-separated origins for CORS (production) |
| `ANONYMOUS_DAILY_LIMIT` | No | Reviews/day for anonymous users. Default: 5 |
| `AUTHENTICATED_DAILY_LIMIT` | No | Reviews/day for authenticated users. Default: 20 |
| `ANONYMOUS_MAX_LINES` | No | Default: 200 lines |
| `AUTHENTICATED_MAX_LINES` | No | Default: 500 lines |
| `NEXT_PUBLIC_API_URL` | Yes | Backend URL for the frontend build |
| `NEXT_PUBLIC_WS_URL` | Yes | WebSocket URL for the frontend build |
| `NEXT_PUBLIC_REDIRECT_URI` | Yes | Google OAuth callback URL |

## API Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/reviews/` | Optional | Submit code for review |
| `GET` | `/api/reviews/{id}/` | Optional | Poll review status and results |
| `GET` | `/api/r/{slug}/` | None | Public share page data |
| `GET` | `/api/history/` | Required | Authenticated user's review history |
| `POST` | `/api/auth/token/` | None | Issue JWT (email + password) |
| `POST` | `/api/auth/social/google/` | None | Google OAuth code → JWT exchange |
| `WS` | `ws://.../ws/reviews/{id}/` | Optional | Live review stream |

### Submit a review

```bash
curl -X POST http://localhost:8000/api/reviews/ \
  -H "Content-Type: application/json" \
  -d '{
    "input_mode": "paste",
    "code": "def foo():\n    x = [i for i in range(1000000)]\n    return x"
  }'
# → {"review_id": "550e8400-e29b-41d4-a716-446655440000"}
```

### WebSocket event stream

```typescript
const ws = new WebSocket(`ws://localhost:8000/ws/reviews/${reviewId}/`);

ws.onmessage = (e) => {
  const event = JSON.parse(e.data);
  switch (event.event) {
    case 'pipeline_start':  // pipeline dispatched to worker
    case 'agent_done':      // event.agent, event.result
    case 'synthesis_done':  // event.verdict (final ranked output)
    case 'done':            // all complete
    case 'error':           // event.message
  }
};
```

**Agent verdict shape:**

```typescript
interface AgentVerdict {
  issues: Array<{
    title: string;
    description: string;
    severity: 'critical' | 'warning' | 'suggestion';
    line_hint: string;
  }>;
  summary: string;
  overall_severity: 'critical' | 'warning' | 'suggestion';
}
```

**Synthesis verdict shape:**

```typescript
interface SynthesisVerdict {
  critical:    Array<{ title: string; description: string; agents: string[] }>;
  warnings:    Array<{ title: string; description: string; agents: string[] }>;
  suggestions: Array<{ title: string; description: string; agents: string[] }>;
  conflicts:   Array<{ topic: string; positions: Record<string, string> }>;
  overall_score: number;  // 0–100
  summary: string;
}
```

## Project Structure

```
panel-review/
├── backend/
│   ├── config/
│   │   ├── celery.py         # Celery app definition
│   │   ├── asgi.py           # ASGI router (HTTP + WebSocket)
│   │   ├── urls.py           # Root URL conf
│   │   └── settings/
│   │       ├── base.py       # Shared settings
│   │       ├── local.py      # Dev overrides
│   │       └── production.py # Prod overrides (HTTPS, no DEBUG)
│   ├── apps/
│   │   ├── reviews/          # Review model, serializers, views, admin
│   │   ├── users/            # Custom User model, JWT + OAuth endpoints
│   │   ├── input_handler/    # Paste, file upload, GitHub fetch
│   │   └── export/           # PDF generation (WeasyPrint)
│   ├── pipeline/
│   │   ├── graph.py          # LangGraph sequential chain (5 agents → synthesis)
│   │   ├── state.py          # ReviewState TypedDict
│   │   └── agents/           # pragmatist, paranoid, minimalist, optimizer, mentor, synthesis
│   ├── ws/                   # Django Channels consumers, routing, JWT middleware
│   ├── railway.json          # Backend Railway config (healthcheck, builder)
│   ├── railway.worker.json   # Worker Railway config (Celery start command, no healthcheck)
│   └── worker/
│       └── tasks.py          # Celery task: pipeline dispatch + lifecycle management
├── frontend/
│   └── app/
│       ├── page.tsx           # Input page (3 tabs: paste / file / GitHub URL)
│       ├── review/[id]/       # Live streaming results via WebSocket
│       ├── r/[slug]/          # Public share page (static render)
│       ├── dashboard/         # Auth-gated review history
│       └── components/        # AgentCard, SynthesisPanel, ShareButton, etc.
└── docker-compose.yml         # Local dev: db, redis, web, worker, frontend
```

## Deployment (Railway)

The project runs as three Railway services sharing one repo:

| Service | Config file | Start command |
|---|---|---|
| Backend (web) | `backend/railway.json` | Dockerfile CMD — migrate + daphne |
| Worker | `backend/railway.worker.json` | `celery -A config.celery worker --loglevel=info --concurrency=2` |
| Frontend | Railway auto-detect (Next.js) | `next start` |

### Steps

1. Push to GitHub
2. Create a Railway project and connect the repo
3. Add Railway plugins: **PostgreSQL** and **Redis**
4. Create three services pointing at the same repo; set each service's **Railway Config File** to the appropriate `railway.json`
5. Set all required environment variables (see table above) in each service's Variables tab
6. Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` **before the first build** — Next.js bakes these into the bundle at build time
7. After first deploy, run migrations via Railway's one-off command:
   ```bash
   python manage.py migrate
   ```

## Known Limitations

- **GitHub API rate limit** — without `GITHUB_TOKEN`, GitHub input is limited to 60 requests/hour across all users
- **Code size** — anonymous users are limited to 200 lines; authenticated users to 500 lines; file uploads capped at 100KB
- **Language support** — all pygments-supported languages are detected, but agent prompts perform best on mainstream languages (Python, TypeScript, Go, Java, Rust, C/C++)
- **Groq rate limits** — the free tier allows 14,400 RPD and 12,000 TPM. The sequential pipeline and SDK retry logic handle occasional bursts, but very large files in rapid succession may add latency
## License

MIT
