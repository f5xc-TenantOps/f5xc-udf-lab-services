"""tops-lab -- base service for F5XC UDF labs."""
import os
import sys
import time
import json
import requests
import boto3
import petname
import yaml

STATE_FILE = "/state/deployment_state.json"
METADATA_BASE_URL = "http://metadata.udf"
MAX_RETRIES = 10
RETRY_DELAY = 6
SQS_INTERVAL = 90
MAX_SQS_RETRIES = 3

LAB_INFO_BUCKET = os.getenv("LAB_INFO_BUCKET")

if not LAB_INFO_BUCKET:
    print("Error: LAB_INFO_BUCKET environment variable is not set.")
    sys.exit(1)

def ensure_state_dir():
    """Ensure the state directory exists."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

def save_state(state):
    """Save full deployment state to a file."""
    ensure_state_dir()
    with open(STATE_FILE, 'w', encoding="utf-8") as f:
        json.dump(state, f)

def load_state():
    """Load deployment state from a file."""
    try:
        with open(STATE_FILE, 'r', encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def fetch_metadata():
    """Fetch and structure metadata, retrying until the service is available."""
    for attempt in range(MAX_RETRIES):
        try:
            dep_id = requests.get(f"{METADATA_BASE_URL}/deployment/id/", timeout=5).text.strip()
            lab_id = requests.get(f"{METADATA_BASE_URL}/userTags/name/labid/value/", timeout=5).text.strip()
            aws_creds = requests.get(f"{METADATA_BASE_URL}/cloudAccounts", timeout=5).json()

            return {
                "depID": dep_id,
                "labID": lab_id,
                "awsKey": aws_creds["cloudAccounts"][0]["credentials"][0]["key"],
                "awsSecret": aws_creds["cloudAccounts"][0]["credentials"][0]["secret"],
            }
        except (requests.RequestException, KeyError, IndexError) as e:
            print(f"Metadata fetch attempt {attempt + 1} failed: {e}")
            time.sleep(RETRY_DELAY)

    print("Metadata service unavailable after retries. Exiting.")
    return None

def get_lab_info(metadata):
    """Fetch lab-specific info from S3."""
    try:
        client = boto3.client(
            's3',
            region_name="us-east-1",
            aws_access_key_id=metadata["awsKey"],
            aws_secret_access_key=metadata["awsSecret"]
        )

        obj = client.get_object(Bucket=LAB_INFO_BUCKET, Key=f"{metadata['labID']}.yaml")
        data = obj['Body'].read().decode('utf-8')
        return yaml.safe_load(data)
    except Exception as e:
        print(f"Error retrieving lab info from S3 ({LAB_INFO_BUCKET}): {e}")
        return None

def send_sqs(meta):
    """Send metadata to SQS, failing after 3 unsuccessful attempts."""
    sqs = boto3.client(
        'sqs', 
        region_name=meta["sqsRegion"],
        aws_access_key_id=meta["awsKey"],
        aws_secret_access_key=meta["awsSecret"]
    )

    failed_attempts = 0

    while failed_attempts < MAX_SQS_RETRIES:
        try:
            response = sqs.send_message(QueueUrl=meta["sqsURL"], MessageBody=json.dumps(meta))
            print(f"SQS message sent: {response['MessageId']}")
            return True  # Successfully sent, reset failure counter
        except Exception as e:
            failed_attempts += 1
            print(f"SQS send attempt {failed_attempts} failed: {e}")
            time.sleep(RETRY_DELAY)

    print(f"SQS message failed {MAX_SQS_RETRIES} times. Exiting.")
    sys.exit(1)

def main():
    """Main function."""
    metadata = fetch_metadata()
    if not metadata:
        return

    print(f"Metadata fetched: {metadata}")
    lab_info = get_lab_info(metadata)
    if not lab_info or "sqsURL" not in lab_info:
        print("Lab info missing or SQS URL not found. Exiting.")
        return

    metadata["sqsURL"] = lab_info["sqsURL"]
    metadata["sqsRegion"] = metadata["sqsURL"].split('.')[1]

    previous_state = load_state()
    if previous_state and previous_state.get("depID") == metadata["depID"]:
        metadata["petname"] = previous_state["petname"]
    else:
        metadata["petname"] = petname.Generate()
        save_state(metadata)

    while True:
        send_sqs(metadata)
        time.sleep(SQS_INTERVAL)

if __name__ == "__main__":
    main()