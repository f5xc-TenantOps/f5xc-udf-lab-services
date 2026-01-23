# UDF State Polling & Status Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable UDF lab service to poll S3 for deployment state and display real-time progress to users via a status web page.

**Architecture:**
- `tops-lab` service polls S3 bucket (`tops-deployment-state`) for `{dep_id}.json` files
- State is written to `/state/backend_state.json` for the info service to read
- `tops-info` service serves a status page showing deployment progress
- Page auto-refreshes to show real-time updates

**Tech Stack:** Python 3.11, Flask 3.0, boto3, Jinja2 templates

---

## Task 1: Add S3 State Poller to tops-lab Service

**Files:**
- Modify: `lab/app.py`

**Step 1: Add S3 polling function**

Add after the existing imports and before the main loop:

```python
# Constants for state polling
STATE_BUCKET = os.getenv("STATE_BUCKET", "tops-deployment-state")
STATE_POLL_INTERVAL = 10  # seconds
BACKEND_STATE_FILE = "/state/backend_state.json"


def poll_backend_state(dep_id: str, s3_client) -> dict | None:
    """Poll S3 for deployment state file.

    Args:
        dep_id: Deployment ID to poll for
        s3_client: Initialized boto3 S3 client

    Returns:
        Parsed state dict or None if not found
    """
    try:
        response = s3_client.get_object(
            Bucket=STATE_BUCKET,
            Key=f"{dep_id}.json"
        )
        state = json.loads(response["Body"].read().decode("utf-8"))
        return state
    except s3_client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"[WARN] Failed to poll backend state: {e}")
        return None


def save_backend_state(state: dict) -> None:
    """Save backend state to local file for info service.

    Args:
        state: Backend state dict from S3
    """
    try:
        with open(BACKEND_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Failed to save backend state: {e}")
```

**Step 2: Integrate polling into main loop**

Modify the main loop to poll state alongside SQS heartbeat:

```python
def main():
    # ... existing setup code ...

    last_state_poll = 0

    while True:
        current_time = time.time()

        # Poll backend state every STATE_POLL_INTERVAL seconds
        if current_time - last_state_poll >= STATE_POLL_INTERVAL:
            dep_id = state["metadata"]["depID"]
            backend_state = poll_backend_state(dep_id, s3_client)
            if backend_state:
                save_backend_state(backend_state)
                print(f"[INFO] Backend state updated: {backend_state.get('status', 'unknown')}")
            last_state_poll = current_time

        # ... existing SQS heartbeat code ...

        time.sleep(min(STATE_POLL_INTERVAL, SQS_INTERVAL))
```

**Step 3: Test locally**

Run: `python lab/app.py` (with mock metadata service or in UDF environment)
Expected: Logs show state polling attempts

**Step 4: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add lab/app.py
git commit -m "$(cat <<'EOF'
feat(lab): add S3 state polling for backend status

