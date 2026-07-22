# Automated Fyers Login (REFERENCE parity, manual fallback)

**Date:** 2026-07-23
**Status:** Approved (design)

## Goal

Replace the current **manual** Fyers login (send auth URL + TOTP to Telegram →
user clicks link → enters TOTP in browser → redirect `auth_code` returns via
webhook) with the **fully automated** 5-step TOTP flow used in the `REFERENCE/`
project, so the daily login at 09:13 IST needs no human interaction.

Constraints from the user:
1. Replicate the REFERENCE automated login flow.
2. Break no other flow — only the login becomes automated.
3. 60 seconds between login retries.
4. **Full parity** strategy order: cached token → PIN-only refresh → full TOTP.
5. Automated login is **bounded to 5 attempts** (60s apart); if all fail, fall
   back to the existing manual Telegram auth-link flow.
6. `FYERS_USERNAME` (fy_id) will be filled in by the user; add the placeholder.

## Approach

Adapt the REFERENCE 5-step TOTP logic **into the existing `FyersAuthenticator`**,
keeping its public interface unchanged:

- `access_token: str | None`
- `is_authenticated: bool`
- `fyers_model`
- `authenticate() -> bool`
- `check_token_with_fyers() -> tuple[bool, str]`
- `cancel_auth()` / `reset_cancel()`

Only the *internals* of `authenticate()` change. The orchestrator, reactive
re-auth, `/hld`, `/rst`, detectors, summaries, and webhooks are untouched.

We do **not** port REFERENCE's DB layer (psycopg3 + dict cursors), pydantic
settings, or Postgres advisory locks. They don't fit this codebase and aren't
needed: `authenticate()` is only ever called from the single, serialized
orchestrator loop, so there is no concurrent-login hazard. Token persistence
stays in `TokenManager` (psycopg2).

## Components

### 1. New module: `services/auth_service/totp_login.py`

A self-contained port of REFERENCE `auth/auth.py`'s HTTP flow, parameterized by a
credentials dict (no globals, no DB — it performs HTTP and returns tokens):

- `_app_id_hash(client_id, secret_key) -> str` — `SHA256("client_id:secret_key")`
- `_post_with_retry(url, headers, payload, timeout=10) -> dict` — network-level
  retry (5 attempts, 2s apart) on `ConnectionError`/`Timeout` only.
- `refresh_access_token(refresh_token, client_id, secret_key, pin) -> dict` —
  PIN-only refresh via `/api/v3/validate-refresh-token`. Returns
  `{access_token, refresh_token}` or `{}`.
- `step1_send_login_otp(fy_id) -> str` (request_key)
- `step2_verify_totp(request_key, totp_secret) -> str` (new request_key; OTP sent
  as a **string** to preserve leading zeros)
- `step3_verify_pin(request_key, pin) -> str` (temp access_token)
- `step4_get_auth_code(temp_token, client_id, redirect_uri, fy_id) -> str`
  (extracts `auth_code` from the `Url` field; `app_id = client_id.split("-")[0]`)
- `step5_validate_auth_code(auth_code, client_id, secret_key, redirect_uri) -> dict`
  — SDK `SessionModel.generate_token()` with a 15s thread timeout, falling back to
  the direct `/api/v3/validate-authcode` call. Returns `{access_token, refresh_token}`.
- `full_totp_login(creds: dict) -> dict` — runs steps 1→5; `{}` on any failure.
- `full_totp_login_with_retry(creds, max_attempts=5, delay=60, should_cancel=None) -> dict`
  — re-runs `full_totp_login` up to `max_attempts`, sleeping `delay` seconds
  between attempts. `should_cancel` is a zero-arg callable polled before each
  attempt and during the sleep (in ~1s slices) so `/hld` and `/rst` interrupt
  promptly. Returns `{}` if cancelled or exhausted.

Uses the same Chrome-mimicking `HEADERS` (Origin/Referer/User-Agent) REFERENCE
uses — Fyers' vagator endpoints reject requests without them.

`creds` shape:
```python
{
  "client_id": cfg.client_id,      # "EH8TE9J6PZ-100"
  "secret_key": cfg.secret_key,
  "redirect_uri": cfg.redirect_uri,
  "username": cfg.username,        # fy_id, e.g. "XK00893"
  "pin": cfg.pin,
  "totp_secret": cfg.totp_secret,
}
```

### 2. `FyersAuthenticator.authenticate()` — new strategy ladder

Async method; blocking work offloaded via `asyncio.to_thread`.

1. **Cached token** — `token_manager.is_token_valid_by_time()` AND
   `check_token_with_fyers()` pass → use it. *(existing)*
