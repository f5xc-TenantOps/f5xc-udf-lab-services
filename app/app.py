"""tops-lab -- consolidated UDF lab service.

Single Flask application with background threads for SQS heartbeat
and S3 state polling. Serves the Vue SPA, deployment status API,
and handles CE registration reactively.
"""

import json
import os
import sys
import threading
import time
from typing import Optional

import boto3
import petname
import requests as http_requests
from flask import Flask, jsonify, send_from_directory

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_FILE = "/state/deployment_state.json"
METADATA_BASE_URL = "http://metadata.udf"
CONFIG_BUCKET = os.getenv("CONFIG_BUCKET", "tops-registry-bucket")

MAX_RETRIES = 10
RETRY_DELAY = 6
SQS_INTERVAL = 90
MAX_SQS_RETRIES = 3
STATE_POLL_INTERVAL = 10

# ---------------------------------------------------------------------------
# Shared in-memory state (written by background threads, read by Flask)
# ---------------------------------------------------------------------------
_backend_state: Optional[dict] = None
_ce_status: Optional[dict] = None
_deployment_state: Optional[dict] = None

# Flag to ensure CE registration fires only once per deployment
_ce_registration_started = False

# Set True when polling loop sees PENDING/IN_PROGRESS — signals that
# a new provisioning cycle is running and the S3 state (including
# outputs.site_token) will be replaced with fresh values.
_seen_provisioning = False

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="")


# ---------------------------------------------------------------------------
# State persistence (for restart recovery)
# ---------------------------------------------------------------------------
def ensure_state_dir():
    """Ensure the state directory exists."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)


def save_deployment_state(dep_id, lab_id, email, pet, config):
    """Save deployment state to disk for restart recovery."""
    ensure_state_dir()
    state = {
        "dep_id": dep_id,
        "lab_id": lab_id,
        "email": email,
        "petname": pet,
        "config": config,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return state


def load_deployment_state():
    """Load deployment state from disk. Returns dict or None."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# UDF metadata
# ---------------------------------------------------------------------------
def _get_metadata_field(path, label):
    """Fetch a single metadata field, raising on non-200 or empty response."""
    resp = http_requests.get(f"{METADATA_BASE_URL}{path}", timeout=5)
    if resp.status_code != 200:
        raise RuntimeError(f"{label}: HTTP {resp.status_code} from {path}")
    value = resp.text.strip()
    if not value:
        raise RuntimeError(f"{label}: empty response from {path}")
    return value


def fetch_metadata():
    """Fetch deployment metadata from the UDF metadata service.

    Retries up to MAX_RETRIES times with RETRY_DELAY between attempts.
    Returns dict with depID, labID, email, awsKey, awsSecret or None.
    """
    for attempt in range(MAX_RETRIES):
        try:
            dep_id = _get_metadata_field("/deployment/id/", "deployment ID")
            lab_id = _get_metadata_field(
                "/userTags/name/labid/value/",
                "lab ID (labid tag) -- add a 'labid' user tag to this UDF instance",
            )
            email = _get_metadata_field("/deployment/deployer/", "deployer email")

            creds_resp = http_requests.get(
                f"{METADATA_BASE_URL}/cloudAccounts", timeout=5
            )
            if creds_resp.status_code != 200:
                raise RuntimeError(
                    f"Cloud accounts: HTTP {creds_resp.status_code} "
                    f"-- is a cloud account attached to this deployment?"
                )
            aws_creds = creds_resp.json()

            accounts = aws_creds.get("cloudAccounts", [])
            if not accounts:
                raise RuntimeError(
                    "No cloud accounts found in UDF metadata. "
                    "Attach an AWS cloud account to this deployment."
                )

            credentials = accounts[0].get("credentials", [])
            if not credentials:
                raise RuntimeError(
                    "Cloud account has no credentials. "
                    "Check the cloud account configuration in UDF."
                )

            key = credentials[0].get("key")
            secret = credentials[0].get("secret")
            if not key or not secret:
                raise RuntimeError(
                    "Cloud account credentials are missing key or secret."
                )

            return {
                "depID": dep_id,
                "labID": lab_id,
                "email": email,
                "awsKey": key,
                "awsSecret": secret,
            }
        except http_requests.RequestException as e:
            print(f"[RETRY {attempt + 1}/{MAX_RETRIES}] Metadata service unreachable: {e}")
        except RuntimeError as e:
            print(f"[RETRY {attempt + 1}/{MAX_RETRIES}] {e}")
        time.sleep(RETRY_DELAY)

    print("[FATAL] Metadata not available after all retries. Exiting.")
    return None


