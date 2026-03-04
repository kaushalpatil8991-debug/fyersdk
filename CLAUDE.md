# Fyers Volume Spike Detector

## Project Overview
Dual volume spike detector for NSE stocks. Monitors Fyers WebSocket ticks and alerts on large trades via Telegram + Google Sheets.

- **Fyers detector**: Rs 3 Cr threshold, 722 large-cap symbols
- **Penny detector**: Rs 52 Lakh threshold, 220 small-cap symbols
- Both share a single Fyers access token (authenticated once)

## Architecture

```
main.py (FastAPI entry point)
  GET  /health            -> detector status
  GET  /auth/callback     -> receives auth code via browser redirect (local dev)
  POST /webhook/telegram  -> receives auth codes + commands from Telegram
  lifespan:
    startup: register webhook -> self-ping -> orchestrator.run()
    shutdown: stop detectors

Orchestrator (supervisor_service/ochestrator.py)
  -> FyersAuthenticator (shared token, webhook-based auth)
  -> FyersService (isolated fyers detector + controller)
  -> PennyService (isolated penny detector + controller)
  -> FyersSummaryService (isolated fyers summary generation)
  -> PennySummaryService (isolated penny summary generation)
  -> Market-hour scheduling (09:13 - 16:00 IST)
  -> Reactive auth refresh (only when Fyers rejects token)
```

## Directory Structure

```
├── main.py                          # FastAPI app entry point
├── shared/
│   ├── config_loader.py             # Env var loading -> AppConfig dataclass
│   ├── constants.py                 # Thresholds, timing, URLs
│   ├── logger.py                    # get_logger(name)
│   ├── exceptions.py                # Custom exceptions
│   └── models.py                    # Pydantic: TickData, TradeAlert, DetectorConfig
├── services/
│   ├── auth_service/
│   │   ├── authenticator.py         # Webhook-based Fyers auth flow
│   │   ├── token_manager.py         # Supabase CRUD for fyers_tokens table
│   │   ├── totp_handler.py          # pyotp wrapper
│   │   ├── server.py                # POST /webhook/telegram + GET /auth/callback
│   │   ├── models.py                # AuthState (asyncio.Event + pending code)
│   │   └── tools.py                 # register/delete Telegram webhook
│   ├── fyers_service/               # Isolated fyers detector + summary
│   │   ├── service.py               # FyersService (detector + controller lifecycle)
│   │   └── summary.py               # FyersSummaryService (summary generation)
│   ├── penny_service/               # Isolated penny detector + summary
│   │   ├── service.py               # PennyService (detector + controller lifecycle)
│   │   └── summary.py               # PennySummaryService (summary generation)
│   ├── detector_service/
│   │   ├── detector.py              # VolumeSpikeDetector (parameterized)
│   │   ├── tick_handler.py          # parse_tick(message)
│   │   ├── trade_analyzer.py        # analyze_trade() -> TradeAlert
│   │   └── websocket_manager.py     # Fyers data_ws wrapper
│   ├── supervisor_service/
│   │   ├── ochestrator.py           # Manages services + scheduling
│   │   ├── run_controller.py        # Thread mgmt for one detector
│   │   ├── schedular.py             # is_market_hours()
│   │   └── health_monitor.py        # Self-ping for Render keep-alive
│   ├── telegram_service/
│   │   ├── bot_handler.py           # TelegramSender (one per bot+chat pair)
│   │   ├── message_template.py      # Alert/auth message templates
│   │   └── command_parser.py        # extract_auth_code(), parse_command()
│   ├── summary_service/
│   │   ├── summary_generator.py     # SummaryGenerator (shared base class)
│   │   └── summary_scheduler.py     # Sends at 16:30 IST daily
│   ├── sheets_service/
│   │   ├── sheet_manager.py         # GoogleSheetsManager
│   │   └── row_builder.py           # TradeAlert -> SheetRow
│   └── sector_service/
│       ├── symbol_manager.py        # SymbolManager — Supabase CRUD for symbols + sectors
│       └── sector_mapper.py         # get_sector(symbol), initialized from Supabase
├── config/
│   ├── sectors.json                 # 890 symbol->sector mappings
│   └── symbols.json                 # {"fyers": [...], "penny": [...]}
├── tests/
│   ├── fyers.py                     # Original monolith (reference only)
│   ├── penny.py                     # Original monolith (reference only)
│   └── health.py                    # Original health server (reference only)
├── .env                             # All secrets (never commit)
├── render.yaml                      # Render deployment config
├── requirements.txt
└── Dockerfile
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `FYERS_CLIENT_ID` | Fyers API client ID |
| `FYERS_SECRET_KEY` | Fyers API secret |
| `FYERS_REDIRECT_URI` | OAuth redirect URI |
| `FYERS_TOTP_SECRET` | TOTP secret for 2FA |
| `FYERS_PIN` | Fyers account PIN |
| `LOGIN_BOT_TOKEN` | Login bot token |
| `LOGIN_CHAT_ID` | Login chat ID — auth URL, TOTP, success/failure, /hld /rst responses |
| `FYERS_TRADE_BOT_TOKEN` | Fyers trade bot token |
| `FYERS_TRADE_CHAT_ID` | Fyers trade alerts chat ID |
| `FYERS_SUMMARY_BOT_TOKEN` | Fyers summary bot token |
| `FYERS_SUMMARY_CHAT_ID` | Fyers daily summary chat ID |
| `PENNY_TRADE_BOT_TOKEN` | Penny trade bot token |
| `PENNY_TRADE_CHAT_ID` | Penny trade alerts chat ID |
| `PENNY_SUMMARY_BOT_TOKEN` | Penny summary bot token |
| `PENNY_SUMMARY_CHAT_ID` | Penny daily summary chat ID |
| `FYERS_GOOGLE_SHEETS_ID` | Fyers Google Sheet ID |
| `PENNY_GOOGLE_SHEETS_ID` | Penny Google Sheet ID |
| `GOOGLE_CREDENTIALS_JSON` | Google service account JSON blob |
| `SUPABASE_DSN` | PostgreSQL connection string |
| `PORT` | Server port (default 8000) |
| `SCHEDULING_ENABLED` | Enable market-hour scheduling (default true) |

## Supabase Database

All tables auto-created on startup. DSN: `postgresql://postgres:[PASSWORD]@db.zfapjzwjhitbkpsgkthy.supabase.co:5432/postgres`

