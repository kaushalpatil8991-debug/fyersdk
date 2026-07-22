# Automated Fyers Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual Telegram-link Fyers login with the headless 5-step TOTP flow (cached → PIN-only refresh → full TOTP, 5 retries × 60s), keeping the manual link as a last-resort fallback and breaking no other flow.

**Architecture:** Port REFERENCE's automated TOTP HTTP flow into a new dependency-light module `services/auth_service/totp_login.py` (parameterized by explicit credentials, no globals/DB). Rewire `FyersAuthenticator.authenticate()` into a 4-strategy ladder that offloads blocking work via `asyncio.to_thread`. Persist the new `refresh_token` in the existing `fyers_tokens` table. The authenticator's public interface is unchanged, so the orchestrator, reactive re-auth, detectors, summaries, and webhooks are untouched.

**Tech Stack:** Python 3.11 (Render) / 3.14 (local), `requests`, `pyotp`, `fyers-apiv3` (lazy import), `psycopg2`, FastAPI, `pytest` (dev).

## Global Constraints

- Strategy order (full parity): cached token → PIN-only refresh → full 5-step TOTP → manual Telegram-link fallback.
- Automated TOTP is bounded to **5 attempts**, **60 seconds** between attempts (`AUTO_LOGIN_MAX_ATTEMPTS = 5`, `AUTO_LOGIN_RETRY_DELAY = 60`).
- The automated flow must not block the FastAPI event loop: run blocking login work via `await asyncio.to_thread(...)`; slice the 60s wait into 1s chunks that poll `should_cancel()` so `/hld` and `/rst` stay responsive.
- `FYERS_USERNAME` (fy_id) is **optional** in config (`os.getenv("FYERS_USERNAME", "")`); an empty value must make the automated path fail cleanly and fall through to the manual link — never crash at startup.
- `fyers_apiv3` is unavailable locally (Python 3.14). New pure-logic module must import `fyers_apiv3` **lazily** (only inside step 5). Files that transitively import `fyers_apiv3` are verified with `python -m py_compile`, not by import.
- Preserve the authenticator's public interface exactly: `access_token`, `is_authenticated`, `fyers_model`, `authenticate() -> bool`, `check_token_with_fyers()`, `cancel_auth()`, `reset_cancel()`.
- All test runs use the venv interpreter from the repo root so `import`/file paths resolve:
  `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -v`

## File Structure

- **Create** `services/auth_service/totp_login.py` — headless 5-step TOTP flow + refresh + bounded retry. Pure logic; `fyers_apiv3` lazy-imported in step 5 only.
- **Create** `tests/test_totp_login.py` — unit tests, module loaded via importlib file-load (bypasses the package `__init__` that needs `fyers_apiv3`).
- **Modify** `shared/constants.py` — add `AUTO_LOGIN_MAX_ATTEMPTS`, `AUTO_LOGIN_RETRY_DELAY`.
- **Modify** `shared/config_loader.py` — add `username` to `FyersConfig`; read `FYERS_USERNAME`.
- **Modify** `services/auth_service/token_manager.py` — `refresh_token` column; `save_token`/`load_token` carry it (`load_token` → 4-tuple).
- **Modify** `services/auth_service/authenticator.py` — 4-strategy ladder; extract existing manual flow into `_manual_login()`.
- **Modify** `.env` — add `FYERS_USERNAME=` placeholder.
- **Modify** `CLAUDE.md` — changelog entry + env-var table row.

---

### Task 1: `totp_login.py` — single-attempt automated flow

**Files:**
- Create: `services/auth_service/totp_login.py`
- Test: `tests/test_totp_login.py`

