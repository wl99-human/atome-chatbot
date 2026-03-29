# Atome Customer Service Bot Interview Project

This repo implements a Python-first customer service bot plus a manager-facing meta-agent builder.

## Stack
- Backend: FastAPI, SQLAlchemy, SQLite locally, optional Postgres in production
- Frontend: React, TypeScript, Vite, Tailwind CSS
- Model: Gemini `gemini-2.5-flash-lite`
- Retrieval: in-app crawler + document parser + lexical chunk retrieval

## Implemented Features
- Customer chat bot with grounded KB answers and citations
- Deterministic tool workflows for:
  - card application status
  - failed card transaction lookup
- Admin UI to:
  - edit the knowledge base URL
  - edit additional guidelines
  - publish a new revision
  - re-sync sources
  - review reported mistakes
  - trigger auto-fix
- Manager UI to:
  - upload docs
  - provide instructions
  - generate a new customer service agent
- Issue reporting and self-fix workflow:
  - report a bad answer from the chat UI
  - diagnose the issue type
  - create a candidate revision
  - replay the failing prompt
  - auto-publish the fix when replay passes
  - archive the issue with fix metadata

## Project Structure
- [backend](/c:/PrivateProject/atome-chatbot/backend)
- [frontend](/c:/PrivateProject/atome-chatbot/frontend)
- [README.md](/c:/PrivateProject/atome-chatbot/README.md)

## Local Setup
### Backend
1. `cd backend`
2. `python -m venv .venv`
3. Activate the venv
4. `pip install -r requirements.txt`
5. Copy `.env.example` to `.env`
6. Set `GEMINI_API_KEY` if you want real Gemini responses
7. `uvicorn app.main:app --reload`

### Frontend
1. `cd frontend`
2. `npm install`
3. Copy `.env.example` to `.env`
4. `npm run dev`

### Single-hosted Build
1. `cd frontend`
2. `npm run build`
3. `cd ../backend`
4. `uvicorn app.main:app --host 0.0.0.0 --port 8000`

When the frontend has been built into `frontend/dist`, FastAPI serves it directly.

## Docker
### Local image build
1. `docker build -t atome-chatbot .`
2. `docker run --rm -p 8000:8000 --env-file backend/.env atome-chatbot`

The container builds the Vite frontend, copies `frontend/dist` into the image, and serves the full app through FastAPI.

## Deploy on Render
This repo now includes a root-level `Dockerfile` and `render.yaml` Blueprint for a single Docker-based web service plus managed Postgres.

1. Push this repo to GitHub or GitLab.
2. In Render, create a new Blueprint and select the repo.
3. Render will detect `render.yaml` and propose:
   - web service: `atome-chatbot`
   - Postgres database: `atome-chatbot-db`
4. Provide `GEMINI_API_KEY` during the initial Blueprint setup.
5. Deploy.

Notes:
- `AUTO_SYNC_DEFAULT_AGENT` is set to `false` in `render.yaml` so deploys do not block on a live KB sync during startup.
- The app uses Render Postgres in production via `DATABASE_URL`.
- The frontend defaults to same-origin `/api` in production, so the single-service setup works behind one Render URL.

## Environment Variables
Backend example is in [backend/.env.example](/c:/PrivateProject/atome-chatbot/backend/.env.example).

Key values:
- `DATABASE_URL`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `DEFAULT_KB_URL`
- `AUTO_SYNC_DEFAULT_AGENT`

## Demo Flows
### Part 1
- Ask a KB question like `How can I check the status of my application?`
- Ask `Please check my application status` and then provide a reference like `APP123456`
- Ask `My card transaction failed` and then provide a transaction ID
- Click `Report mistake` on an assistant answer
- Switch to `Admin` and trigger `Auto-fix`

### Part 2
- Switch to `Manager`
- Upload one or more docs
- Provide agent instructions
- Generate a new agent
- Switch back to `Customer` and chat with the generated agent

## Validation
- Backend tests: `cd backend && pytest -q`
- Frontend build: `cd frontend && npm run build`

## AI Usage
This implementation was built with AI assistance using a GPT-5-based coding agent in Codex-style workflow support. AI was used for scaffolding, code generation, refactoring, and debugging. Final architecture, tradeoffs, and validation were still selected and checked during implementation.

## Notes
- If `GEMINI_API_KEY` is missing, the app still runs with deterministic fallback behavior for retrieval answers and mock tool flows.
- The default support agent seeds fallback Atome knowledge locally, then can re-sync from the live Atome help center URL.
- The lookup tools are mocked by design for interview purposes.
