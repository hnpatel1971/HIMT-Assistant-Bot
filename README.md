# HIMT Assistant Bot

AI chatbot for HIMT (Hindustan Institute of Maritime Training). It crawls the HIMT websites, retrieves
relevant passages, and answers visitor questions with Claude, grounded in that content. It also captures
leads and feedback, and has admin endpoints for re-ingestion, logs and stats.

Originally built in Emergent; now runs on the Anthropic Claude API.

## Stack

- **Backend:** FastAPI + MongoDB (motor), Claude via the `anthropic` Python SDK, APScheduler for daily re-ingestion, Resend for alert emails
- **Frontend:** React (CRA + craco), Tailwind, shadcn/ui; `public/embed.js` embeds the widget on any site

## Run locally

```bash
# Backend
cd backend
cp .env.example .env        # set MONGO_URL, DB_NAME, ANTHROPIC_API_KEY, ADMIN_TOKEN
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# Frontend
cd frontend
cp .env.example .env        # REACT_APP_BACKEND_URL=http://localhost:8001
yarn install
yarn start
```

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key. Without it the bot answers with the rule-based fallback. |
| `LLM_MODEL` | Model ID, default `claude-opus-5-5`. |
| `LLM_EFFORT` | `low` / `medium` / `high`, default `low` (short grounded answers). |
| `ADMIN_TOKEN` | Required for `/api/admin/*` (sent as `X-Admin-Token`). No default. |

See `backend/.env.example` for the full list.

## Embedding

```html
<script src="https://<your-chatbot-host>/embed.js" async></script>
```