Poll tops-deployment-state bucket every 10 seconds for {dep_id}.json
Save backend state to /state/backend_state.json for info service
EOF
)"
```

---

## Task 2: Create Status Page Template

**Files:**
- Create: `info/templates/status.html`

**Step 1: Create templates directory**

```bash
mkdir -p /home/coder/f5xc-udf-lab-services/info/templates
```

**Step 2: Create status.html template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>Lab Deployment Status</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
            padding: 2rem;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 2rem;
        }
        .header h1 {
            font-size: 1.8rem;
            color: #fff;
            margin-bottom: 0.5rem;
        }
        .header .petname {
            font-size: 2.5rem;
            font-weight: bold;
            color: #4fc3f7;
        }
        .header .email {
            color: #9e9e9e;
            font-size: 0.9rem;
        }
        .status-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .status-badge {
            display: inline-block;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9rem;
            text-transform: uppercase;
        }
        .status-pending { background: #455a64; color: #fff; }
        .status-in_progress { background: #1976d2; color: #fff; }
        .status-completed { background: #388e3c; color: #fff; }
        .status-failed { background: #d32f2f; color: #fff; }

        .steps-list {
            margin-top: 1.5rem;
        }
        .step {
            display: flex;
            align-items: center;
            padding: 1rem;
            margin-bottom: 0.5rem;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 8px;
            border-left: 4px solid #455a64;
        }
        .step.success { border-left-color: #4caf50; }
        .step.in_progress { border-left-color: #2196f3; animation: pulse 2s infinite; }
        .step.failed { border-left-color: #f44336; }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }

        .step-icon {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 1rem;
            font-size: 1rem;
        }
        .step-icon.pending { background: #455a64; }
        .step-icon.success { background: #4caf50; }
        .step-icon.in_progress { background: #2196f3; }
        .step-icon.failed { background: #f44336; }

        .step-info { flex: 1; }
        .step-name { font-weight: 600; color: #fff; }
        .step-detail { font-size: 0.85rem; color: #9e9e9e; margin-top: 0.25rem; }
        .step-error { color: #ef5350; font-size: 0.85rem; margin-top: 0.25rem; }

        .outputs {
            margin-top: 1.5rem;
        }
        .output-item {
            display: flex;
            justify-content: space-between;
            padding: 0.75rem;
            background: rgba(76, 175, 80, 0.1);
            border-radius: 6px;
            margin-bottom: 0.5rem;
        }
        .output-key { color: #81c784; }
        .output-value {
            color: #fff;
            font-family: monospace;
            word-break: break-all;
        }

        .waiting {
            text-align: center;
            padding: 3rem;
        }
        .spinner {
            width: 50px;
            height: 50px;
            border: 4px solid rgba(255, 255, 255, 0.1);
            border-top-color: #4fc3f7;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 1rem;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .refresh-note {
            text-align: center;
            color: #757575;
            font-size: 0.8rem;
            margin-top: 2rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Lab Deployment Status</h1>
            {% if state %}
            <div class="petname">{{ state.petname or 'Initializing...' }}</div>
            <div class="email">{{ state.email }}</div>
            {% else %}
            <div class="petname">{{ petname or 'Unknown' }}</div>
            <div class="email">{{ email or '' }}</div>
            {% endif %}
        </div>

        {% if state %}
        <div class="status-card">
            <span class="status-badge status-{{ state.status | lower | replace(' ', '_') }}">
                {{ state.status }}
            </span>
            {% if state.updated_at %}
            <span style="float: right; color: #757575; font-size: 0.85rem;">
                Updated: {{ state.updated_at }}
            </span>
            {% endif %}
        </div>

        {% if state.steps %}
        <div class="status-card">
            <h3 style="margin-bottom: 1rem; color: #fff;">Workflow Steps</h3>
            <div class="steps-list">
                {% for step_name, step in state.steps.items() %}
                <div class="step {{ step.status | lower }}">
                    <div class="step-icon {{ step.status | lower }}">
                        {% if step.status == 'SUCCESS' %}✓
                        {% elif step.status == 'FAILED' %}✗
                        {% elif step.status == 'IN_PROGRESS' %}⋯
                        {% else %}○{% endif %}
                    </div>
                    <div class="step-info">
                        <div class="step-name">{{ step_name | replace('_', ' ') | title }}</div>
                        {% if step.name %}
                        <div class="step-detail">{{ step.name }}</div>
                        {% endif %}
                        {% if step.error %}
                        <div class="step-error">{{ step.error }}</div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        {% if state.outputs and state.outputs | length > 0 %}
        <div class="status-card">
            <h3 style="margin-bottom: 1rem; color: #fff;">Outputs</h3>
            <div class="outputs">
                {% for key, value in state.outputs.items() %}
                <div class="output-item">
                    <span class="output-key">{{ key }}</span>
                    <span class="output-value">{{ value }}</span>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        {% if state.errors and state.errors | length > 0 %}
        <div class="status-card" style="border-color: rgba(244, 67, 54, 0.3);">
            <h3 style="margin-bottom: 1rem; color: #ef5350;">Errors</h3>
            {% for error in state.errors %}
            <div style="color: #ef5350; margin-bottom: 0.5rem;">• {{ error }}</div>
            {% endfor %}
        </div>
        {% endif %}

        {% else %}
        <div class="status-card">
            <div class="waiting">
                <div class="spinner"></div>
                <h3>Waiting for deployment to start...</h3>
                <p style="color: #757575; margin-top: 1rem;">
                    Your lab is being provisioned. This page will update automatically.
                </p>
            </div>
        </div>
        {% endif %}

        <div class="refresh-note">
            Page auto-refreshes every 5 seconds
        </div>
    </div>
</body>
</html>
```

**Step 3: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add info/templates/status.html
git commit -m "feat(info): add status page HTML template"
```

---

## Task 3: Add Status Endpoint to tops-info Service

**Files:**
- Modify: `info/app.py`

**Step 1: Update imports and add template support**

Add to imports:

```python
from flask import Flask, jsonify, render_template
```

**Step 2: Add backend state loading**

Add after existing constants:

```python
BACKEND_STATE_FILE = "/state/backend_state.json"


