# Atome Chatbot Demo Guide

This guide is the authoritative live-demo script for this project. It is optimized for an 8-12 minute demo walkthrough and is safe to run even when `GEMINI_API_KEY` is empty.

## Demo Goal
- Show the three main surfaces: `Customer` at `/customer`, `Admin` at `/admin`, and `Manager` at `/manager`.
- Demonstrate grounded knowledge-base answers with citations.
- Demonstrate deterministic lookup workflows for application status and failed transactions.
- Demonstrate issue reporting plus the auto-fix loop.
- Demonstrate manager-driven creation of a new agent from uploaded documents.

## Pre-Demo Checklist
- Backend dependencies are installed and the backend can start on `http://localhost:8000`.
- Frontend dependencies are installed and the frontend can start on `http://localhost:5173`.
- `backend/.env` exists. `GEMINI_API_KEY` is optional for this script.
- `frontend/.env` exists or the default API base is still `http://localhost:8000/api`.
- The selected agent in the left sidebar is `Atome Card Support` before you start the customer and admin flows.
- If you want a clean demo state, point `DATABASE_URL` to a fresh local SQLite file before launching the backend.
- Prepare a tiny text file for the manager demo:

```text
it_policy.txt

To reset your password, contact the IT helpdesk at helpdesk@example.com.
Access requests are approved by your manager.
```

## Startup
### Backend
```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend
```powershell
cd frontend
npm install
npm run dev
```

### First Screen Check
1. Open `http://localhost:5173`.
2. Confirm the top nav shows `Customer`, `Admin`, and `Manager`.
3. Confirm the left sidebar shows `Atome Card Support`.
4. Note the sync badge:
   - `fallback` is fine for this script.
   - `live_api` is also fine; the same core demo still works.

## Recommended Demo Order
1. Customer grounded Q&A and issue reporting
2. Customer lookup workflows
3. Admin overview and auto-fix
4. Manager-built agent

## Live Demo Script
### 1. Customer Q&A With Citations
1. Go to `Customer`.
2. Click `New chat` if the page already has conversation history you do not want.
3. Send this prompt:

```text
Why can an Atome Card transaction fail?
```

4. What to show:
   - The assistant gives a grounded answer instead of a tool lookup.
   - The answer includes a `Sources` expander.
   - The citations include `Open source` links.
   - This works in fallback mode because the project seeds Atome card knowledge locally.

### 2. Report a Mistake From the Customer View
1. On the same assistant answer, click `Report mistake`.
2. In `Short reason`, enter:

```text
Please mention that unsuccessful pending charges are usually reversed or refunded within 14 days.
```

3. Leave `Optional details` empty or add extra context if you want.
4. Click `Submit report`.
5. What to show:
   - The customer can report a bad answer directly from the chat UI.
   - The modal confirms the issue is now in the admin review queue.
   - You can close the modal now and continue the walkthrough.

### 3. Application Status Lookup
1. Stay on `Customer`.
2. Click `New chat` for a clean flow.
3. Send:

```text
Please check my application status
```

4. When the assistant asks for the reference, reply with:

```text
APP222222
```

5. What to show:
   - The assistant asks for the missing identifier first.
   - The follow-up is conversational, but the result is deterministic.
   - `APP222222` currently resolves to `APPROVED`.

### 4. Failed Transaction Lookup
1. Click `New chat` again.
2. Send:

```text
My card transaction failed
```

3. When the assistant asks for the transaction ID, reply with:

```text
TRX111111
```

4. What to show:
   - The assistant asks for the missing transaction ID before responding.
   - The tool-backed result is deterministic.
   - `TRX111111` currently resolves to `DECLINED`.

### 5. Admin Overview and Auto-Fix
1. Go to `Admin`.
2. Start on `Overview`.
3. Point out the revision controls and current state:
   - `Publish revision`
   - `Sync sources`
   - current revision number
   - sync mode
   - indexed docs and chunks
   - current KB URL
4. Important framing:
   - Treat `Publish revision` and `Sync sources` as revision and source-management controls.
   - Do not claim that changing `Additional guidelines` immediately changes chat behavior in the current implementation.
5. Optional 20-second add-on:
   - Edit `Description` slightly and click `Publish revision`.
   - Show the revision number increment.
   - Keep the explanation focused on revision lifecycle, not runtime policy changes.
6. Switch to the `Issues` tab.
7. Confirm the reported issue is in the queue for `Atome Card Support`.
8. Click `View details`.
9. Show:
   - customer prompt
   - assistant answer
   - customer note
   - replay and fix state
10. Click `Auto-fix`.
11. If the item disappears from `Open queue`, switch the filter to `Archived` or `All`.
12. What to show:
   - the issue moved to archived after a successful auto-fix
   - a fix attempt was recorded
   - replay passed
   - auto-published is recorded in the fix state

### 6. Manager-Built Agent
1. Go to `Manager`.
2. Upload the prepared `it_policy.txt`.
3. Use these inputs:

```text
Agent name: Internal IT Support
Description: IT support demo agent
Instructions: Answer only from uploaded knowledge.
```

4. Click `Generate agent`.
5. What to show on the manager page:
   - the generated blueprint appears on the right
   - the UI shows instructions, knowledge summary, and enabled tools
   - in fallback-safe mode, the enabled tool list may simply be `support_handoff`
6. Click `Open in customer view`.
7. In the generated agent chat, send:

```text
How do I reset my password?
```

8. What to show:
   - the selected agent has changed from `Atome Card Support` to `Internal IT Support`
   - the assistant answers from the uploaded file
   - the answer includes a citation grounded in the uploaded text

## Optional Live-Mode Addendum
- If `GEMINI_API_KEY` is configured, responses and generated blueprints may sound more natural, but the same script still applies.
- If `Sync sources` succeeds against the live Atome Zendesk content, the sync badge will show `live_api`.
- In `live_api` mode, you can mention that the bot is using a broader synced help-center knowledge base instead of only the seeded fallback set.
- A good optional live-sync FAQ prompt is:

```text
How do I change the mobile number for my account?
```

## Troubleshooting
- If the frontend loads but no app data appears, confirm the backend is running on `http://localhost:8000` and the frontend is pointing to `http://localhost:8000/api`.
- If the customer answer has no citations, make sure the selected agent is `Atome Card Support` and use the exact fallback-safe KB prompt from this guide.
- If the issue is not visible in `Admin`, confirm you reported it against the currently selected agent and check the `Open queue`, `Archived`, and `All` filters.
- If an issue disappears right after `Auto-fix`, that is usually expected; successful fixes move the issue to archived.
- If the manager-generated agent does not answer from the uploaded text, confirm you clicked `Open in customer view` and that the active agent changed in the left sidebar.
- If live sync fails, continue the demo in `fallback` mode. That is a valid path for this project.

## Validation Commands
```powershell
cd backend
pytest -q
```

```powershell
cd frontend
npm run build
```