**Interfaces:**
- Consumes: nothing (standalone; `requests`, `pyotp`, stdlib).
- Produces:
  - `_app_id_hash(client_id: str, secret_key: str) -> str`
  - `_post_with_retry(url: str, headers: dict, payload: dict, timeout: int = 10) -> dict`
  - `refresh_access_token(refresh_token: str, client_id: str, secret_key: str, pin: str) -> dict` → `{"access_token","refresh_token"}` or `{}`
  - `step1_send_login_otp(fy_id: str) -> str`
  - `step2_verify_totp(request_key: str, totp_secret: str) -> str`
  - `step3_verify_pin(request_key: str, pin: str) -> str`
  - `step4_get_auth_code(temp_token: str, client_id: str, redirect_uri: str, fy_id: str) -> str`
  - `step5_validate_auth_code(auth_code: str, client_id: str, secret_key: str, redirect_uri: str) -> dict`
  - `full_totp_login(creds: dict) -> dict` — `creds` keys: `client_id, secret_key, redirect_uri, username, pin, totp_secret`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_totp_login.py`:

```python
"""Unit tests for the headless TOTP login module.

Loaded via importlib file-load so the package __init__ (which imports the
authenticator -> fyers_apiv3, unavailable on Python 3.14) is bypassed. Only
requests + pyotp are needed; step5's fyers_apiv3 import is lazy and mocked.
"""
import importlib.util
import pathlib
import pytest

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "services" / "auth_service" / "totp_login.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("totp_login_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


totp = _load()


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_post(payload):
    """Return a requests.post replacement that always yields `payload`."""
    def _post(url, json=None, headers=None, timeout=None):
        return _FakeResp(payload)
    return _post


# ---- _app_id_hash ----

def test_app_id_hash_is_sha256_of_id_colon_secret():
    from hashlib import sha256
    expected = sha256(b"CID-100:SEC").hexdigest()
    assert totp._app_id_hash("CID-100", "SEC") == expected


# ---- _post_with_retry retries on network errors ----

def test_post_with_retry_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise totp.requests.ConnectionError("boom")
        return _FakeResp({"s": "ok"})

    monkeypatch.setattr(totp.time, "sleep", lambda s: None)
    monkeypatch.setattr(totp.requests, "post", flaky)
    assert totp._post_with_retry("u", {}, {}) == {"s": "ok"}
    assert calls["n"] == 3


# ---- step1 ----

def test_step1_returns_request_key_on_ok(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "ok", "request_key": "RK1"}))
    assert totp.step1_send_login_otp("FY123") == "RK1"


def test_step1_returns_empty_on_error(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "error", "message": "bad id"}))
    assert totp.step1_send_login_otp("FY123") == ""


# ---- step2 (OTP sent as string, preserves leading zeros) ----

def test_step2_sends_otp_as_string_and_returns_key(monkeypatch):
    captured = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured.update(json)
        return _FakeResp({"s": "ok", "request_key": "RK2"})

    class _FakeTOTP:
        def __init__(self, secret):
            pass

        def now(self):
            return "063265"

    monkeypatch.setattr(totp.requests, "post", _post)
    monkeypatch.setattr(totp.pyotp, "TOTP", _FakeTOTP)
    assert totp.step2_verify_totp("RK1", "SECRET") == "RK2"
    assert captured["otp"] == "063265"
    assert isinstance(captured["otp"], str)


# ---- step3 ----

def test_step3_returns_temp_token(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "ok", "data": {"access_token": "TEMP"}}))
    assert totp.step3_verify_pin("RK2", "1234") == "TEMP"


# ---- step4 (auth_code extracted from the capital-U "Url" query string) ----

def test_step4_extracts_auth_code_from_url(monkeypatch):
    url = "https://redir.example?s=ok&code=200&auth_code=ACODE123&state=x"
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "ok", "Url": url}))
    assert totp.step4_get_auth_code("TEMP", "CID-100", "https://redir.example", "FY123") == "ACODE123"


def test_step4_returns_empty_when_auth_code_missing(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "ok", "Url": "https://redir.example?s=ok"}))
    assert totp.step4_get_auth_code("TEMP", "CID-100", "https://redir.example", "FY123") == ""


