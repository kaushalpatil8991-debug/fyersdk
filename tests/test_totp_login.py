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
