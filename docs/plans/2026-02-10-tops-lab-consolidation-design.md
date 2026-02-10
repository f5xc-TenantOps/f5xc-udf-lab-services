# tops-lab Consolidation & Enhancement Design

> **Date:** 2026-02-10
> **Scope:** Consolidate `tops-lab` + `tops-info` into a single container, add Vue frontend, integrate CE registration, validate E2E outputs flow.
> **Branch:** `hive`
> **Repo:** `f5xc-udf-lab-services`

---

## Context

The UDF lab service currently runs as two separate containers sharing a `/state` volume:
- `tops-lab` — background daemon that fetches metadata, sends SQS heartbeats, polls S3 for backend state
- `tops-info` — Flask API server that reads state files and serves a status page + outputs API

The [E2E spec](../../udf-lab-e2e-spec.md) calls for consolidation into a single container, config simplification, CE registration support, and a proper frontend. This design covers Phases 1, 3, and 4 of the spec (Phase 2 — lab validator — is deferred as it's lab-specific).

---

## Phase 1: Consolidate & Simplify

### 1.1 Single Container Architecture

Merge `tops-lab` (daemon) and `tops-info` (Flask) into one process. Flask runs as the main thread. Background work runs in daemon threads.

**New directory structure:**
```
app/
  app.py                    # Flask + background threads
  ce_client.py              # CE registration logic (Phase 3)
  requirements.txt          # Merged dependencies
  Dockerfile
  tops_lab_install.sh        # Single install script
  test_app.py               # Consolidated tests
  frontend/                 # Vue 3 SPA (Phase 2)
    package.json
    vite.config.js
    index.html
    src/
      main.js
      App.vue
      assets/
        f5-logo.svg
```

**Removed:**
- `lab/` directory (merged into `app/`)
- `info/` directory (merged into `app/`)
- `info/tops_info_install.sh` (single install script now)
- `.github/workflows/tops-info.yml` (single workflow now)

### 1.2 Startup Sequence

1. Check for existing state in `/state/deployment_state.json`. If present with valid `dep_id` and petname, this is a restart — skip to step 6.
2. Fetch UDF metadata from `http://metadata.udf` (dep_id, lab_id, email, AWS creds). Retry up to 10 times, 6s delay.
3. Fetch global config from `s3://{CONFIG_BUCKET}/config.json` using AWS creds. Returns `sqsURL` and `stateBucket`.
4. Generate petname via `petname` library.
5. Save deployment state to `/state/deployment_state.json`.
6. Start SQS heartbeat daemon thread (every 90s).
7. Start S3 state polling daemon thread (every 10s).
8. Start Flask on port 5123 (main thread).

### 1.3 State Sharing

The S3 polling thread writes backend state to a module-level variable (`_backend_state: dict | None`). Flask routes read from it. Python's GIL makes dict reference swaps atomic — the poller replaces the reference, Flask reads the current reference. No explicit locking needed.

The `/state/deployment_state.json` file persists for restart recovery only. The old `/state/backend_state.json` inter-container file is eliminated since both concerns are in-process.

### 1.4 Config Simplification

**Current:** `tops-lab` requires a `LAB_INFO_BUCKET` env var and reads `{labID}.yaml` per-lab from S3. Every lab's YAML contains the same two values (SQS URL, state bucket) — pure redundancy.

**New:** A single `config.json` in a known bucket. The bucket name is baked into the image:

```python
CONFIG_BUCKET = os.getenv("CONFIG_BUCKET", "tops-registry-bucket")
```

**`config.json` format:**
```json
{
  "sqsURL": "https://sqs.us-east-1.amazonaws.com/123456789/tops-udf-queue",
  "stateBucket": "tops-deployment-state"
}
```

**What this removes:**
- `LAB_INFO_BUCKET` env var (not needed in install script or systemd unit)
- `get_lab_info()` function and per-lab S3 lookup
- `pyyaml` dependency
- `labinfo` field from `deployment_state.json` — replaced by `config`

**New `deployment_state.json` format:**
```json
{
  "dep_id": "deployment-12345",
  "lab_id": "e37500bc",
  "email": "user@example.com",
  "petname": "fuzzy-dragon",
  "config": {
    "sqsURL": "https://sqs...",
    "stateBucket": "tops-deployment-state"
  }
}
```

**Infra dependency:** The `config.json` object must exist in the S3 bucket. This is a change in `f5xc-tops-infra` (outside this repo).

### 1.5 API Endpoints

All served on port 5123 by Flask. Unchanged from the spec.

| Endpoint | Method | Response | Purpose |
|----------|--------|----------|---------|
| `/` | GET | SPA `index.html` | Vue frontend |
| `/health` | GET | `{"status": "running"}` | Health check |
| `/status/json` | GET | JSON deployment state | Programmatic status |
| `/metadata` | GET | JSON | Deployment metadata |
| `/petname` | GET | `{"petname": "..."}` | Deployment name |
| `/outputs` | GET | `{key: value, ...}` | All backend outputs |
| `/outputs/<key>` | GET | `{key: value}` or 404 | Specific output |
| `/ce/status` | GET | CE registration state | Phase 3 |

### 1.6 Install Script

Single `tops_lab_install.sh` replaces both existing scripts:

- Image: `ghcr.io/f5xc-tenantops/f5xc-udf-lab-services/tops-lab:latest`
- Expose port 5123
- No `LAB_INFO_BUCKET` env var
- `docker run --pull=always` (spec requirement — ensures fresh image on every restart)
- `/state` volume mount for restart persistence

### 1.7 CI/CD

Single workflow `.github/workflows/tops-lab.yml` replaces both existing workflows:

```yaml
on:
  push:
    branches: [main, dev, hive]
    paths: ["app/**"]
  workflow_dispatch:
```

Steps:
1. Checkout
2. Setup Python 3.11
3. Run pytest
4. Setup Node 20
5. `npm ci && npm run build` (frontend)
6. Login to GHCR
7. Build Docker image from `app/`
8. Push to GHCR

`tops-info.yml` is deleted.

---

## Phase 2: Vue SPA Frontend

### 2.1 Design Language

Ported from the existing [`udf-ce-registration`](https://github.com/kreynoldsf5/udf-ce-registration) frontend. This establishes the visual style for all UDF host UIs.

| Element | Style |
|---------|-------|
| Background | `#ffffff` (light) |
| Font | `system-ui, -apple-system, sans-serif` |
| Cards | `#f5f5f5` background, `8px` border-radius |
| Badge green | `#dcfce7` bg, `#166534` text |
| Badge amber | `#fef3c7` bg, `#92400e` text |
| Badge gray | `#e5e7eb` bg, `#374151` text |
| Badge red | `#fee2e2` bg, `#991b1b` text |
| Primary button | `#6366f1` (indigo) |
| Monospace | `ui-monospace, monospace` |

### 2.2 Page Layout

Single page, no routing. Content:

1. **Header** — F5 logo, "Lab Deployment Status", petname (large), deployer email
2. **Deployment Status card** — status badge, step-by-step progress with icons, timestamps
3. **Outputs card** — key/value table (site_token, lb_hostname, etc.), shown when available
4. **CE Status card** — shown only when CE registration is active (state, hostname, OS version, public IP)
5. **Error section** — collapsible error log

### 2.3 Client-Side Polling

```javascript
// Poll /status/json every 5 seconds
setInterval(async () => {
  const res = await fetch('/status/json')
  state.value = await res.json()
}, 5000)
```

No full-page refresh. Smooth state transitions.

### 2.4 Build & Serve

- **Dev:** `cd frontend && npm run dev` (Vite dev server with proxy to Flask)
- **Build:** `npm run build` → outputs to `frontend/dist/`
- **Docker:** `COPY frontend/dist/ static/` in Dockerfile
- **Flask:** Serves `static/` directory. `GET /` → `static/index.html`

### 2.5 Tech Stack

- Vue 3 (composition API, `<script setup>`)
- Vite 5
- No additional UI libraries — plain CSS matching CE registration styles

---

## Phase 3: CE Registration (Inside tops-lab)

### 3.1 Rationale

Instead of a separate `tops-ce-agent` container, CE registration lives inside `tops-lab` as reactive behavior. The S3 polling thread already observes the deployment state — when `site_token` appears in `outputs`, it triggers registration automatically. No extra container, no inter-container polling.

If the backend never writes a `site_token` (non-CE lab), the registration code never fires.

### 3.2 Logic (adapted from udf-ce-registration)

**Reused from [`udf-ce-registration`](https://github.com/kreynoldsf5/udf-ce-registration):**

| Concept | Source File | Adaptation |
|---------|------------|------------|
| CE discovery | `metadata_client.py` | `metadata.udf/userTags/name/role/value/CE` → `mgmtIp` |
| Registration POST | `ce_client.py` | `https://{ce_ip}:65500/api/ves.io.vpm/introspect/write/ves.io.vpm.config/update` with `{"token": "..."}` |
| Status polling | `ce_client.py` | `https://{ce_ip}:65500/api/ves.io.vpm/introspect/read/ves.io.vpm.health` |
| Basic auth | `ce_client.py` | `admin/Volterra123` defaults |

**Not needed:**
- `jwt_parser.py` — token comes as a raw string from S3 `outputs.site_token`
- FastAPI server + Vue frontend — no manual input, fully automated
- `httpx` — use `requests` (already a dependency) with `verify=False`

### 3.3 Flow

1. S3 poller sees `site_token` in `outputs` → sets flag `ce_registration_started`
2. Spawns CE registration daemon thread (once per deployment):
   - Discovers CE IP from `metadata.udf` (same metadata service `tops-lab` already uses)
   - POSTs `{"token": site_token}` to CE config endpoint
   - Polls CE health endpoint every 15s until `state == "ONLINE"` or timeout (10 min)
   - Stores CE status in module-level variable `_ce_status`
3. `GET /ce/status` returns current CE registration state
4. Vue frontend displays CE status card when data is available

### 3.4 Guard Rails

- Only fires once per deployment (flag prevents re-triggering)
- On restart recovery, check if CE is already registered before re-POSTing
- Timeout after 10 minutes
- All errors logged, exposed via `/ce/status` with error detail
- `requests` with `verify=False` for self-signed CE certs in lab environment

### 3.5 New Dependency

None. `requests` already handles HTTPS with `verify=False`.

---

## Phase 4: E2E Validation

### 4.1 Contract Test (CI)

A pytest test that validates the S3 state schema contract between repos:

- Load a sample `{depID}.json` fixture matching the spec schema (status, steps, outputs, errors)
- Verify `tops-lab` parses it correctly
- Verify all fields are served via the API endpoints
- Catches schema drift between what Lambdas write and what `tops-lab` expects

### 4.2 Manual E2E Checklist

Documented runbook for live UDF validation:

1. Deploy a UDF lab
2. Verify `/` loads Vue SPA
3. Verify status page shows progress in real-time (PENDING → IN_PROGRESS → COMPLETED)
4. Verify `/outputs/site_token` returns JWT once provisioning completes
5. Verify CE registers automatically (for CE-enabled labs) and `/ce/status` shows ONLINE
6. Verify cleanup happens when deployment ends (heartbeats stop → TTL expires → resources removed)

---

## Implementation Order

| Step | Phase | Description |
|------|-------|-------------|
| 1 | 1 | Create `app/` directory, consolidate `app.py` (Flask + threads) |
| 2 | 1 | Implement config.json lookup, remove per-lab YAML |
| 3 | 1 | Consolidate and expand tests |
| 4 | 1 | Single Dockerfile, install script |
| 5 | 1 | Unified CI workflow |
| 6 | 2 | Scaffold Vue frontend (Vite + Vue 3) |
| 7 | 2 | Build status page with CE registration design language |
| 8 | 2 | Wire frontend build into Dockerfile and CI |
| 9 | 3 | Add `ce_client.py` (CE discovery, registration, status polling) |
| 10 | 3 | Integrate CE registration into S3 polling thread |
| 11 | 3 | Add CE status card to Vue frontend |
| 12 | 4 | Contract test with S3 state fixture |
| 13 | 4 | Document manual E2E checklist |
| 14 | — | Clean up old `lab/`, `info/` directories and `tops-info.yml` workflow |

---

## Dependencies on Other Repos

| Dependency | Repo | Change |
|-----------|------|--------|
| `config.json` in S3 bucket | `f5xc-tops-infra` | Add `config.json` S3 object to bucket config |

---

## Files Removed

- `lab/app.py`, `lab/Dockerfile`, `lab/requirements.txt`, `lab/test_app.py`, `lab/tops_lab_install.sh`
- `info/app.py`, `info/Dockerfile`, `info/requirements.txt`, `info/test_app.py`, `info/tops_info_install.sh`, `info/templates/`
- `.github/workflows/tops-info.yml`
