# F5xc UDF Lab Services

**This service is unusable outside of the F5 Universal Demo Framework (UDF) as it relies on the UDF metadata service.**

## Overview

`tops-lab` is a consolidated service that runs as a single container, combining what was previously split across `lab/` and `info/` into one application under `app/`.

### What it does

- Fetches deployment metadata from the UDF metadata service
- Sends SQS heartbeats to signal the deployment is active
- Polls S3 for provisioning state and lab outputs
- Serves a status page with real-time provisioning progress
- Exposes an outputs API for retrieving Terraform/provisioning results
- Handles CE (Customer Edge) registration status

## API Endpoints

| Endpoint | Description |
|---|---|
| `/` | Status page (HTML) |
| `/health` | Health check |
| `/status/json` | Provisioning status as JSON |
| `/metadata` | UDF deployment metadata |
| `/petname` | Deployment petname |
| `/outputs` | All provisioning outputs |
| `/outputs/<key>` | Single output value by key |
| `/ce/status` | Customer Edge registration status |

## Local Development

### Backend

```bash
cd app
pip install -r requirements.txt
python3 -m pytest test_app.py -v
```

### Frontend

```bash
cd app/frontend
npm install
npm run dev
```
