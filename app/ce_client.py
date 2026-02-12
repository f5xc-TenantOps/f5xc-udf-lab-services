"""CE registration client for F5 XC Customer Edge devices.

Adapted from https://github.com/kreynoldsf5/udf-ce-registration.
Discovers the CE via UDF metadata, registers it with a site token,
and polls until it comes online.
"""

import os
import re
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
CE_CONFIG_READ_PATH = "/api/ves.io.vpm/introspect/read/ves.io.vpm.config"

CE_POLL_INTERVAL = 15       # seconds between polls
CE_SILENCE_TIMEOUT = 600    # 10 min — give up if CE goes completely silent this long
CE_OVERALL_TIMEOUT = 1500   # 25 min — hard cap, CE isn't coming up


def _sanitize_error(exc):
    """Strip noisy connection-pool details from exception messages.

    Raw urllib3/requests exceptions include the full
    HTTPSConnectionPool(...) prefix and nested <urllib3...object>
    references which are confusing for users.
    """
    msg = str(exc)
    # Strip HTTPS?Connection(Pool)?(host=..., port=...): prefix
    msg = re.sub(r"HTTPS?Connection(?:Pool)?\([^)]*\):\s*", "", msg)
    # Strip "Max retries exceeded with url: /path " noise
    msg = re.sub(r"Max retries exceeded with url: \S+\s*", "", msg)
    # Strip "(Caused by " wrapper
    msg = re.sub(r"\(?Caused by\s*", "", msg)
    # Strip exception class wrappers like NewConnectionError('...')
    msg = re.sub(r"\w+Error\(['\"]?", "", msg)
    # Strip <urllib3.connection.HTTPSConnection object at 0x...> references
    msg = re.sub(r"<[^>]+>", "", msg)
    # Clean up stray punctuation left from stripping
    msg = msg.strip("'\"() \n")
    # Collapse leading ": " or ", " left after object removal
    msg = re.sub(r"^[,:]\s*", "", msg)
    # Truncate very long messages
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg


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
        raise RuntimeError(f"CE registration failed: {_sanitize_error(e)}")


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
        raise RuntimeError(f"CE not responding: {_sanitize_error(e)}")


def poll_ce_until_online(ce_ip):
    """Poll CE health endpoint until state is ONLINE or timeout.

    Uses dual timeouts:
    - silence timeout: gives up if CE stops responding entirely
    - overall timeout: hard cap on total wait time

    Args:
        ce_ip: CE management IP address

    Returns dict with final CE status including state, hostname, etc.
    """
    start = time.time()
    last_contact = time.time()
    last_state = "UNKNOWN"

    while True:
        try:
            status = get_ce_status(ce_ip)
            last_contact = time.time()
            last_state = status.get("state", "UNKNOWN")
            print(f"[INFO] CE state: {last_state}")

            if last_state.upper() in ("ONLINE", "PROVISIONED"):
                return {
                    "status": "REGISTERED",
                    "ce_ip": ce_ip,
                    **status,
                }
        except RuntimeError as e:
            print(f"[WARN] CE poll failed: {e}")

        now = time.time()
        if now - last_contact > CE_SILENCE_TIMEOUT:
            return {
                "status": "TIMEOUT",
                "reason": "silence",
                "ce_ip": ce_ip,
                "state": last_state,
                "error": "CE has not responded for 10 minutes",
            }
        if now - start > CE_OVERALL_TIMEOUT:
            return {
                "status": "TIMEOUT",
                "reason": "overall",
                "ce_ip": ce_ip,
                "state": last_state,
                "error": "CE did not come online within 25 minutes",
            }

        time.sleep(CE_POLL_INTERVAL)


def get_ce_config(ce_ip):
    """Read the CE VPM config to retrieve the current registration token.

    Returns the parsed JSON response dict (contains a 'Token' field when
    the CE has been registered).
    Raises RuntimeError if the CE is unreachable.
    """
    url = f"https://{ce_ip}:{CE_PORT}{CE_CONFIG_READ_PATH}"
    try:
        resp = http_requests.get(
            url,
            auth=(CE_USERNAME, CE_PASSWORD),
            verify=False,
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"CE config read failed: {_sanitize_error(e)}")