**Table: `fyers_tokens`** (managed by TokenManager)
```sql
CREATE TABLE IF NOT EXISTS fyers_tokens (
    id          SERIAL PRIMARY KEY,
    access_token TEXT NOT NULL,
    timestamp   DOUBLE PRECISION NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fyers_tokens_id_desc ON fyers_tokens (id DESC);
```

**Table: `stock_symbols`** (managed by SymbolManager)
```sql
CREATE TABLE IF NOT EXISTS stock_symbols (
    id       SERIAL PRIMARY KEY,
    symbol   TEXT NOT NULL,
    detector TEXT NOT NULL,           -- 'fyers' or 'penny'
    active   BOOLEAN DEFAULT TRUE,
    UNIQUE(symbol, detector)
);
CREATE INDEX IF NOT EXISTS idx_stock_symbols_detector ON stock_symbols (detector, active);
```

**Table: `sector_mappings`** (managed by SymbolManager)
```sql
CREATE TABLE IF NOT EXISTS sector_mappings (
    id     SERIAL PRIMARY KEY,
    symbol TEXT UNIQUE NOT NULL,      -- 'NSE:TCS-EQ'
    sector TEXT NOT NULL              -- 'Information Technology'
);
CREATE INDEX IF NOT EXISTS idx_sector_mappings_symbol ON sector_mappings (symbol);
```

**Auto-seed:** On first run, if `stock_symbols` or `sector_mappings` are empty, `SymbolManager` seeds from `config/symbols.json` and `config/sectors.json`. After that, Supabase is the source of truth.

