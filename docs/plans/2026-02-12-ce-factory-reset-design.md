# CE Stale Registration Detection & Factory Reset

**Date:** 2026-02-12
**Repo:** f5xc-udf-lab-services
**Branch:** hive
**Revert ref:** `b2fcf9f` (restore old early-return in `_run_ce_registration`)

---

## Problem

When a UDF deployment spins down and back up, the XC console site object is
deleted and recreated with a new registration token. However, the CE device
retains the old token and reports `PROVISIONED` -- registered to a ghost site
that no longer exists.

The previous restart-recovery logic in `_run_ce_registration` saw the
`PROVISIONED` state and skipped registration entirely, leaving the CE in a
permanently broken state where it could never join the new site.

## Solution

Compare the token currently configured on the CE with the new site token
fetched from S3. If they differ (or the config endpoint is unreachable on a
`PROVISIONED` CE), factory-reset the device to clear the stale token, wait for
it to reboot into `WAITING_FOR_CONFIG`, then proceed with normal registration.

## VPM Endpoints

| Operation | Method | Path |
|-----------|--------|------|
| Health (existing) | GET | `/api/ves.io.vpm/introspect/read/ves.io.vpm.health` |
| Config write (existing) | POST | `/api/ves.io.vpm/introspect/write/ves.io.vpm.config/update` |
| **Config read (new)** | GET | `/api/ves.io.vpm/introspect/read/ves.io.vpm.config` |
| **Factory reset (new)** | POST | `/api/ves.io.vpm/introspect/write/ves.io.vpm.node/clean` |

All endpoints: port 65500, basic auth (`admin`/`Volterra123`), `verify=False`.

Factory reset body: `{"reboot": true}` -- CE wipes its config, reboots, and
returns in `WAITING_FOR_CONFIG` state.

> **Vendor note:** These VPM introspect endpoints are not publicly documented
> by F5. The factory reset (`/node/clean`) was intentionally hidden from the
> VPM UI. We use it because there is no supported alternative for clearing a
> stale registration token without SSH access to the CE.

## Detection Flow

```
discover CE IP
  -> read CE health (get state)
  -> if PROVISIONED/ONLINE:
      -> read CE config (get current token)
      -> if token == new token -> skip, already registered (restart recovery)
      -> if token != new token -> factory reset -> wait for WAITING_FOR_CONFIG -> register
      -> if config read fails  -> factory reset (cannot verify, assume stale)
  -> if WAITING_FOR_CONFIG / unreachable -> register normally
```

## New CE Status Phases

The RESETTING phase has been added to the CE registration lifecycle:

```
DISCOVERING -> RESETTING (if stale) -> REGISTERING -> PROVISIONING -> REGISTERED
                                                                   -> TIMEOUT
                                                                   -> FAILED
```

The Vue frontend renders RESETTING as an active (in-progress) step with the
subtext "Resetting CE device -- this may take several minutes" or the reason
string from the backend.

## Timing Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `CE_RESET_SETTLE_TIME` | 30s | Sleep before polling after reset (let CE begin reboot) |
| `CE_RESET_BOOT_TIMEOUT` | 300s | Max wait for CE to reach WAITING_FOR_CONFIG |
| `CE_RESET_POLL_INTERVAL` | 10s | Poll interval during post-reset wait |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| VPM endpoints change in CE firmware update | Low | Reset path breaks | Pin CE image version in UDF blueprint; test after upgrades |
| Token encoding changes (whitespace, wrapping) | Low | False mismatch triggers unnecessary reset | `.strip()` on both tokens before comparison |
| CE takes longer than 5 min to reboot | Low | Timeout, registration fails | User can restart the container to retry; CE eventually comes up |
| Factory reset on a correctly-registered CE | Very low | Unnecessary downtime | Token comparison prevents this; only resets on mismatch |
| Config read endpoint returns token in different field name | Low | Cannot detect stale | Defensive: treat config-read failure as stale, reset anyway |

## Error Sanitization

Raw Python exceptions from `urllib3`/`requests` (e.g.,
`HTTPSConnectionPool(host='...', port=65500): Max retries exceeded...`) are
noisy and confusing in the UI. The `_sanitize_error()` helper strips the
connection-pool wrapper and extracts the meaningful inner message, truncating
at 200 characters.

Applied to: `register_ce`, `get_ce_status`, `get_ce_config`, `factory_reset_ce`.

## How to Revert

Restore the old early-return logic that skipped registration when the CE was
already `PROVISIONED`:

```bash
git show b2fcf9f:app/app.py > app/app.py    # restore old _run_ce_registration
git show b2fcf9f:app/ce_client.py > app/ce_client.py  # drop new functions
```

The new `ce_client` functions (`get_ce_config`, `factory_reset_ce`,
`poll_ce_until_state`) and constants are additive and harmless to leave in
place -- they are only called from `_run_ce_registration`.

## Files Changed

| File | Changes |
|------|---------|
| `app/ce_client.py` | New constants, `_sanitize_error`, `get_ce_config`, `factory_reset_ce`, `poll_ce_until_state`; sanitized error messages in existing functions |
| `app/test_ce_client.py` | New test classes: `TestSanitizeError`, `TestGetCeConfig`, `TestFactoryResetCe`, `TestPollCeUntilState` |
| `app/app.py` | Replaced `_run_ce_registration` with stale detection flow |
| `app/frontend/src/App.vue` | Added `RESETTING` to `ceIsActive`, `ceRowStatus`, `ceSubtext` |
| `app/static/*` | Rebuilt frontend assets |
