"""Service providing an API for deployment information."""
import json
import os
import time
import sys
import requests
from flask import Flask, jsonify, render_template

STATE_FILE = "/state/deployment_state.json"
BACKEND_STATE_FILE = "/state/backend_state.json"
METADATA_BASE_URL = "http://metadata.udf"
MAX_RETRIES = 10
RETRY_DELAY = 6

app = Flask(__name__, template_folder='templates')

def fetch_depid():
    """Fetch and structure metadata, retrying until the service is available."""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(f"{METADATA_BASE_URL}/deployment/id/", timeout=5)
            return response.text.strip()
        except requests.RequestException as e:
            print(f"Metadata fetch attempt {attempt + 1} failed: {e}")
            time.sleep(RETRY_DELAY)
    return None 

def load_state():
    """Load deployment state from a file."""
    try:
        with open(STATE_FILE, 'r', encoding="utf-8") as f:
            state = json.load(f)
            if state.get("metadata", {}).get("depID") == fetch_depid():
                return state
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return None


def load_backend_state() -> dict | None:
    """Load backend state from file written by tops-lab.

    Returns:
        Backend state dict or None if not available
    """
    try:
        if os.path.exists(BACKEND_STATE_FILE):
            with open(BACKEND_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load backend state: {e}")
    return None


def wait_for_state():
    """Wait for a current state file."""
    for attempt in range(1, MAX_RETRIES + 1):
        state = load_state()
        if state:
            return state
        print(f"State file not synced. Retrying in {RETRY_DELAY} seconds (Attempt {attempt}/{MAX_RETRIES})...")
        time.sleep(RETRY_DELAY)

    print(f"Error: Synced state file {STATE_FILE} not found after {MAX_RETRIES} attempts. Exiting.")
    sys.exit(1)

def validate_state(state):
    """Validate that state contains required keys."""
    if not state:
        raise ValueError("State file is empty or malformed.")

    metadata = state.get("metadata")
    lab_info = state.get("labinfo")

    if not metadata:
        raise ValueError("Error: 'metadata' key is missing in state file.")

    if not lab_info:
        raise ValueError("Error: 'labinfo' key is missing in state file.")

    petname = metadata.get("petname")
    if not petname:
        raise ValueError("Error: 'petname' is missing in metadata.")

    return metadata, lab_info, petname

@app.route('/')
def index():
    """Return a list of all API endpoints."""
    endpoints = [{"route": rule.rule} for rule in app.url_map.iter_rules() if rule.endpoint != 'static']
    return jsonify(endpoints)

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "running"}), 200


@app.route('/status')
def status_page():
    """Render deployment status page."""
    backend_state = load_backend_state()

    # Get fallback info from local state
    local_petname = metadata.get("petname") if metadata else None
    local_email = metadata.get("email") if metadata else None

    return render_template(
        'status.html',
        state=backend_state,
        petname=local_petname,
        email=local_email
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


@app.route('/metadata', methods=['GET'])
def get_metadata():
    return jsonify(metadata), 200

@app.route('/labinfo', methods=['GET'])
def get_labinfo():
    return jsonify(lab_info), 200

@app.route('/petname', methods=['GET'])
def get_petname():
    return jsonify({"petname": petname}), 200

def main():
    """Main function"""
    global metadata, lab_info, petname
    state = wait_for_state()
    metadata, lab_info, petname = validate_state(state)

    print("State file loaded successfully. Starting API server.")
    app.run(host='0.0.0.0', port=5123, debug=False, threaded=True)

if __name__ == '__main__':
    main()
