"""Headless Fyers TOTP login — the 5-step automated flow (no browser/webhook).

Ported from REFERENCE/auth/auth.py and parameterized by explicit credentials so
it has no dependency on global config or the database. It performs the HTTP
dance and returns tokens; persistence is the caller's job (TokenManager).

fyers_apiv3 is imported lazily inside step5 only, so this module imports fine in
environments without the SDK (e.g. local Python 3.14).
"""
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from hashlib import sha256
from urllib.parse import parse_qs, urlparse
from typing import Callable, Optional

import requests
import pyotp

log = logging.getLogger("totp_login")

# Fyers' vagator/api endpoints reject requests without these browser-like headers.
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
    "Origin": "https://api-t1.fyers.in",
    "Referer": "https://api-t1.fyers.in/",
}

# Per-request network retry (transient ConnectionError/Timeout only).
NET_MAX_RETRIES = 5
NET_RETRY_DELAY = 2

_REQUIRED_CREDS = ("client_id", "secret_key", "redirect_uri", "username", "pin", "totp_secret")


def _app_id_hash(client_id: str, secret_key: str) -> str:
    """SHA256("client_id:secret_key") — Fyers app authentication."""
    return sha256(f"{client_id}:{secret_key}".encode()).hexdigest()


def _post_with_retry(url: str, headers: dict, payload: dict, timeout: int = 10) -> dict:
    """POST with retry on transient network errors only (not API-level errors)."""
    last_exc = None
    for attempt in range(1, NET_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < NET_MAX_RETRIES:
                log.debug(f"Retry {attempt}/{NET_MAX_RETRIES} for {url}: {e}")
                time.sleep(NET_RETRY_DELAY)
            else:
                log.error(f"All {NET_MAX_RETRIES} attempts failed for {url}: {e}")
    raise last_exc


def refresh_access_token(refresh_token: str, client_id: str, secret_key: str, pin: str) -> dict:
    """PIN-only refresh (no TOTP). Returns {access_token, refresh_token} or {}."""
    try:
        payload = {
            "grant_type": "refresh_token",
            "appIdHash": _app_id_hash(client_id, secret_key),
            "refresh_token": refresh_token,
            "pin": str(pin),
        }
        data = _post_with_retry(
            "https://api-t1.fyers.in/api/v3/validate-refresh-token",
            headers=HEADERS, payload=payload,
        )
        if data.get("s") == "ok" and data.get("access_token"):
            log.info("Refresh token OK")
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
            }
    except requests.RequestException as e:
        log.debug(f"Refresh network error: {e}")
    except (KeyError, ValueError) as e:
        log.debug(f"Refresh parse error: {e}")
    return {}


def step1_send_login_otp(fy_id: str) -> str:
    """Step 1: 'I want to login' -> request_key."""
    try:
        d = _post_with_retry(
            "https://api-t2.fyers.in/vagator/v2/send_login_otp",
            headers=HEADERS, payload={"fy_id": fy_id, "app_id": "2"},
        )
        if d.get("s") == "ok":
            log.info("Step 1: send_login_otp OK")
            return d["request_key"]
        log.error(f"Step 1 FAILED: {d.get('message', d)}")
    except requests.RequestException as e:
        log.error(f"Step 1 network error: {e}")
    except (KeyError, ValueError) as e:
        log.error(f"Step 1 parse error: {e}")
    return ""


def step2_verify_totp(request_key: str, totp_secret: str) -> str:
    """Step 2: verify a generated TOTP code. OTP is sent as a STRING."""
    try:
        otp = pyotp.TOTP(totp_secret).now()
        d = _post_with_retry(
            "https://api-t2.fyers.in/vagator/v2/verify_otp",
            headers=HEADERS, payload={"request_key": request_key, "otp": otp},
        )
        if d.get("s") == "ok":
            log.info("Step 2: verify_totp OK")
            return d["request_key"]
        log.error(f"Step 2 FAILED: {d.get('message', d)}")
    except requests.RequestException as e:
        log.error(f"Step 2 network error: {e}")
    except (KeyError, ValueError) as e:
        log.error(f"Step 2 parse error: {e}")
    return ""


def step3_verify_pin(request_key: str, pin: str) -> str:
    """Step 3: verify PIN -> temporary access_token."""
    try:
        d = _post_with_retry(
            "https://api-t2.fyers.in/vagator/v2/verify_pin",
            headers=HEADERS,
            payload={"request_key": request_key, "identity_type": "pin",
                     "identifier": str(pin)},
        )
        if d.get("s") == "ok":
            log.info("Step 3: verify_pin OK")
            return d["data"]["access_token"]
        log.error(f"Step 3 FAILED: {d.get('message', d)}")
    except requests.RequestException as e:
        log.error(f"Step 3 network error: {e}")
    except (KeyError, ValueError) as e:
        log.error(f"Step 3 parse error: {e}")
    return ""