## Auth Flow (Webhook-based)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         STARTUP                                     │
├─────────────────────────────────────────────────────────────────────┤
│ 1. main.py calls register_telegram_webhook(login_bot_token)         │
│    → POST https://api.telegram.org/bot<TOKEN>/setWebhook            │
│    → webhook_url = https://fyers-volume-spike-detector.onrender.com │
│          /webhook/telegram                                          │
│                                                                     │
│ 2. main.py calls register_bot_commands(login_bot_token)             │
│    → Registers /hld and /rst in Telegram's command menu             │
│                                                                     │
│ 3. Orchestrator.run() starts                                        │
│    → Calls authenticator.authenticate()                             │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTHENTICATION                                   │
├─────────────────────────────────────────────────────────────────────┤
│ 4. Check Supabase for stored token                                  │
│    → SELECT * FROM fyers_tokens ORDER BY id DESC LIMIT 1            │
│    → If valid (< 24h old), verify with Fyers profile API            │
│    → If Fyers says OK → use token, send success msg → DONE          │
│                                                                     │
│ 5. No valid token → Fresh auth:                                     │
│    a. Create FyersSessionModel, generate auth URL                   │
│    b. Generate TOTP code via pyotp                                  │
│    c. Login bot sends to chat: auth URL + TOTP code                 │
│    d. auth_state.auth_event.clear() → wait for webhook              │
│                                                                     │
│ 6. User clicks URL in Telegram → browser opens Fyers login          │
│    → User enters TOTP → authorizes → redirected to redirect_uri     │
│    → Redirect URL contains ?auth_code=...                           │
│    → User pastes redirect URL back in the Telegram chat             │
│                                                                     │
│ 7. Telegram sends message to webhook:                               │
│    POST /webhook/telegram → server.py                               │
│    → extract_auth_code(text) parses auth_code from URL              │
│    → Sets auth_state.pending_auth_code = auth_code                  │
│    → Sets auth_state.auth_event (wakes up authenticator)            │
│                                                                     │
│ 8. Authenticator wakes up:                                          │
│    → session.set_token(auth_code)                                   │
│    → session.generate_token() → gets access_token from Fyers        │
│    → Saves token to Supabase: INSERT INTO fyers_tokens (...)        │
│    → Login bot sends success message                                │
│                                                                     │
│ 9. If no response in 5 min → resend auth URL + new TOTP             │
│    → Loop continues until auth_code is received                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DETECTORS START                                   │
├─────────────────────────────────────────────────────────────────────┤
│ 10. Orchestrator builds both detectors with shared access_token     │
│     → Fyers detector: 722 symbols, Rs 3Cr threshold                │
│     → Penny detector: 220 symbols, Rs 52L threshold                │
│     → Each gets its own trade_sender + summary_sender               │
│                                                                     │
│ 11. RunController.start() launches each detector in a thread        │
│     → WebSocket connects to Fyers data_ws                           │
│     → Tick callbacks fire on each price update                      │
│                                                                     │
│ 12. On trade alert: trade_sender.send(alert_message)                │
│     → Also logs to Google Sheets via sheets_manager                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    REACTIVE TOKEN REFRESH                            │
├─────────────────────────────────────────────────────────────────────┤
│ 13. No timer — detectors run until Fyers rejects the token          │
│     → Detector sets self.token_expired = True                       │
│     → Orchestrator loop checks _any_token_expired() every 5s        │
│     → On expiry: stop both → re-authenticate (step 4-8) → restart   │
└─────────────────────────────────────────────────────────────────────┘
```

## Telegram Commands

Commands are received via the login bot's webhook at `POST /webhook/telegram`.

### `/hld [reason]` — Hold
Stops all detectors and enters hold mode. Use when market is closed (holidays, weekends, etc).

```
User sends:    /hld Holi holiday
Webhook:       server.py → parse_command() → _handle_hold()
Orchestrator:  hold() → stops both RunControllers, sets on_hold=True
Response:      Login bot sends hold confirmation message
Loop:          Orchestrator skips detector start while on_hold=True
Health API:    GET /health returns {"status": "on_hold", ...}
```

### `/rst` — Restart
Stops detectors, re-authenticates with Fyers, and restarts everything.

```
User sends:    /rst
Webhook:       server.py → parse_command() → _handle_restart()
Orchestrator:  request_restart() → sets restart_requested=True, on_hold=False
Response:      Login bot sends restart confirmation message
Loop:          Orchestrator detects restart_requested → stops detectors →
               re-authenticates (full auth flow) → restarts detectors