# ---------------------------------------------------------------------------
# Global config from S3
# ---------------------------------------------------------------------------
def fetch_global_config(aws_key, aws_secret):
    """Read config.json from S3 CONFIG_BUCKET.

    Retries up to MAX_RETRIES times with RETRY_DELAY between attempts.
    Returns dict with sqsURL and stateBucket, or None on failure.
    """
    try:
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
        )
    except Exception as e:
        print(f"[ERROR] Failed to create S3 client: {e}")
        return None

    for attempt in range(MAX_RETRIES):
        try:
            obj = s3.get_object(Bucket=CONFIG_BUCKET, Key="config.json")
            data = json.loads(obj["Body"].read().decode("utf-8"))
            if "sqsURL" not in data:
                print(f"[ERROR] config.json is missing 'sqsURL'. Contents: {list(data.keys())}")
                return None
            return data
        except s3.exceptions.NoSuchBucket:
            print(f"[ERROR] S3 bucket '{CONFIG_BUCKET}' does not exist. "
                  f"Check CONFIG_BUCKET env var or create the bucket.")
            return None
        except s3.exceptions.NoSuchKey:
            print(f"[RETRY {attempt + 1}/{MAX_RETRIES}] config.json not found in "
                  f"s3://{CONFIG_BUCKET}/. Waiting for backend to create it...")
            time.sleep(RETRY_DELAY)
        except json.JSONDecodeError as e:
            print(f"[ERROR] config.json is not valid JSON: {e}")
            return None
        except Exception as e:
            print(f"[RETRY {attempt + 1}/{MAX_RETRIES}] Failed to fetch "
                  f"s3://{CONFIG_BUCKET}/config.json: {e}")
            time.sleep(RETRY_DELAY)

    print(f"[FATAL] config.json not available after {MAX_RETRIES} retries.")
    return None


# ---------------------------------------------------------------------------
# S3 state polling
# ---------------------------------------------------------------------------
def poll_backend_state(dep_id, s3_client, state_bucket):
    """Poll S3 for the deployment state file.

    Returns parsed state dict or None if not found.
    """
    try:
        response = s3_client.get_object(
            Bucket=state_bucket, Key=f"{dep_id}.json"
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except s3_client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"[WARN] Failed to poll backend state: {e}")
        return None


