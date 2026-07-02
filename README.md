# The Council of Me

An interactive self-reflection application. A user's concern is elicited through
dialogue, explored by a council of inner "voices" in a structured debate, and
finally distilled into a synthesized landscape and inner portrait.

## Architecture

- **Backend** — FastAPI service (`backend/`) orchestrating the elicitation →
  debate → synthesis → portrait pipeline over an OpenAI-compatible LLM.
- **Frontend** — React + TypeScript + Vite single-page app (`frontend/`).

```
.
├── backend/
│   ├── app/          # FastAPI application (api, services, models, repositories)
│   ├── eval/harness/ # runtime tracing/observability helpers imported by app
│   ├── migrations/   # SQL schema (optional Postgres tier)
│   └── requirements.txt
└── frontend/
    ├── src/          # React app source
    ├── public/
    └── package.json
```

## Backend

Requires Python 3.11+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# configure the LLM (copy the template and fill in your keys)
cp ../.env.example ../.env

uvicorn app.main:app --reload
```

The API serves on `http://127.0.0.1:8000`. Without LLM credentials the service
falls back to mock responses, so it boots without external configuration.

## Frontend

Requires Node.js 18+.

```bash
cd frontend
npm install
npm run dev      # dev server (proxies /api to http://127.0.0.1:8000)
npm run build    # production build
```

## Configuration

Copy `.env.example` to `.env` and set the LLM provider values. The project uses
an OpenAI-compatible interface; see the comments in `.env.example` for the
supported providers.