# ---- refresh_access_token ----

def test_refresh_returns_tokens_on_ok(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "ok", "access_token": "AT", "refresh_token": "RT2"}))
    assert totp.refresh_access_token("RT1", "CID-100", "SEC", "1234") == {
        "access_token": "AT", "refresh_token": "RT2"}


def test_refresh_keeps_old_refresh_when_none_returned(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "ok", "access_token": "AT"}))
    assert totp.refresh_access_token("RT1", "CID-100", "SEC", "1234")["refresh_token"] == "RT1"


def test_refresh_returns_empty_on_error(monkeypatch):
    monkeypatch.setattr(totp.requests, "post",
                        _fake_post({"s": "error"}))
    assert totp.refresh_access_token("RT1", "CID-100", "SEC", "1234") == {}


# ---- full_totp_login (missing creds is a fast permanent failure) ----

def test_full_totp_login_empty_when_username_missing():
    creds = {"client_id": "CID-100", "secret_key": "SEC", "redirect_uri": "r",
             "username": "", "pin": "1234", "totp_secret": "S"}
    assert totp.full_totp_login(creds) == {}


def test_full_totp_login_runs_all_steps(monkeypatch):
    monkeypatch.setattr(totp, "step1_send_login_otp", lambda fy: "RK1")
    monkeypatch.setattr(totp, "step2_verify_totp", lambda rk, s: "RK2")
    monkeypatch.setattr(totp, "step3_verify_pin", lambda rk, p: "TEMP")
    monkeypatch.setattr(totp, "step4_get_auth_code", lambda t, c, r, fy: "ACODE")
    monkeypatch.setattr(totp, "step5_validate_auth_code",
                        lambda ac, c, s, r: {"access_token": "AT", "refresh_token": "RT"})
    creds = {"client_id": "CID-100", "secret_key": "SEC", "redirect_uri": "r",
             "username": "FY123", "pin": "1234", "totp_secret": "S"}
    assert totp.full_totp_login(creds) == {"access_token": "AT", "refresh_token": "RT"}


def test_full_totp_login_aborts_on_step2_failure(monkeypatch):
    monkeypatch.setattr(totp, "step1_send_login_otp", lambda fy: "RK1")
    monkeypatch.setattr(totp, "step2_verify_totp", lambda rk, s: "")  # fail
    creds = {"client_id": "CID-100", "secret_key": "SEC", "redirect_uri": "r",
             "username": "FY123", "pin": "1234", "totp_secret": "S"}
    assert totp.full_totp_login(creds) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -v`
Expected: FAIL/ERROR — `totp_login.py` does not exist yet (import/collection error).

- [ ] **Step 3: Write the module**

Create `services/auth_service/totp_login.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -v`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
git add services/auth_service/totp_login.py tests/test_totp_login.py
git commit -m "feat(auth): headless 5-step Fyers TOTP login module"
```

---

### Task 2: `full_totp_login_with_retry` — bounded, cancellable retry

**Files:**
- Modify: `services/auth_service/totp_login.py`
- Test: `tests/test_totp_login.py`

**Interfaces:**
- Consumes: `full_totp_login(creds) -> dict` (Task 1).
- Produces: `full_totp_login_with_retry(creds: dict, max_attempts: int = 5, delay: int = 60, should_cancel: Optional[Callable[[], bool]] = None) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_totp_login.py`:

