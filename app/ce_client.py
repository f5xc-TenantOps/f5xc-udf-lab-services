"""CE registration client for F5 XC Customer Edge devices.

Adapted from https://github.com/kreynoldsf5/udf-ce-registration.
Discovers the CE via UDF metadata, registers it with a site token,
and polls until it comes online.
"""

import os
import time
import urllib3

import requests as http_requests

# Suppress InsecureRequestWarning for self-signed CE certs in lab
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

METADATA_BASE_URL = "http://metadata.udf"
CE_PORT = 65500
CE_USERNAME = os.environ.get("CE_USERNAME", "admin")
CE_PASSWORD = os.environ.get("CE_PASSWORD", "Volterra123")
CE_CONFIG_PATH = "/api/ves.io.vpm/introspect/write/ves.io.vpm.config/update"
CE_HEALTH_PATH = "/api/ves.io.vpm/introspect/read/ves.io.vpm.health"

CE_POLL_INTERVAL = 15  # seconds
CE_TIMEOUT = 600  # 10 minutes


def discover_ce_ip():
    """Discover CE management IP from the UDF metadata service.

    Looks for instances tagged with role=CE.
    Returns the mgmtIp string.
    Raises RuntimeError if CE cannot be found.
    """
    try:
        resp = http_requests.get(
            f"{METADATA_BASE_URL}/userTags/name/role/value/CE", timeout=5
        )
        if resp.status_code == 400:
            raise RuntimeError(
                "No 'role' user tag found. Add a user tag role=CE to the "
                "CE instance in this UDF deployment."
            )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            raise RuntimeError(
                "No instances tagged role=CE found. Tag the CE instance "
                "with user tag role=CE in UDF."
            )
        return results[0]["mgmtIp"]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Cannot discover CE: {e}")


def register_ce(ce_ip, site_token):
    """POST the site registration token to the CE config endpoint.

    Args:
        ce_ip: CE management IP address
        site_token: JWT site registration token

    Raises RuntimeError on failure.
    """
    url = f"https://{ce_ip}:{CE_PORT}{CE_CONFIG_PATH}"
    payload = {"token": site_token}
    try:
        resp = http_requests.post(
            url,
            json=payload,
            auth=(CE_USERNAME, CE_PASSWORD),
            verify=False,
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[INFO] CE registration submitted to {ce_ip}")
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"CE registration failed: {e}")


def get_ce_status(ce_ip):
    """Get current CE status from the health endpoint.

    Returns dict with state, hostname, os_version, public_ip.
    Raises RuntimeError if CE is not responding.
    """
    url = f"https://{ce_ip}:{CE_PORT}{CE_HEALTH_PATH}"
    try:
        resp = http_requests.get(
            url,
            auth=(CE_USERNAME, CE_PASSWORD),
            verify=False,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "state": data.get("state", "UNKNOWN"),
            "hostname": data.get("hostname", ""),
            "os_version": data.get("os_version", ""),
            "public_ip": data.get("public_ip", ""),
        }
    except Exception as e:
        raise RuntimeError(f"CE not responding: {e}")


def poll_ce_until_online(ce_ip):
    """Poll CE health endpoint until state is ONLINE or timeout.

    Args:
        ce_ip: CE management IP address

    Returns dict with final CE status including state, hostname, etc.
    """
    start = time.time()
    last_state = "UNKNOWN"

    while time.time() - start < CE_TIMEOUT:
        try:
            status = get_ce_status(ce_ip)
            last_state = status.get("state", "UNKNOWN")
            print(f"[INFO] CE state: {last_state}")

            if last_state.upper() == "ONLINE":
                return {
                    "status": "REGISTERED",
                    "ce_ip": ce_ip,
                    **status,
                }
        except RuntimeError as e:
            print(f"[WARN] CE poll failed: {e}")

        time.sleep(CE_POLL_INTERVAL)

    return {
        "status": "TIMEOUT",
        "ce_ip": ce_ip,
        "state": last_state,
        "error": f"CE did not come online within {CE_TIMEOUT}s",
    }