2. **Refresh token** — if `load_token()` returns a `refresh_token`, call
   `refresh_access_token(...)` in a thread → on success `save_token(access,
   refresh)`, set model, verify live via `check_token_with_fyers()` → use.
   On failure, fall through. *(new)*
3. **Full TOTP (automated)** — `full_totp_login_with_retry(creds, max_attempts=5,
   delay=60, should_cancel=lambda: self._cancel_event.is_set())` in a thread → on
   success `save_token(access, refresh)`, build model, mark authenticated, send
   `auth_success_message()`. *(new)*
4. **Manual fallback** — if steps 2–3 yield no token (and not cancelled), run the
   **existing** manual flow verbatim: `_create_session()` → `_send_auth_msg()` →
   wait on `auth_state.auth_event` (5-min resend loop) → exchange `auth_code`.
   *(existing, now a fallback)*

Cancellation (`_cancel_event`) is honored at every stage: between strategies,
inside the retry loop, and in the manual wait — so `/hld` / `/rst` return `False`
promptly and the orchestrator loops back.

### 3. `TokenManager` + schema

- `CREATE TABLE` gains `refresh_token TEXT`; add
  `ALTER TABLE fyers_tokens ADD COLUMN IF NOT EXISTS refresh_token TEXT` in
  `_ensure_table()` for existing deployments (safe, backward-compatible).
- `save_token(access_token, ts=None, created_at=None, refresh_token=None)` —
  INSERT includes `refresh_token`.
- `load_token() -> tuple[token, timestamp, created_at, refresh_token]` — 4-tuple;
  update the two in-repo callers (`is_token_valid_by_time`, `authenticator`).
- INSERT-based audit trail preserved (no move to UPDATE).

### 4. Config / constants / .env

- `FyersConfig` gains `username: str`.
- `load_config()` reads `FYERS_USERNAME` with `os.getenv("FYERS_USERNAME", "")`
  (**optional** — empty fy_id makes the automated path fail cleanly and fall
  through to the manual link; nothing crashes at startup).
- `shared/constants.py`: `AUTO_LOGIN_MAX_ATTEMPTS = 5`, `AUTO_LOGIN_RETRY_DELAY = 60`.
- `.env`: add `FYERS_USERNAME=` placeholder.

### 5. Async safety

The automated flow uses synchronous `requests` and `time.sleep(60)`. Running it on
the FastAPI event loop would freeze `/health`, webhooks, and self-ping. Therefore:

- `refresh_access_token` and `full_totp_login_with_retry` are invoked with
  `await asyncio.to_thread(...)`.
- The 60s inter-attempt wait is sliced into ~1s `time.sleep` chunks that poll
  `should_cancel()`, so cancellation is responsive despite running in a thread.

## Data flow (daily 09:13)

```
orchestrator loop → market hours open (09:13) → authenticate()
  ├─ cached token valid+live?           → use  (0 API calls)
  ├─ refresh_token present?             → refresh (1 call, PIN only) → use
  ├─ full TOTP, ≤5 tries × 60s          → steps 1-5 → use
  └─ all automated failed?              → manual Telegram link (webhook auth_code)
→ token set → orchestrator builds/starts detectors (unchanged)
```

Reactive re-auth (`_re_authenticate`) calls the same `authenticate()`, so it is
automated too, with the manual link as its last resort.

## Error handling

- Each step logs a clear success/failure; non-network Fyers errors are permanent
  for that attempt (the flow-level retry re-runs the whole sequence with a fresh
  TOTP).
- Missing credentials (including empty `username`) → automated path returns `{}`
  fast → manual fallback.
- `save_token` failures propagate as today (DB errors surface in logs).

## Testing

- Unit: `totp_login` step parsers/`_app_id_hash`/`auth_code` extraction with
  mocked `requests` responses; `full_totp_login_with_retry` honoring
  `max_attempts`, `delay`, and `should_cancel` (monkeypatched `sleep`).
- Integration (manual, with real fy_id in `.env`): confirm cached → refresh →
  TOTP ladder and that `/hld` interrupts an in-progress automated login.
- Regression: `/hld`, `/rst`, `/snd`, `/sdt`, detectors, summaries, and the manual
  webhook fallback still function.

## Out of scope

- No change to market-hour scheduling (login still triggers at 09:13 via
  `is_market_hours()` opening; no separate APScheduler job added).
- No change to detector/summary/sheets/MCP logic.
- No migration of the token table to REFERENCE's `client_id`-keyed UPDATE schema.
```
