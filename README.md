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

- **Gemini 3.5 Flash-Lite**: lower-cost telemetry extraction and repository reasoning.
- **Qwen2.5 7B / Qwen2.5-Coder 7B**: lower-compute analysis, pytest reproduction,
  and candidate patch generation through Hugging Face.
- Providers automatically fall back to the other configured provider when possible.

These smaller models preserve the same task interfaces, but may produce less reliable
results than the previous 32B coding model. Smaller checkpoints can reduce per-request
cost; they do not provide free inference or restore exhausted provider quotas. Hard
quota-exhaustion responses are not retried, avoiding redundant requests.

True fine-tuning is not claimed by this repository. A real task-specific fine-tune requires a curated incident/patch/test dataset and training/evaluation procedure. The current implementation uses task-specific prompts, strict output contracts, source/evidence grounding, AST validation, and a model repair pass.

## Important verification boundary

Isolated Docker verification is implemented but requires Docker and the trusted
`agentic-rca-sandbox:py311` image. When available, it checks that the generated
reproduction fails before the candidate patch, passes after it, and the repository's
full pytest suite passes. The patch is applied only to a disposable repository copy;
if Docker or the image is unavailable, the candidate remains unverified.

## Run with Docker Compose

Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin is required.
From the project root, create the local environment file and add provider credentials:

```powershell
Copy-Item .env.example .env
# Edit .env and set GOOGLE_API_KEY and/or HF_TOKEN
docker compose up --build
```

Open `http://localhost:3000`; the API is at `http://localhost:8000` and Swagger at
`http://localhost:8000/docs`. Stop the stack with `Ctrl+C`, then run
`docker compose down` to remove its containers. Named database and sandbox-workspace
volumes are retained; `docker compose down -v` also deletes that persisted data.

To build the isolated verification image, run this in another terminal:

```powershell
docker compose --profile tools build sandbox
```

The standard Compose setup runs the app without mounting the host Docker socket.
Candidate patch verification therefore stays unavailable until you explicitly opt in:

```powershell
# Stop the regular stack with Ctrl+C, then restart with sandbox access:
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up --build --force-recreate
```

The sandbox override gives the backend access to the host Docker daemon, which is
effectively host-level administrative access. Use it only for a trusted, local demo;
the API is bound to localhost, but do not expose it to an untrusted network. The
verification container itself runs with networking disabled, a read-only root
filesystem, a non-root user, and resource limits. Generated patches are never
applied to the original repository. The sandbox workspace volume must be mounted
with Docker's `volume-subpath` support so each verification container sees only its
own temporary repository copy.

## Run the backend locally

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
pip install -r requirements.txt
# Windows PowerShell: Copy-Item .env.example .env
python main.py
```

Backend API: `http://127.0.0.1:8000`
Swagger: `http://127.0.0.1:8000/docs`
Health: `http://127.0.0.1:8000/api/health`

Configure at least one model provider in `.env`. For the intended routing, configure both `GOOGLE_API_KEY` and `HF_TOKEN`.
The recommended lower-cost defaults are `gemini-3.5-flash-lite`,
`Qwen/Qwen2.5-Coder-7B-Instruct`, and `Qwen/Qwen2.5-7B-Instruct`.

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

## Isolated patch verification and GitHub PR publishing

Runtime verification is fail-closed: the backend never falls back to executing candidate code on the host. A patch is marked verified only when the generated reproduction test fails by assertion before the patch, passes after the patch, and the complete pytest regression suite passes in Docker.

### Build the sandbox image manually

Alternatively, build the image directly from the project root:

```bash
docker build -f backend/services/sandbox.Dockerfile -t agentic-rca-sandbox:py311 .
```

The verification container is then run with networking disabled, a read-only container root filesystem, non-root user, dropped capabilities, no-new-privileges, and CPU/memory/PID/time limits. The generated patch is applied only to a temporary copy of the uploaded repository. If Docker or the image is unavailable, verification fails explicitly; the application does not run uploaded code on the host.

The sandbox image includes this application's declared `requirements.txt`. Uploaded repositories that require additional runtime packages need a separately reviewed, trusted sandbox image containing those dependencies; dependencies from an untrusted uploaded repository are not installed automatically.

### Optional GitHub publishing

Set these values in `.env` only if you want the human approval action to publish a pull request:

```dotenv
GITHUB_TOKEN=
GITHUB_REPO=owner/repository
GITHUB_BASE_BRANCH=main
GITHUB_HTTP_TIMEOUT=15
RCA_SANDBOX_IMAGE=agentic-rca-sandbox:py311
```

Use a fine-grained token scoped only to the target repository with the minimum contents and pull-request permissions required. Do not commit `.env` or the token. GitHub publishing is blocked unless the candidate is sandbox-verified and the user explicitly invokes the approval action. Publishing clones the configured target repository into a temporary directory, applies the validated unified diff, commits only changed files, pushes a branch, and opens a PR. It never auto-merges. If the diff does not apply to the configured target branch, publishing fails instead of guessing or force-pushing.

### Verification tests

Run the local automated tests with:

```bash
python -m compileall -q backend main.py tests
python -m pytest -q
```

These tests validate application helpers and fail-closed behavior. A full runtime verification requires Docker and the built sandbox image. A frontend build requires installing `frontend/package-lock.json` dependencies first:

```bash
cd frontend
npm ci
npm run build
```