```

### `/snd` — Send Summary Now
Immediately generates and sends today's summaries for both fyers and penny.

```
User sends:    /snd
Webhook:       server.py → _handle_send_summary()
Response:      Login bot: "Generating summaries..."
               Fyers summary bot: daily summary (+ 3-day/weekly based on day)
               Penny summary bot: daily summary (+ 3-day/weekly based on day)
```

### `/sdt` — Send Summary for Date
Two-step command: first prompts for a date, then generates summaries for that date.

```
User sends:    /sdt
Response:      Login bot: "Send the date (DD-MM-YYYY):"

User sends:    28-02-2026
Webhook:       server.py → _handle_summary_date_input()
               parse_date("28-02-2026") → checks day of week (Saturday)
Response:      Login bot: "Generating summaries for 28-02-2026 (Saturday)..."
               Fyers + Penny summary bots: summaries for that date

User sends:    abc (invalid)
Response:      Login bot: "Invalid date format. Use DD-MM-YYYY"
               (stays in date-waiting mode, user can retry)
```

### Command Registration
- `register_bot_commands()` in `auth_service/tools.py` auto-registers `/hld`, `/rst`, `/snd`, `/sdt` with Telegram Bot API on startup
- Optional: manually register via BotFather → select login bot → Edit Commands:
  ```
  hld - Hold — stop all detectors (market closed/holiday)
  rst - Restart — re-authenticate and restart all detectors
  snd - Send today's summary now
  sdt - Send summary for a specific date
  ```

## 5 Separate Telegram Bots

Each channel has its own bot_token + chat_id pair (`TelegramChannel` dataclass). The `TelegramSender` class wraps one channel — just call `sender.send(text)`.

| Bot | Env Vars | What gets sent there |
|-----|----------|---------------------|
| Login | `LOGIN_BOT_TOKEN` + `LOGIN_CHAT_ID` | Auth URL + TOTP, success/failure, /hld /rst responses |
| Fyers Trade | `FYERS_TRADE_BOT_TOKEN` + `FYERS_TRADE_CHAT_ID` | Real-time fyers trade alerts (Rs 3Cr+) |
| Fyers Summary | `FYERS_SUMMARY_BOT_TOKEN` + `FYERS_SUMMARY_CHAT_ID` | Fyers end-of-day summary |
| Penny Trade | `PENNY_TRADE_BOT_TOKEN` + `PENNY_TRADE_CHAT_ID` | Real-time penny trade alerts (Rs 52L+) |
| Penny Summary | `PENNY_SUMMARY_BOT_TOKEN` + `PENNY_SUMMARY_CHAT_ID` | Penny end-of-day summary |

## Summary Service (16:30 IST Daily)

Reads trade data from Google Sheets, aggregates by symbol, sends top-15 ranking to summary bots.

```
At 16:30 IST (SummaryScheduler checks every 30s):
  │
  ├── Fyers SummaryGenerator:
  │     → Reads fyers Google Sheet (all today's rows)
  │     → Aggregates: count trades per symbol + total value in Cr
  │     → Sorts by count, takes top 15
  │     → Sends formatted HTML message via fyers_summary_sender
  │
  └── Penny SummaryGenerator:
        → Same logic, reads penny Google Sheet
        → Sends via penny_summary_sender
```

**Day-specific messages:**
- **Every day**: Daily summary (today's data)
- **Wednesday + Friday**: Daily + 3-day summary (last 3 days)
- **Friday**: Daily + 3-day + weekly summary (last 5 days)

**Column matching:** Finds `Date`, `Symbol`, and `Trd_Val_Cr` columns by header name (case-insensitive). Supports multiple date formats (DD-MM-YYYY, YYYY-MM-DD, etc).

**Independence:** Runs as a separate async task — not affected by `/hld` or detector state. Always sends if there's data in the sheet.

## Key Design Decisions

- **Shared token**: One FyersAuthenticator instance, token passed to both services
- **Isolated services**: `FyersService` and `PennyService` each own their detector, controller, sheets, and senders — no shared state
- **Isolated summaries**: `FyersSummaryService` and `PennySummaryService` each own their `SummaryGenerator` — no shared state
- **Webhook not polling**: No `getUpdates` loop, Telegram pushes to `/webhook/telegram`
- **Local dev auth**: `GET /auth/callback?auth_code=...` endpoint for browser-based auth without webhook
- **Supabase not local JSON**: Token persists across deploys, INSERT-based audit trail
- **Parameterized detector**: Single `VolumeSpikeDetector` class serves both fyers/penny via `DetectorConfig`
- **No user ID restriction**: Any user in the Telegram group can send auth codes
- **Reactive token refresh**: No timer/polling — detectors run until Fyers rejects the token, then orchestrator re-authenticates and restarts
- **Telegram commands**: `/hld` stops everything (holidays), `/rst` re-authenticates and restarts
- **IST everywhere**: All timestamps use `datetime.now(IST)` — works correctly regardless of server timezone

## Deployment

- Hosted on **Render.com** at `https://fyers-volume-spike-detector.onrender.com`
- Self-ping every 7 min to prevent Render free-tier sleep
- `render.yaml`: Python 3.11.7, `TZ=Asia/Kolkata`, port 10000
- `Dockerfile` also available (python:3.12-slim, runs `python main.py`)

## Changelog

### 2026-03-04 - Initial modular architecture
- Merged `tests/fyers.py` (1721 lines), `tests/penny.py` (1552 lines), `tests/health.py` (57 lines) into modular service architecture
- Switched Telegram auth from polling to webhook (`/webhook/telegram`)
- Replaced local `fyers_access_token.json` with Supabase `fyers_tokens` table
- Moved all hardcoded credentials to environment variables
- Deduplicated detector code into single parameterized `VolumeSpikeDetector`
- Added dedicated `LOGIN_CHAT_ID` (5th channel) for auth link/TOTP/notifications
- Extracted 890 sector mappings to `config/sectors.json`, symbols to `config/symbols.json`

### 2026-03-04 - Reactive token refresh (no polling)
- Removed proactive hourly `AUTH_CHECK_INTERVAL` timer entirely
- Removed `AUTH_CHECK_INTERVAL` constant from `shared/constants.py`
- Token refresh is now **reactive**: only triggered when Fyers actually rejects a request
- Detector sets `self.token_expired = True` on rejection
- Orchestrator checks `_any_token_expired()` each loop iteration
- On expiry: stops both detectors, re-authenticates, updates tokens, detectors auto-restart
- No unnecessary downtime — detectors run uninterrupted until token actually fails

### 2026-03-04 - Telegram commands /hld and /rst
- Added `/hld [reason]` command: stops all detectors, enters hold mode (for holidays/weekends)
- Added `/rst` command: stops detectors, re-authenticates, restarts everything
- Updated webhook handler (`auth_service/server.py`) to parse commands via `command_parser.py`
- Webhook handler now receives orchestrator reference for command execution
- Added `hold()` and `request_restart()` methods to `Orchestrator`
- Orchestrator loop respects `on_hold` flag — skips detector start while held
- Added `register_bot_commands()` in `auth_service/tools.py` — auto-registers commands with Telegram on startup
- Health endpoint shows `"status": "on_hold"` when held
- All command responses sent to `LOGIN_CHAT_ID`

### 2026-03-04 - 5 separate Telegram bots
- Restructured from 1 shared bot token + 5 chat IDs to **5 separate bot+chat pairs**
- New `TelegramChannel` dataclass in `config_loader.py` holds `bot_token` + `chat_id` per channel
- `TelegramConfig` now has 5 `TelegramChannel` fields: login, fyers_trade, fyers_summary, penny_trade, penny_summary
- Renamed `TelegramBot` to `TelegramSender` — one instance per channel, chat_id baked in, call `send(text)` directly
- Orchestrator creates 5 `TelegramSender` instances and passes relevant ones to authenticator/detectors
- Removed `trade_chat_id` / `summary_chat_id` from `DetectorConfig` — senders passed directly to detector constructor
- `.env` restructured: 10 telegram vars (5 `*_BOT_TOKEN` + 5 `*_CHAT_ID`)
- Webhook + commands registered only on login bot

### 2026-03-04 - Summary service
- Merged `tests/fyerssum.py` and `tests/pennysum.py` into `services/summary_service/`
- `SummaryGenerator`: parameterized class — reads Google Sheet, aggregates trades by symbol, formats top-15 HTML message
- `SummaryScheduler`: async loop, triggers at 16:30 IST daily (once per day guard)
- Day-specific logic: Daily always, +3-day on Wed/Fri, +weekly on Fri
- Started as `asyncio.create_task()` in orchestrator — runs independently of detectors/hold
- Added `SUMMARY_SEND_TIME` constant to `shared/constants.py`

### 2026-03-04 - /snd and /sdt commands
- `/snd` — immediately sends today's summaries for both fyers and penny (day-appropriate: daily/3-day/weekly)
- `/sdt` — two-step: prompts for date, then generates summaries for that date with day-of-week logic
- Added `parse_date()` to `command_parser.py` — supports DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD
- Added `generate_messages_for_date()` and `send_summary_for_date()` to `SummaryGenerator`
- Refactored `_get_records()` and `_format_message()` to accept optional `target_date` parameter
- Webhook uses `_pending_summary_date` flag to track /sdt two-step state
- Orchestrator exposes `self.summary_generators` list (created in `__init__`, not `run()`)
- Added message templates: `summary_generating_message`, `summary_date_prompt_message`, `summary_date_invalid_message`, `summary_date_confirm_message`
- Registered `/snd` and `/sdt` in bot commands via `tools.py`

### 2026-03-04 - Isolated fyers/penny services
- Created `services/fyers_service/` — `FyersService` (detector lifecycle) + `FyersSummaryService` (summary generation)
- Created `services/penny_service/` — `PennyService` (detector lifecycle) + `PennySummaryService` (summary generation)
- Each service owns its own detector, controller, sheets manager, and telegram senders — fully isolated, no shared state
- Orchestrator simplified: `self.fyers`, `self.penny`, `self.fyers_summary`, `self.penny_summary` replace raw controllers/generators
- Removed `_build_detector()`, `_fyers_det`, `_penny_det`, `fyers_controller`, `penny_controller`, `summary_generators` from orchestrator
- `/snd` and `/sdt` commands now call `fyers_summary.send_today()` + `penny_summary.send_today()` separately
- Added `GET /auth/callback` endpoint for local dev authentication (browser redirect with auth_code)
- Fixed all `datetime.now()` calls to use `datetime.now(IST)` — trade alerts, health check, token timestamps
- Suppressed uvicorn "Invalid HTTP request received" warnings from bot scanners

### 2026-03-04 - Symbols & sectors moved to Supabase
- Created `services/sector_service/symbol_manager.py` — `SymbolManager` class with Supabase CRUD
- Two new tables: `stock_symbols` (symbol + detector + active flag) and `sector_mappings` (symbol → sector)
- Auto-seeds from `config/symbols.json` and `config/sectors.json` on first run (empty tables)
- After seed, Supabase is the source of truth — JSON files kept as backup
- Updated `sector_mapper.py` — no longer loads from JSON at import, initialized via `init_sector_mapping()` from Supabase data
- Updated `ochestrator.py` — replaced `_load_symbols_and_sectors()` JSON reads with `SymbolManager` queries
- Symbols can now be added/deactivated via Supabase dashboard without redeploying
