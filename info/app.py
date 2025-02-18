"""Service providing an API for deployment information."""
import json
import time
import sys
from flask import Flask, jsonify


STATE_FILE = "/state/deployment_state.json"
MAX_WAIT_TIME = 60  # Maximum wait time in seconds
RETRY_DELAY = 5  # Time to wait between retries in seconds

def load_state():
    """Load deployment state from a file."""
    try:
        with open(STATE_FILE, 'r', encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def wait_for_state():
    """Wait for the state file to appear, retrying every 5 seconds up to 1 minute."""
    elapsed_time = 0
    while elapsed_time < MAX_WAIT_TIME:
        state = load_state()
        if state:
            return state
        print(f"State file not found. Retrying in {RETRY_DELAY} seconds...")
        time.sleep(RETRY_DELAY)
        elapsed_time += RETRY_DELAY

    print(f"Error: State file {STATE_FILE} not found after {MAX_WAIT_TIME} seconds. Exiting.")
    sys.exit(1)

def validate_state(state):
    """Validate that state contains required keys."""
    if not state:
        print("Error: State file is empty or malformed. Exiting.")
        sys.exit(1)

    metadata = state.get("metadata")
    lab_info = state.get("labinfo")

    if not metadata:
        print("Error: 'metadata' key is missing in state file. Exiting.")
        sys.exit(1)

    if not lab_info:
        print("Error: 'labinfo' key is missing in state file. Exiting.")
        sys.exit(1)

    petname = metadata.get("petname")
    if not petname:
        print("Error: 'petname' is missing in metadata. Exiting.")
        sys.exit(1)

    return metadata, lab_info, petname

def main():
    """Main function to set up and run the Flask app."""
    state = wait_for_state()
    metadata, lab_info, petname = validate_state(state)

    app = Flask(__name__)

    @app.route('/')
    def index():
        """Return a list of all API endpoints."""
        endpoints = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint != 'static':
                endpoints.append({
                    "route": rule.rule,
                    "methods": list(rule.methods)
                })
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

    print("State file loaded successfully. Starting API server.")
    app.run(host='0.0.0.0', port=5123, debug=False)

if __name__ == '__main__':
    main()