def step4_get_auth_code(temp_token: str, client_id: str, redirect_uri: str, fy_id: str) -> str:
    """Step 4: exchange temp token for an auth_code (parsed from the 'Url' field)."""
    try:
        app_id = client_id.split("-")[0]
        h = {**HEADERS, "Authorization": f"Bearer {temp_token}"}
        payload = {
            "fyers_id": fy_id,
            "app_id": app_id,
            "redirect_uri": redirect_uri,
            "appType": "100",
            "code_challenge": "",
            "state": "sample_state",
            "scope": "",
            "nonce": "",
            "response_type": "code",
            "create_cookie": True,
        }
        d = _post_with_retry(
            "https://api-t1.fyers.in/api/v3/token", headers=h, payload=payload,
        )
        if d.get("s") != "ok":
            log.error(f"Step 4 FAILED: {d}")
            return ""
        redirect_url = d.get("Url", "")
        auth_code = parse_qs(urlparse(redirect_url).query).get("auth_code", [""])[0]
        if not auth_code:
            log.error(f"Step 4 FAILED: auth_code missing in Url: {redirect_url!r}")
            return ""
        log.info("Step 4: get_auth_code OK")
        return auth_code
    except requests.RequestException as e:
        log.error(f"Step 4 network error: {e}")
    except (KeyError, ValueError) as e:
        log.error(f"Step 4 parse error: {e}")
    return ""


def step5_validate_auth_code(auth_code: str, client_id: str, secret_key: str,
                             redirect_uri: str) -> dict:
    """Step 5: exchange auth_code for the final {access_token, refresh_token}.

    Method A: Fyers SDK SessionModel.generate_token() (15s thread timeout).
    Method B (fallback): direct POST /api/v3/validate-authcode.
    """
    # ---- Method A: Fyers SDK (lazy import so this module loads without the SDK) ----
    try:
        from fyers_apiv3 import fyersModel
        session = fyersModel.SessionModel(
            client_id=client_id, secret_key=secret_key, redirect_uri=redirect_uri,
            response_type="code", grant_type="authorization_code",
        )
        session.set_token(auth_code)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(session.generate_token)
            resp = future.result(timeout=15)
        if resp.get("s") == "ok" or resp.get("code") == 200:
            log.info("Step 5: validate_auth_code OK (SDK)")
            return {
                "access_token": resp.get("access_token", ""),
                "refresh_token": resp.get("refresh_token", ""),
            }
    except FuturesTimeoutError:
        log.warning("Step 5 SDK timed out after 15s, trying direct API...")
    except Exception as e:
        log.debug(f"Step 5 SDK: {e}")

    # ---- Method B: direct API ----
    try:
        payload = {
            "grant_type": "authorization_code",
            "appIdHash": _app_id_hash(client_id, secret_key),
            "code": auth_code,
        }
        d = _post_with_retry(
            "https://api-t1.fyers.in/api/v3/validate-authcode",
            headers=HEADERS, payload=payload,
        )
        if d.get("s") == "ok":
            log.info("Step 5: validate_auth_code OK (direct API)")
            return {
                "access_token": d.get("access_token", ""),
                "refresh_token": d.get("refresh_token", ""),
            }
        log.error(f"Step 5 FAILED: {d}")
    except requests.RequestException as e:
        log.error(f"Step 5 network error: {e}")
    except (KeyError, ValueError) as e:
        log.error(f"Step 5 parse error: {e}")
    return {}


def full_totp_login(creds: dict) -> dict:
    """Run steps 1->5. Returns {access_token, refresh_token} or {} on any failure.

    Missing/empty credentials is a fast permanent failure (no HTTP calls).
    """
    missing = [k for k in _REQUIRED_CREDS if not creds.get(k)]
    if missing:
        log.error(f"Cannot auto-login, missing creds: {', '.join(missing)}")
        return {}

    log.info(f"Full TOTP login as {creds['username']}...")
    request_key = step1_send_login_otp(creds["username"])
    if not request_key:
        return {}
    request_key = step2_verify_totp(request_key, creds["totp_secret"])
    if not request_key:
        return {}
    temp_token = step3_verify_pin(request_key, creds["pin"])
    if not temp_token:
        return {}
    auth_code = step4_get_auth_code(
        temp_token, creds["client_id"], creds["redirect_uri"], creds["username"])
    if not auth_code:
        return {}
    return step5_validate_auth_code(
        auth_code, creds["client_id"], creds["secret_key"], creds["redirect_uri"])