def state_polling_loop(dep_id, s3_client, state_bucket):
    """Background thread: poll S3 for backend state every STATE_POLL_INTERVAL seconds.

    Updates the module-level _backend_state variable and triggers CE
    registration when the site token appears in outputs.

    Also tracks whether a new provisioning cycle has started
    (_seen_provisioning) so that CE registration can wait for the
    fresh token instead of comparing against a stale one.
    """
    global _backend_state, _ce_registration_started, _seen_provisioning

    expected_petname = _deployment_state.get("petname") if _deployment_state else None

    while True:
        state = poll_backend_state(dep_id, s3_client, state_bucket)
        if state:
            # Ignore stale S3 state from a previous deployment cycle
            state_petname = state.get("petname")
            if expected_petname and state_petname and state_petname != expected_petname:
                print(f"[INFO] Ignoring stale S3 state "
                      f"(petname {state_petname} != {expected_petname})")
                time.sleep(STATE_POLL_INTERVAL)
                continue

            _backend_state = state
            status = (state.get("status") or "").upper()
            print(f"[INFO] Backend state updated: {status or 'unknown'}")

            # Track whether a new provisioning cycle is running
            if status in ("PENDING", "IN_PROGRESS"):
                _seen_provisioning = True

            # Trigger CE registration when site token is available in outputs
            token = state.get("outputs", {}).get("site_token")
            if token and not _ce_registration_started:
                _ce_registration_started = True
                ce_thread = threading.Thread(
                    target=_run_ce_registration,
                    args=(token,),
                    daemon=True,
                )
                ce_thread.start()

        time.sleep(STATE_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# SQS heartbeat
# ---------------------------------------------------------------------------
def _parse_sqs_region(sqs_url):
    """Extract AWS region from SQS URL. Raises RuntimeError on bad format."""
    # Expected: https://sqs.<region>.amazonaws.com/...
    try:
        parts = sqs_url.split(".")
        if len(parts) < 2 or not parts[1]:
            raise ValueError
        return parts[1]
    except (ValueError, IndexError):
        raise RuntimeError(
            f"Cannot parse region from SQS URL: {sqs_url} "
            f"-- expected https://sqs.<region>.amazonaws.com/..."
        )


def send_sqs(sqs_url, aws_key, aws_secret, dep_id, lab_id, email, pet):
    """Send heartbeat message to SQS. Returns True on success, False after retries."""
    try:
        region = _parse_sqs_region(sqs_url)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        return False

    sqs = boto3.client(
        "sqs",
        region_name=region,
        aws_access_key_id=aws_key,
        aws_secret_access_key=aws_secret,
    )
    payload = {
        "lab_id": lab_id,
        "dep_id": dep_id,
        "email": email,
        "petname": pet,
    }

    for attempt in range(1, MAX_SQS_RETRIES + 1):
        try:
            resp = sqs.send_message(
                QueueUrl=sqs_url, MessageBody=json.dumps(payload)
            )
            print(f"SQS message sent: {resp['MessageId']}")
            return True
        except Exception as e:
            print(f"[WARN] SQS send attempt {attempt}/{MAX_SQS_RETRIES} failed: {e}")
            time.sleep(RETRY_DELAY)

    print(f"[ERROR] SQS heartbeat failed {MAX_SQS_RETRIES} times. Will retry next interval.")
    return False


def sqs_heartbeat_loop(sqs_url, aws_key, aws_secret, dep_id, lab_id, email, pet):
    """Background thread: send SQS heartbeat every SQS_INTERVAL seconds."""
    consecutive_failures = 0
    while True:
        success = send_sqs(sqs_url, aws_key, aws_secret, dep_id, lab_id, email, pet)
        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= 5:
                print("[ERROR] SQS heartbeat has failed 5 consecutive intervals. "
                      "Backend will not extend deployment TTL.")
        time.sleep(SQS_INTERVAL)


# ---------------------------------------------------------------------------
# CE registration (reactive -- triggered by state_polling_loop)
# ---------------------------------------------------------------------------
PROVISIONING_GRACE_PERIOD = 30   # seconds to wait for provisioning to start
PROVISIONING_WAIT_TIMEOUT = 300  # seconds to wait for provisioning to complete


def _get_latest_site_token():
    """Read the current site_token from backend state."""
    return (_backend_state or {}).get("outputs", {}).get("site_token", "")


def _wait_for_fresh_token(original_token):
    """Wait for the site token to stabilize after any new provisioning.

    The site_token that triggered this thread may be stale (from an S3
    state file that persisted across deployment cycles).  This function
    ensures we don't compare against a stale token:

    1. If a provisioning cycle has already been detected
       (_seen_provisioning), wait for it to complete and return the
       fresh token.
    2. If not yet detected, wait a grace period — the step function
       may not have started yet.  If provisioning starts during the
       grace period, wait for completion.
    3. If no provisioning is detected after the grace period, this is
       a genuine restart — the current token is valid.
    """
    # If provisioning already detected, skip the grace period
    if _seen_provisioning:
        print("[INFO] Provisioning cycle detected — waiting for completion")
        return _wait_for_provisioning_complete(original_token)

    # Grace period: wait to see if provisioning starts
    print(f"[INFO] Waiting up to {PROVISIONING_GRACE_PERIOD}s "
          f"for provisioning cycle to start")
    grace_start = time.time()
    while time.time() - grace_start < PROVISIONING_GRACE_PERIOD:
        if _seen_provisioning:
            print("[INFO] Provisioning cycle detected — waiting for completion")
            return _wait_for_provisioning_complete(original_token)
        time.sleep(5)

    # No provisioning detected — genuine restart
    print("[INFO] No provisioning cycle detected — using current token")
    return _get_latest_site_token() or original_token


def _wait_for_provisioning_complete(original_token):
    """Wait until backend state reaches COMPLETED with a token."""
    start = time.time()
    while time.time() - start < PROVISIONING_WAIT_TIMEOUT:
        status = (_backend_state or {}).get("status", "").upper()
        token = _get_latest_site_token()
        if status == "COMPLETED" and token:
            if token != original_token:
                print("[INFO] Fresh token available from new provisioning")
            return token
        time.sleep(5)

    print("[WARN] Timed out waiting for provisioning to complete")
    return _get_latest_site_token() or original_token


def _run_ce_registration(site_token):
    """Register the CE device using the site token.

    Imported lazily from ce_client to keep the module optional.

    Flow:
      1. Discover CE IP via UDF metadata
      2. Wait for CE to become reachable (it may still be booting)
      3. Wait for any in-flight provisioning to complete so we have
         a fresh token (not stale from a previous deployment cycle)
      4. Read CE config and compare tokens — this is determinative:
         - Token match  → restart recovery, skip registration
         - Token mismatch → stale/orphaned CE, fail with clear message
         - No token      → fresh CE, proceed with registration
    """
    global _ce_status
    try:
        from ce_client import (
            discover_ce_ip,
            register_ce,
            wait_for_ce,
            get_ce_config,
            poll_ce_until_online,
        )

        _ce_status = {"status": "DISCOVERING"}

        ce_ip = discover_ce_ip()

        # Wait for CE to become reachable (retries for up to 5 min)
        _ce_status = {"status": "DISCOVERING", "ce_ip": ce_ip}
        current = wait_for_ce(ce_ip)
        current_state = (current.get("state") or "").upper()
        print(f"[INFO] CE reachable — state: {current_state}")

        # Wait for any in-flight provisioning to complete so we
        # compare against a fresh token, not a stale one from an
        # S3 state file that persisted across deployment cycles.
        effective_token = _wait_for_fresh_token(site_token)

        # Token check is determinative
        try:
            config = get_ce_config(ce_ip)
            ce_token = (config.get("Vpm", {}).get("Token") or "").strip()
            new_token = (effective_token or "").strip()
            print(f"[DEBUG] CE token present: {bool(ce_token)}, "
                  f"new token present: {bool(new_token)}, "
                  f"match: {ce_token == new_token if ce_token else 'n/a'}")

            if ce_token and new_token:
                if ce_token == new_token:
                    _ce_status = {"status": "REGISTERED", "ce_ip": ce_ip, **current}
                    print(f"[INFO] CE token matches — restart recovery")
                    return
                else:
                    _ce_status = {
                        "status": "FAILED",
                        "ce_ip": ce_ip,
                        "error": (
                            "CE is registered to a previous site that no longer exists. "
                            "A new UDF deployment is needed to reset the CE."
                        ),
                    }
                    print(f"[ERROR] CE token mismatch — stale registration")
                    return
        except RuntimeError as e:
            print(f"[WARN] Cannot read CE config ({e}) — proceeding with registration")

        # No token on CE (or config unreadable) → register normally
        _ce_status = {"status": "REGISTERING", "ce_ip": ce_ip}

        register_ce(ce_ip, effective_token)
        _ce_status = {"status": "PROVISIONING", "ce_ip": ce_ip}

        final_status = poll_ce_until_online(ce_ip)
        _ce_status = final_status
        print(f"[INFO] CE registration complete: {final_status.get('state', 'UNKNOWN')}")

    except Exception as e:
        _ce_status = {"status": "FAILED", "error": str(e)}
        print(f"[ERROR] CE registration failed: {e}")


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Serve Vue SPA."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    """Health check."""
    return jsonify({"status": "running"})


@app.route("/status/json")
def status_json():
    """Return deployment status as JSON."""
    if _backend_state:
        return jsonify(_backend_state)
    return jsonify({"status": "WAITING", "message": "Waiting for backend state"})


@app.route("/metadata")
def get_metadata():
    """Return deployment metadata."""
    if _deployment_state:
        return jsonify({
            "dep_id": _deployment_state.get("dep_id"),
            "lab_id": _deployment_state.get("lab_id"),
            "email": _deployment_state.get("email"),
            "petname": _deployment_state.get("petname"),
        })
    return jsonify({}), 404


@app.route("/petname")
def get_petname():
    """Return the deployment petname."""
    if _deployment_state:
        return jsonify({"petname": _deployment_state.get("petname")})
    return jsonify({}), 404


@app.route("/outputs")
def get_outputs():
    """Return all outputs from backend state."""
    if _backend_state and _backend_state.get("outputs"):
        return jsonify(_backend_state["outputs"])
    return jsonify({}), 404


@app.route("/outputs/<key>")
def get_output(key):
    """Return a specific output value. 404 if not yet available."""
    if _backend_state and _backend_state.get("outputs"):
        outputs = _backend_state["outputs"]
        if key in outputs:
            return jsonify({key: outputs[key]})
    return jsonify({"error": f"Output '{key}' not available"}), 404


@app.route("/ce/status")
def ce_status():
    """Return CE registration status."""
    if _ce_status:
        return jsonify(_ce_status)
    return jsonify({"status": "NOT_STARTED"})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def main():
    """Entry point: initialise state, start background threads, run Flask."""
    global _deployment_state

    # 1. Check for existing state (restart recovery)
    existing = load_deployment_state()

    # 2. Fetch metadata
    metadata = fetch_metadata()
    if not metadata:
        return

    print(f"Metadata fetched: depID={metadata['depID']}, labID={metadata['labID']}")

    # If restart with same deployment, recover petname and config
    if existing and existing.get("dep_id") == metadata["depID"]:
        print("[INFO] Restart detected -- recovering previous state")
        pet = existing["petname"]
        config = existing["config"]
    else:
        # 3. Fetch global config
        config = fetch_global_config(metadata["awsKey"], metadata["awsSecret"])
        if not config:
            print("[FATAL] Cannot proceed without global config. Exiting.")
            return

        # 4. Generate petname
        pet = petname.Generate()

        # 5. Save deployment state
        save_deployment_state(
            dep_id=metadata["depID"],
            lab_id=metadata["labID"],
            email=metadata["email"],
            pet=pet,
            config=config,
        )

    _deployment_state = {
        "dep_id": metadata["depID"],
        "lab_id": metadata["labID"],
        "email": metadata["email"],
        "petname": pet,
        "config": config,
    }

    sqs_url = config["sqsURL"]
    state_bucket = config.get("stateBucket", "tops-deployment-state")

    # S3 client for state polling
    s3_client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id=metadata["awsKey"],
        aws_secret_access_key=metadata["awsSecret"],
    )

    # 6. Start SQS heartbeat thread
    sqs_thread = threading.Thread(
        target=sqs_heartbeat_loop,
        args=(sqs_url, metadata["awsKey"], metadata["awsSecret"],
              metadata["depID"], metadata["labID"], metadata["email"], pet),
        daemon=True,
    )
    sqs_thread.start()

    # 7. Start S3 state polling thread
    poll_thread = threading.Thread(
        target=state_polling_loop,
        args=(metadata["depID"], s3_client, state_bucket),
        daemon=True,
    )
    poll_thread.start()

    # 8. Start Flask (main thread)
    print(f"[INFO] Starting Flask on port 5123 -- petname={pet}")
    app.run(host="0.0.0.0", port=5123, debug=False, threaded=True)


if __name__ == "__main__":
    main()