```python
# ---- full_totp_login_with_retry ----

def test_retry_succeeds_on_third_attempt(monkeypatch):
    calls = {"n": 0}

    def _login(creds):
        calls["n"] += 1
        return {"access_token": "AT"} if calls["n"] == 3 else {}

    monkeypatch.setattr(totp, "full_totp_login", _login)
    monkeypatch.setattr(totp.time, "sleep", lambda s: None)
    creds = {"username": "FY123"}
    out = totp.full_totp_login_with_retry(creds, max_attempts=5, delay=60)
    assert out == {"access_token": "AT"}
    assert calls["n"] == 3


def test_retry_gives_up_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def _login(creds):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(totp, "full_totp_login", _login)
    monkeypatch.setattr(totp.time, "sleep", lambda s: None)
    out = totp.full_totp_login_with_retry({"username": "FY"}, max_attempts=5, delay=60)
    assert out == {}
    assert calls["n"] == 5


def test_retry_stops_when_cancelled_before_first_attempt(monkeypatch):
    calls = {"n": 0}

    def _login(creds):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(totp, "full_totp_login", _login)
    monkeypatch.setattr(totp.time, "sleep", lambda s: None)
    out = totp.full_totp_login_with_retry(
        {"username": "FY"}, max_attempts=5, delay=60, should_cancel=lambda: True)
    assert out == {}
    assert calls["n"] == 0


def test_retry_waits_between_attempts_in_cancellable_slices(monkeypatch):
    """delay is consumed as 1s sleeps so cancellation is responsive."""
    sleeps = {"n": 0}
    monkeypatch.setattr(totp, "full_totp_login", lambda creds: {})
    monkeypatch.setattr(totp.time, "sleep", lambda s: sleeps.__setitem__("n", sleeps["n"] + 1))
    totp.full_totp_login_with_retry({"username": "FY"}, max_attempts=2, delay=3)
    # one 3-slice wait between the 2 attempts (no wait after the last)
    assert sleeps["n"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -k retry -v`
Expected: FAIL — `full_totp_login_with_retry` not defined (AttributeError).

- [ ] **Step 3: Add the retry wrapper**

Append to `services/auth_service/totp_login.py`:

```python
def full_totp_login_with_retry(creds: dict, max_attempts: int = 5, delay: int = 60,
                               should_cancel: Optional[Callable[[], bool]] = None) -> dict:
    """Re-run full_totp_login up to max_attempts, sleeping `delay`s between tries.

    should_cancel() is polled before each attempt and during the inter-attempt
    wait (in 1s slices) so /hld and /rst interrupt promptly. Returns {} if
    cancelled or exhausted.
    """
    max_attempts = max(1, int(max_attempts))
    for attempt in range(1, max_attempts + 1):
        if should_cancel and should_cancel():
            log.info("Auto-login cancelled before attempt %d", attempt)
            return {}
        tokens = full_totp_login(creds)
        if tokens.get("access_token"):
            if attempt > 1:
                log.info(f"Auto-login succeeded on attempt {attempt}/{max_attempts}")
            return tokens
        if attempt < max_attempts:
            log.warning(
                f"Auto-login attempt {attempt}/{max_attempts} failed; retrying in {delay}s")
            for _ in range(int(delay)):
                if should_cancel and should_cancel():
                    log.info("Auto-login cancelled during retry wait")
                    return {}
                time.sleep(1)
    log.error(f"Auto-login failed after {max_attempts} attempt(s)")
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -v`
Expected: PASS (all tests, including the earlier ones).

- [ ] **Step 5: Commit**

```bash
git add services/auth_service/totp_login.py tests/test_totp_login.py
git commit -m "feat(auth): bounded cancellable retry for TOTP login"
```

---

### Task 3: Config wiring — constants, `FyersConfig.username`, `.env`

**Files:**
- Modify: `shared/constants.py`
- Modify: `shared/config_loader.py:11-16` (FyersConfig), `:78-84` (loader)
- Modify: `.env`
- Test: `tests/test_totp_login.py` (constants smoke) — see step 1

**Interfaces:**
- Produces: `shared.constants.AUTO_LOGIN_MAX_ATTEMPTS = 5`, `shared.constants.AUTO_LOGIN_RETRY_DELAY = 60`; `FyersConfig.username: str`.

- [ ] **Step 1: Write the failing test (constants exist and hold the required values)**

