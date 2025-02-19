"""Service providing an API for deployment information."""
import json
import time
import sys
import requests
from flask import Flask, jsonify

STATE_FILE = "/state/deployment_state.json"
METADATA_BASE_URL = "http://metadata.udf"
MAX_RETRIES = 10
RETRY_DELAY = 6

app = Flask(__name__)

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

@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "running"}), 200

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
