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

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="/static")


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
def fetch_metadata():
    """Fetch deployment metadata from the UDF metadata service.

    Retries up to MAX_RETRIES times with RETRY_DELAY between attempts.
    Returns dict with depID, labID, email, awsKey, awsSecret or None.
    """
    for attempt in range(MAX_RETRIES):
        try:
            dep_id = http_requests.get(
                f"{METADATA_BASE_URL}/deployment/id/", timeout=5
            ).text.strip()
            lab_id = http_requests.get(
                f"{METADATA_BASE_URL}/userTags/name/labid/value/", timeout=5
            ).text.strip()
            email = http_requests.get(
                f"{METADATA_BASE_URL}/deployment/deployer/", timeout=5
            ).text.strip()
            aws_creds = http_requests.get(
                f"{METADATA_BASE_URL}/cloudAccounts", timeout=5
            ).json()

            return {
                "depID": dep_id,
                "labID": lab_id,
                "email": email,
                "awsKey": aws_creds["cloudAccounts"][0]["credentials"][0]["key"],
                "awsSecret": aws_creds["cloudAccounts"][0]["credentials"][0]["secret"],
            }
        except (http_requests.RequestException, KeyError, IndexError) as e:
            print(f"Metadata fetch attempt {attempt + 1} failed: {e}")
            time.sleep(RETRY_DELAY)

    print("Metadata service unavailable after retries. Exiting.")
    return None


# ---------------------------------------------------------------------------
# Global config from S3
# ---------------------------------------------------------------------------
def fetch_global_config(aws_key, aws_secret):
    """Read config.json from S3 CONFIG_BUCKET.

    Returns dict with sqsURL and stateBucket, or None on failure.
    """
    try:
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
        )
        obj = s3.get_object(Bucket=CONFIG_BUCKET, Key="config.json")
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"Error fetching global config from s3://{CONFIG_BUCKET}/config.json: {e}")
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
    registration when site_token first appears in outputs.
    """
    global _backend_state, _ce_registration_started

    while True:
        state = poll_backend_state(dep_id, s3_client, state_bucket)
        if state:
            _backend_state = state
            print(f"[INFO] Backend state updated: {state.get('status', 'unknown')}")

            # Trigger CE registration when site_token appears
            outputs = state.get("outputs", {})
            if "site_token" in outputs and not _ce_registration_started:
                _ce_registration_started = True
                ce_thread = threading.Thread(
                    target=_run_ce_registration,
                    args=(outputs["site_token"],),
                    daemon=True,
                )
                ce_thread.start()

        time.sleep(STATE_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# SQS heartbeat
# ---------------------------------------------------------------------------
def send_sqs(sqs_url, aws_key, aws_secret, dep_id, lab_id, email, pet):
    """Send heartbeat message to SQS. Exits after MAX_SQS_RETRIES failures."""
    region = sqs_url.split(".")[1]
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

    failed = 0
    while failed < MAX_SQS_RETRIES:
        try:
            resp = sqs.send_message(
                QueueUrl=sqs_url, MessageBody=json.dumps(payload)
            )
            print(f"SQS message sent: {resp['MessageId']}")
            return True
        except Exception as e:
            failed += 1
            print(f"SQS send attempt {failed} failed: {e}")
            time.sleep(RETRY_DELAY)

    print(f"SQS message failed {MAX_SQS_RETRIES} times. Exiting.")
    sys.exit(1)


def sqs_heartbeat_loop(sqs_url, aws_key, aws_secret, dep_id, lab_id, email, pet):
    """Background thread: send SQS heartbeat every SQS_INTERVAL seconds."""
    while True:
        send_sqs(sqs_url, aws_key, aws_secret, dep_id, lab_id, email, pet)
        time.sleep(SQS_INTERVAL)


# ---------------------------------------------------------------------------
# CE registration (reactive -- triggered by state_polling_loop)
# ---------------------------------------------------------------------------
def _run_ce_registration(site_token):
    """Register the CE device using the site token.

    Imported lazily from ce_client to keep the module optional.
    """
    global _ce_status
    try:
        from ce_client import discover_ce_ip, register_ce, poll_ce_until_online

        _ce_status = {"status": "DISCOVERING"}

        ce_ip = discover_ce_ip()
        _ce_status = {"status": "REGISTERING", "ce_ip": ce_ip}

        register_ce(ce_ip, site_token)
        _ce_status = {"status": "POLLING", "ce_ip": ce_ip}

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
        if not config or "sqsURL" not in config:
            print("Global config missing or sqsURL not found. Exiting.")
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