Append to `tests/test_totp_login.py`:

```python
def test_auto_login_constants():
    from shared import constants
    assert constants.AUTO_LOGIN_MAX_ATTEMPTS == 5
    assert constants.AUTO_LOGIN_RETRY_DELAY == 60
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py::test_auto_login_constants -v`
Expected: FAIL — `AttributeError: module 'shared.constants' has no attribute 'AUTO_LOGIN_MAX_ATTEMPTS'`.

- [ ] **Step 3a: Add constants**

In `shared/constants.py`, under the `# Timing` block (after `TOKEN_VALIDITY_SECONDS`), add:

```python
# Automated login (headless TOTP) retry policy
AUTO_LOGIN_MAX_ATTEMPTS = 5           # attempts before manual-link fallback
AUTO_LOGIN_RETRY_DELAY = 60           # seconds between attempts
```

- [ ] **Step 3b: Add `username` to `FyersConfig`**

In `shared/config_loader.py`, change the `FyersConfig` dataclass:

```python
@dataclass
class FyersConfig:
    client_id: str
    secret_key: str
    redirect_uri: str
    totp_secret: str
    pin: str
    username: str = ""   # Fyers login id (fy_id) for headless TOTP login
```

- [ ] **Step 3c: Read `FYERS_USERNAME` in the loader**

In `shared/config_loader.py`, in `load_config()`'s `FyersConfig(...)` construction, add the `username` line:

```python
        fyers=FyersConfig(
            client_id=os.environ["FYERS_CLIENT_ID"],
            secret_key=os.environ["FYERS_SECRET_KEY"],
            redirect_uri=os.environ["FYERS_REDIRECT_URI"],
            totp_secret=os.environ["FYERS_TOTP_SECRET"],
            pin=os.environ["FYERS_PIN"],
            username=os.getenv("FYERS_USERNAME", ""),
        ),
```

- [ ] **Step 3d: Add the `.env` placeholder**

In `.env`, add a line (near the other `FYERS_*` entries):

```
FYERS_USERNAME=
```

- [ ] **Step 4: Run tests + syntax-verify the edited config file**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -v`
Expected: PASS (including `test_auto_login_constants`).

Run: `.venv/Scripts/python.exe -m py_compile shared/config_loader.py shared/constants.py`
Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add shared/constants.py shared/config_loader.py .env
git commit -m "feat(config): FYERS_USERNAME + auto-login retry constants"
```

---

### Task 4: `TokenManager` — persist `refresh_token`

**Files:**
- Modify: `services/auth_service/token_manager.py`

**Interfaces:**
- Produces:
  - `save_token(access_token: str, ts: float | None = None, created_at: str | None = None, refresh_token: str | None = None)`
  - `load_token() -> tuple[str | None, float, str, str | None]` (token, timestamp, created_at, **refresh_token**)

**Note:** Cannot import-test locally — `services.auth_service` `__init__` imports the authenticator (`fyers_apiv3`, unavailable on 3.14) and DB methods need Supabase. Verify with `py_compile`; behavior is verified on deployment (Task 7).

- [ ] **Step 1: Update `_ensure_table` (create + migrate column)**

In `services/auth_service/token_manager.py` `_ensure_table()`, replace the `CREATE TABLE` block and add a backfill `ALTER`:

```python
    def _ensure_table(self):
        """Create token table if not exists; add refresh_token if missing."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS fyers_tokens (
                        id SERIAL PRIMARY KEY,
                        access_token TEXT NOT NULL,
                        timestamp DOUBLE PRECISION NOT NULL,
                        created_at TEXT NOT NULL,
                        refresh_token TEXT
                    )
                """)
                cur.execute(
                    "ALTER TABLE fyers_tokens "
                    "ADD COLUMN IF NOT EXISTS refresh_token TEXT"
                )
            conn.commit()
        log.info("Supabase token table ready")
```