def load_backend_state() -> dict | None:
    """Load backend state from file written by tops-lab.

    Returns:
        Backend state dict or None if not available
    """
    try:
        if os.path.exists(BACKEND_STATE_FILE):
            with open(BACKEND_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load backend state: {e}")
    return None
```

**Step 3: Add status and outputs endpoints**

Add new routes:

```python
@app.route('/status')
def status_page():
    """Render deployment status page."""
    backend_state = load_backend_state()

    # Get fallback info from local state
    petname = metadata.get("petname") if metadata else None
    email = metadata.get("email") if metadata else None

    return render_template(
        'status.html',
        state=backend_state,
        petname=petname,
        email=email
    )


@app.route('/status/json')
def status_json():
    """Return deployment status as JSON."""
    backend_state = load_backend_state()
    if backend_state:
        return jsonify(backend_state)
    return jsonify({"status": "WAITING", "message": "Waiting for backend state"})


@app.route('/outputs')
def get_outputs():
    """Return all outputs from backend state.

    Other UDF services can poll this endpoint to get provisioned resources
    like site_token, lb_hostname, etc.
    """
    backend_state = load_backend_state()
    if backend_state and backend_state.get("outputs"):
        return jsonify(backend_state["outputs"])
    return jsonify({}), 404


@app.route('/outputs/<key>')
def get_output(key: str):
    """Return a specific output value.

    Example: GET /outputs/site_token

    Returns 404 if output not yet available (useful for polling).
    """
    backend_state = load_backend_state()
    if backend_state and backend_state.get("outputs"):
        outputs = backend_state["outputs"]
        if key in outputs:
            return jsonify({key: outputs[key]})
    return jsonify({"error": f"Output '{key}' not available"}), 404
```

**Step 4: Update Flask app to use templates**

Modify Flask app initialization:

```python
app = Flask(__name__, template_folder='templates')
```

**Step 5: Test locally**

Run: `cd info && python app.py`
Expected: `/status` returns HTML page, `/status/json` returns JSON

**Step 6: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add info/app.py
git commit -m "$(cat <<'EOF'
feat(info): add /status and /outputs endpoints

- GET /status returns HTML status page
- GET /status/json returns full status as JSON
- GET /outputs returns all outputs (site_token, lb_hostname, etc.)
- GET /outputs/<key> returns specific output (for polling)
- Reads backend state from /state/backend_state.json
EOF
)"
```

---

## Task 4: Update Dockerfiles

**Files:**
- Modify: `info/Dockerfile`

**Step 1: Ensure templates are copied**

Update `info/Dockerfile` to include templates:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

EXPOSE 5123

CMD ["python", "app.py"]
```

**Step 2: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add info/Dockerfile
git commit -m "fix(info): include templates in Docker image"
```

---

## Task 5: Update Lab Config with State Bucket

**Files:**
- Document: Update lab YAML configs in S3 to include `stateBucket`

**Step 1: Document the required lab config change**

Lab YAML files in S3 need a new field:

```yaml
# Example: {lab_id}.yaml
sqsURL: "https://sqs.us-east-1.amazonaws.com/123456789/tops-udf-lab-queue"
stateBucket: "tops-deployment-state"  # NEW FIELD
# ... other config
```

**Step 2: Update lab/app.py to read stateBucket**

Modify the state bucket constant to read from lab config:

```python
# In lab/app.py, after loading labinfo:
STATE_BUCKET = labinfo.get("stateBucket", os.getenv("STATE_BUCKET", "tops-deployment-state"))
```

**Step 3: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add lab/app.py
git commit -m "feat(lab): read stateBucket from lab config"
```

---

## Task 6: Add Tests

**Files:**
- Create: `lab/test_app.py`
- Create: `info/test_app.py`

**Step 1: Create lab service test**

```python
# lab/test_app.py
"""Tests for tops-lab service."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestPollBackendState:
    """Tests for S3 state polling."""

    def test_poll_returns_state_when_exists(self):
        """Returns parsed state when S3 object exists."""
        from app import poll_backend_state

        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "status": "IN_PROGRESS",
            "steps": {"namespace": {"status": "SUCCESS"}}
        }).encode()
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = poll_backend_state("dep-123", mock_s3)

        assert result["status"] == "IN_PROGRESS"
        assert result["steps"]["namespace"]["status"] == "SUCCESS"

    def test_poll_returns_none_when_not_found(self):
        """Returns None when S3 object doesn't exist."""
        from app import poll_backend_state

        mock_s3 = MagicMock()
        mock_s3.exceptions.NoSuchKey = Exception
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        result = poll_backend_state("dep-123", mock_s3)

        assert result is None
```

**Step 2: Create info service test**

```python
# info/test_app.py
"""Tests for tops-info service."""

import json
import os
from unittest.mock import patch, mock_open

import pytest

from app import app, load_backend_state


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestStatusEndpoint:
    """Tests for /status endpoint."""

    def test_status_json_returns_state(self, client):
        """GET /status/json returns backend state."""
        mock_state = {
            "status": "IN_PROGRESS",
            "petname": "fuzzy-cat",
            "steps": {}
        }

        with patch('app.load_backend_state', return_value=mock_state):
            response = client.get('/status/json')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "IN_PROGRESS"
            assert data["petname"] == "fuzzy-cat"

    def test_status_json_returns_waiting_when_no_state(self, client):
        """GET /status/json returns waiting status when no state."""
        with patch('app.load_backend_state', return_value=None):
            response = client.get('/status/json')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "WAITING"

    def test_status_page_renders_html(self, client):
        """GET /status returns HTML page."""
        mock_state = {
            "status": "COMPLETED",
            "petname": "fuzzy-cat",
            "email": "user@test.com",
            "steps": {"namespace": {"status": "SUCCESS"}}
        }

        with patch('app.load_backend_state', return_value=mock_state):
            with patch('app.metadata', {"petname": "fuzzy-cat", "email": "user@test.com"}):
                response = client.get('/status')

                assert response.status_code == 200
                assert b'fuzzy-cat' in response.data
                assert b'COMPLETED' in response.data


class TestOutputsEndpoint:
    """Tests for /outputs endpoints."""

    def test_outputs_returns_all_outputs(self, client):
        """GET /outputs returns all outputs."""
        mock_state = {
            "status": "COMPLETED",
            "outputs": {
                "site_token": "abc123",
                "lb_hostname": "app.example.com"
            }
        }

        with patch('app.load_backend_state', return_value=mock_state):
            response = client.get('/outputs')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["site_token"] == "abc123"
            assert data["lb_hostname"] == "app.example.com"

    def test_outputs_returns_404_when_no_outputs(self, client):
        """GET /outputs returns 404 when no outputs available."""
        with patch('app.load_backend_state', return_value=None):
            response = client.get('/outputs')

            assert response.status_code == 404

    def test_output_key_returns_specific_output(self, client):
        """GET /outputs/<key> returns specific output."""
        mock_state = {
            "status": "COMPLETED",
            "outputs": {"site_token": "abc123"}
        }

        with patch('app.load_backend_state', return_value=mock_state):
            response = client.get('/outputs/site_token')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["site_token"] == "abc123"

    def test_output_key_returns_404_when_not_found(self, client):
        """GET /outputs/<key> returns 404 when key not found."""
        mock_state = {
            "status": "IN_PROGRESS",
            "outputs": {}
        }

        with patch('app.load_backend_state', return_value=mock_state):
            response = client.get('/outputs/site_token')

            assert response.status_code == 404
```

**Step 3: Add pytest to requirements**

Add to both `lab/requirements.txt` and `info/requirements.txt`:

```
pytest>=7.4.0
```

**Step 4: Run tests**

```bash
cd /home/coder/f5xc-udf-lab-services
python -m pytest lab/test_app.py info/test_app.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add lab/test_app.py info/test_app.py lab/requirements.txt info/requirements.txt
git commit -m "test: add tests for state polling and status endpoint"
```

---

## Task 7: Update CI/CD Workflows

**Files:**
- Modify: `.github/workflows/tops-info.yml`

**Step 1: Add test step to workflow**

Add before the build step:

```yaml
    - name: Run tests
      run: |
        pip install -r info/requirements.txt
        python -m pytest info/test_app.py -v
```

**Step 2: Commit**

```bash
cd /home/coder/f5xc-udf-lab-services
git add .github/workflows/tops-info.yml
git commit -m "ci: add test step to tops-info workflow"
```

---

## Summary

| Task | Files | Purpose |
|------|-------|---------|
| 1 | lab/app.py | Add S3 state polling |
| 2 | info/templates/status.html | Status page template |
| 3 | info/app.py | /status and /outputs endpoints |
| 4 | info/Dockerfile | Include templates |
| 5 | lab/app.py | Read stateBucket from config |
| 6 | lab/test_app.py, info/test_app.py | Unit tests |
| 7 | .github/workflows | CI test step |

## API Endpoints (after implementation)

| Endpoint | Purpose | Consumer |
|----------|---------|----------|
| `GET /status` | HTML status page | User browser |
| `GET /status/json` | Full deployment state | Debugging |
| `GET /outputs` | All outputs (site_token, etc.) | Other UDF services |
| `GET /outputs/<key>` | Specific output value | Other UDF services (polling) |

## Dependencies

- **State Feedback Implementation (job-workers):** ✅ Complete - provides S3 state files
- **S3 Bucket:** `tops-deployment-state` must exist with cross-account read policy

## User Experience

1. User deploys UDF lab
2. `tops-lab` service starts, sends SQS heartbeat, begins polling S3
3. Backend receives SQS, starts provisioning, writes state to S3
4. `tops-lab` reads state from S3, writes to `/state/backend_state.json`
5. User opens `http://<lab-ip>:5123/status`
6. Page shows real-time progress, auto-refreshes every 5 seconds
7. When complete, outputs (site_token, lb_hostname) are displayed
