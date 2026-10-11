# Agentic RCA — Evidence-Grounded Root-Cause Analysis

A five-agent RCA workflow that connects production telemetry to Git history, generates a focused regression reproducer, proposes a candidate patch, and performs pre-isolation validation.

## Agent pipeline

```text
Telemetry Parser
      ↓
Git Forensic
      ↓
Crash Reproduction
      ↓
Patch Synthesizer
      ↓
Pre-isolation Verification
```

### Model routing

- **Gemini**: telemetry extraction and evidence-grounded repository reasoning.
- **Hugging Face coding model**: pytest reproduction and candidate patch generation.
- Providers automatically fall back to the other configured provider when possible.

True fine-tuning is not claimed by this repository. A real task-specific fine-tune requires a curated incident/patch/test dataset and training/evaluation procedure. The current implementation uses task-specific prompts, strict output contracts, source/evidence grounding, AST validation, and a model repair pass.

## Important verification boundary

Sandbox execution is intentionally **not implemented yet**.

The current verification stage:

- validates generated Python syntax;
- checks for dangerous generated constructs;
- confirms the original incident can be reproduced when the reproduction agent succeeds;
- does **not** apply the candidate patch to the uploaded repository;
- does **not** claim that the patch fixes the production failure.

Runtime patch verification can be added later using an isolated sandbox/container/worktree.

## Backend

```bash
cd backend-project
python -m venv .venv
# Windows
.venv\\Scripts\\activate
pip install -r requirements.txt
copy .env.example .env
python main.py
```

Backend API: `http://127.0.0.1:8000`
Swagger: `http://127.0.0.1:8000/docs`
Health: `http://127.0.0.1:8000/api/health`

Configure at least one model provider in `.env`. For the intended routing, configure both `GOOGLE_API_KEY` and `HF_TOKEN`.

## Next.js frontend

```bash
cd frontend
npm install
copy .env.local.example .env.local
npm run dev
```

Frontend: `http://localhost:3000`

The frontend uses the REST API for incident creation/upload/start/report and WebSocket `/ws/incidents/{id}/progress` for live pipeline logs.

## API flow

1. `POST /api/incidents` — create incident.
2. `POST /api/incidents/{id}/upload` — upload repository ZIP.
3. `WS /ws/incidents/{id}/progress` — connect to the progress stream.
4. `POST /api/incidents/{id}/run` — start LangGraph pipeline after the WebSocket connects.
5. `GET /api/incidents/{id}/status` — poll status if WebSocket is unavailable.
6. `GET /api/incidents/{id}/report` — retrieve final report.
7. `GET /api/incidents/{id}/patch` — retrieve candidate patch.
8. `POST /api/incidents/{id}/approve` — record human approval for later manual/isolated verification.
9. `POST /api/demo` — prepare a bundled demo incident; connect to its progress WebSocket and call `/api/incidents/{id}/run`.

## Clean-up performed

Removed prototype/utility artifacts that were not part of the production RCA workflow, including the old static HTML frontend, patch helper scripts, unrelated Strategy-pattern Java demo files, generated caches, local database state, and generated reproduction test artifacts.