- [ ] **Step 2: Update `load_token` to return the refresh token (4-tuple)**

```python
    def load_token(self) -> tuple[str | None, float, str, str | None]:
        """Load latest token. Returns (token, timestamp, created_at, refresh_token)."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT access_token, timestamp, created_at, refresh_token "
                    "FROM fyers_tokens ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
        if row:
            log.info("Token loaded from Supabase")
            return row[0], row[1], row[2], row[3]
        return None, 0.0, "", None
```

- [ ] **Step 3: Update `save_token` to persist the refresh token**

```python
    def save_token(self, access_token: str, ts: float | None = None,
                   created_at: str | None = None, refresh_token: str | None = None):
        """Insert new token (with optional refresh_token) into Supabase."""
        ts = ts or time.time()
        created_at = created_at or datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO fyers_tokens "
                    "(access_token, timestamp, created_at, refresh_token) "
                    "VALUES (%s, %s, %s, %s)",
                    (access_token, ts, created_at, refresh_token)
                )
            conn.commit()
        log.info("Token saved to Supabase")
```

- [ ] **Step 4: Update the internal caller `is_token_valid_by_time` (4-tuple unpack)**

```python
    def is_token_valid_by_time(self) -> tuple[bool, str]:
        """Check token validity based on timestamp."""
        token, ts, _, _ = self.load_token()
        if not token:
            return False, "No token available"
        if time.time() - ts < TOKEN_VALIDITY_SECONDS:
            return True, "Token is valid"
        return False, "Token expired"
```

- [ ] **Step 5: Syntax-verify**

Run: `.venv/Scripts/python.exe -m py_compile services/auth_service/token_manager.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add services/auth_service/token_manager.py
git commit -m "feat(auth): persist refresh_token in fyers_tokens"
```

---

### Task 5: `FyersAuthenticator` — 4-strategy ladder

**Files:**
- Modify: `services/auth_service/authenticator.py`

**Interfaces:**
- Consumes: `totp_login.refresh_access_token`, `totp_login.full_totp_login_with_retry` (Tasks 1–2); `TokenManager.load_token` 4-tuple, `save_token(refresh_token=...)` (Task 4); `AUTO_LOGIN_MAX_ATTEMPTS`, `AUTO_LOGIN_RETRY_DELAY` (Task 3).
- Produces: unchanged public interface (`authenticate() -> bool`, etc.).

**Note:** Imports `fyers_apiv3` (unavailable on 3.14). Verify with `py_compile`; behavior verified on deployment (Task 7).

- [ ] **Step 1: Update imports**

At the top of `services/auth_service/authenticator.py`, add after the existing imports:

```python
from . import totp_login
from shared.constants import AUTO_LOGIN_MAX_ATTEMPTS, AUTO_LOGIN_RETRY_DELAY
```

- [ ] **Step 2: Replace `authenticate()` with the strategy ladder**

Replace the entire `authenticate()` method (lines 58–129) with:

```python
    async def authenticate(self) -> bool:
        """Auth ladder: cached -> refresh -> automated TOTP -> manual link."""
        # Strategy 1: cached token (time-valid AND live-verified)
        valid, _ = self.token_manager.is_token_valid_by_time()
        if valid:
            token, _, _, _ = self.token_manager.load_token()
            self.access_token = token
            ok, fmsg = self.check_token_with_fyers()
            if ok:
                log.info("Using existing valid token from Supabase")
                self.is_authenticated = True
                await self.login_sender.send_async(auth_success_message())
                return True
            log.warning(f"Stored token invalid from Fyers: {fmsg}")
        if self._cancel_event.is_set():
            return False

        # Strategy 2: PIN-only refresh
        _, _, _, refresh_token = self.token_manager.load_token()
        if refresh_token:
            log.info("Attempting PIN-only token refresh...")
            tokens = await asyncio.to_thread(
                totp_login.refresh_access_token,
                refresh_token, self.cfg.client_id, self.cfg.secret_key, self.cfg.pin,
            )
            if tokens.get("access_token") and await self._apply_tokens(tokens):
                return True
        if self._cancel_event.is_set():
            return False

        # Strategy 3: full automated TOTP (bounded, cancellable)
        log.info("Starting automated TOTP login...")
        tokens = await asyncio.to_thread(
            totp_login.full_totp_login_with_retry,
            self._creds(), AUTO_LOGIN_MAX_ATTEMPTS, AUTO_LOGIN_RETRY_DELAY,
            lambda: self._cancel_event.is_set(),
        )
        if tokens.get("access_token") and await self._apply_tokens(tokens):
            return True
        if self._cancel_event.is_set():
            return False

        # Strategy 4: manual Telegram-link fallback
        log.warning("Automated login failed — falling back to manual auth link")
        return await self._manual_login()

    def _creds(self) -> dict:
        """Credential bundle for the headless TOTP flow."""
        return {
            "client_id": self.cfg.client_id,
            "secret_key": self.cfg.secret_key,
            "redirect_uri": self.cfg.redirect_uri,
            "username": self.cfg.username,
            "pin": self.cfg.pin,
            "totp_secret": self.cfg.totp_secret,
        }

    async def _apply_tokens(self, tokens: dict) -> bool:
        """Adopt a fresh {access_token, refresh_token}: verify live, persist, notify."""
        self.access_token = tokens.get("access_token")
        ok, fmsg = self.check_token_with_fyers()  # sets self.fyers_model on success
        if not ok:
            log.warning(f"New token failed live check: {fmsg}")
            return False
        self.token_manager.save_token(
            self.access_token, refresh_token=tokens.get("refresh_token"))
        self.is_authenticated = True
        log.info("Authentication successful (automated)!")
        await self.login_sender.send_async(auth_success_message())
        return True

    async def _manual_login(self) -> bool:
        """Existing webhook flow: send auth URL + TOTP, wait for auth_code."""
        self.auth_state.auth_event.clear()
        self.auth_state.pending_auth_code = None

        session = self._create_session()
        await self._send_auth_msg(session)

        while True:
            try:
                await asyncio.wait_for(
                    self.auth_state.auth_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                if self._cancel_event.is_set():
                    log.info("Auth cancelled during timeout")
                    return False
                log.info("5 min elapsed, resending auth URL with fresh TOTP...")
                await self._send_auth_msg(session)
                self.auth_state.auth_event.clear()
                continue
            if self._cancel_event.is_set():
                log.info("Auth cancelled")
                return False
            break

        auth_code = self.auth_state.pending_auth_code
        if not auth_code:
            error_msg = "No auth code received"
            await self.login_sender.send_async(auth_failure_message(error_msg))
            raise AuthenticationError(error_msg)

        log.info(f"Auth code received ({auth_code[:20]}...), exchanging for token...")
        session.set_token(auth_code)
        response = session.generate_token()

        if response and response.get("s") == "ok":
            self.access_token = response["access_token"]
            self.token_manager.save_token(self.access_token)
            self.fyers_model = fyersModel.FyersModel(
                client_id=self.cfg.client_id,
                token=self.access_token, log_path="")
            self.is_authenticated = True
            log.info("Authentication successful!")
            await self.login_sender.send_async(auth_success_message())
            return True

        error_msg = f"Token generation failed: {response}"
        await self.login_sender.send_async(auth_failure_message(error_msg))
        raise AuthenticationError(error_msg)
```

(`_create_session` and `_send_auth_msg` remain unchanged below.)

- [ ] **Step 3: Syntax-verify**

Run: `.venv/Scripts/python.exe -m py_compile services/auth_service/authenticator.py`
Expected: no output, exit 0.

- [ ] **Step 4: Full test suite still green**

Run: `.venv/Scripts/python.exe -m pytest tests/test_totp_login.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/auth_service/authenticator.py
git commit -m "feat(auth): automated login ladder with manual-link fallback"
```

---

### Task 6: Docs — CLAUDE.md changelog + env table

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the env-var row**

In `CLAUDE.md`'s Environment Variables table, add a row after `FYERS_PIN`:

```
| `FYERS_USERNAME` | Fyers login id (fy_id, e.g. XK00893) for headless TOTP login |
```

- [ ] **Step 2: Add a changelog entry**

At the top of the `## Changelog` section, add:

```markdown
### 2026-07-23 - Automated headless login (REFERENCE parity)
- New `services/auth_service/totp_login.py`: headless 5-step Fyers TOTP flow
  (send_login_otp → verify_otp → verify_pin → /api/v3/token → validate-authcode),
  plus PIN-only `refresh_access_token` and bounded, cancellable
  `full_totp_login_with_retry` (5 attempts, 60s apart). `fyers_apiv3` lazy-imported.
- `FyersAuthenticator.authenticate()` is now a ladder: cached token → PIN-only
  refresh → automated TOTP → manual Telegram-link fallback. Blocking login runs
  via `asyncio.to_thread`; `/hld` and `/rst` interrupt mid-login.
- `fyers_tokens` gains a nullable `refresh_token` column (auto-migrated).
- New env var `FYERS_USERNAME` (fy_id); new constants `AUTO_LOGIN_MAX_ATTEMPTS=5`,
  `AUTO_LOGIN_RETRY_DELAY=60`.
- No change to detectors, summaries, commands, scheduling, or the webhook path.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document automated login flow and FYERS_USERNAME"
```

---

### Task 7: Deployment verification (manual)

**Not automated** — the full app needs `fyers_apiv3` + Supabase, available only on Render. After merge and after the user sets a real `FYERS_USERNAME` in the Render env:

- [ ] Confirm startup logs show the token table ready (with `refresh_token` migrated) and no crash when `FYERS_USERNAME` was previously empty.
- [ ] At/after 09:13 IST (or via `/rst`), confirm the login bot receives the automated success message — with **no** auth-URL/TOTP link sent (automated path won).
- [ ] Confirm `fyers_tokens` has a fresh row with a non-null `refresh_token`.
- [ ] Next day (or after invalidating the access token): confirm the PIN-only refresh path is used (log `Refresh token OK`) without a full TOTP.
- [ ] Force an automated failure (temporarily wrong PIN): confirm 5 retries at 60s, then the manual auth-link fallback message arrives; `/hld` during the retry loop interrupts promptly.
- [ ] Regression: `/hld`, `/rst`, `/snd`, `/sdt`, detector alerts, and 16:30 summaries all still work.

---

## Self-Review

**1. Spec coverage:**
- Headless 5-step flow → Task 1. ✅
- Refresh strategy → Task 1 (`refresh_access_token`), wired in Task 5. ✅
- 60s / 5-attempt bounded retry → Task 2 + constants Task 3. ✅
- Manual fallback → Task 5 (`_manual_login`). ✅
- `refresh_token` column → Task 4. ✅
- `FYERS_USERNAME` optional config + `.env` → Task 3. ✅
- Async safety (`to_thread`, sliced cancellable wait) → Task 2 (slices) + Task 5 (`to_thread`). ✅
- Untouched flows → confirmed in Task 7 regression + no edits to detector/summary/server/main. ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step has complete code. ✅

**3. Type consistency:** `load_token()` returns a 4-tuple everywhere it's consumed (Task 4 definition; Task 4 `is_token_valid_by_time`; Task 5 two unpack sites). `save_token(refresh_token=...)` keyword matches Task 4 signature. `full_totp_login_with_retry(creds, max_attempts, delay, should_cancel)` positional order matches the Task 5 call site. `_creds()` keys match `_REQUIRED_CREDS` in Task 1. ✅
```
