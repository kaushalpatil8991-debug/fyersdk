"""Load and validate all configuration from environment variables."""
import os
import json
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class FyersConfig:
    client_id: str
    secret_key: str
    redirect_uri: str
    totp_secret: str
    pin: str


@dataclass
class TelegramChannel:
    """A single Telegram bot+chat pair."""
    bot_token: str
    chat_id: str


@dataclass
class TelegramConfig:
    login: TelegramChannel
    fyers_trade: TelegramChannel
    fyers_summary: TelegramChannel
    penny_trade: TelegramChannel
    penny_summary: TelegramChannel


@dataclass
class GoogleConfig:
    credentials: dict | None
    fyers_sheet_id: str
    penny_sheet_id: str


@dataclass
class SupabaseConfig:
    dsn: str


@dataclass
class AppConfig:
    fyers: FyersConfig
    telegram: TelegramConfig
    google: GoogleConfig
    supabase: SupabaseConfig
    scheduling_enabled: bool = True
    port: int = 8000


def _channel(token_env: str, chat_id_env: str) -> TelegramChannel:
    return TelegramChannel(
        bot_token=os.environ[token_env],
        chat_id=os.environ[chat_id_env],
    )


def load_config() -> AppConfig:
    """Build AppConfig from environment variables."""
    google_creds = _load_google_credentials()

    return AppConfig(
        fyers=FyersConfig(
            client_id=os.environ["FYERS_CLIENT_ID"],
            secret_key=os.environ["FYERS_SECRET_KEY"],
            redirect_uri=os.environ["FYERS_REDIRECT_URI"],
            totp_secret=os.environ["FYERS_TOTP_SECRET"],
            pin=os.environ["FYERS_PIN"],
        ),
        telegram=TelegramConfig(
            login=_channel("LOGIN_BOT_TOKEN", "LOGIN_CHAT_ID"),
            fyers_trade=_channel("FYERS_TRADE_BOT_TOKEN", "FYERS_TRADE_CHAT_ID"),
            fyers_summary=_channel("FYERS_SUMMARY_BOT_TOKEN", "FYERS_SUMMARY_CHAT_ID"),
            penny_trade=_channel("PENNY_TRADE_BOT_TOKEN", "PENNY_TRADE_CHAT_ID"),
            penny_summary=_channel("PENNY_SUMMARY_BOT_TOKEN", "PENNY_SUMMARY_CHAT_ID"),
        ),
        google=GoogleConfig(
            credentials=google_creds,
            fyers_sheet_id=os.environ.get("FYERS_GOOGLE_SHEETS_ID", ""),
            penny_sheet_id=os.environ.get("PENNY_GOOGLE_SHEETS_ID", ""),
        ),
        supabase=SupabaseConfig(
            dsn=os.environ["SUPABASE_DSN"],
        ),
        scheduling_enabled=os.getenv("SCHEDULING_ENABLED", "true").lower() == "true",
        port=int(os.getenv("PORT", "8000")),
    )


def _load_google_credentials() -> dict | None:
    """Load Google service account credentials from env."""
    blob = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if blob:
        creds = json.loads(blob)
        if "private_key" in creds:
            creds["private_key"] = creds["private_key"].replace("\\n", "\n")
        return creds

    pk = os.getenv("GOOGLE_PRIVATE_KEY", "")
    if pk:
        pk = pk.replace("\\n", "\n")

    creds = {
        "type": "service_account",
        "project_id": os.getenv("GOOGLE_PROJECT_ID", ""),
        "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID", ""),
        "private_key": pk,
        "client_email": os.getenv("GOOGLE_CLIENT_EMAIL", ""),
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("GOOGLE_CLIENT_X509_CERT_URL", ""),
        "universe_domain": "googleapis.com",
    }
    required = ["project_id", "private_key_id", "private_key", "client_email"]
    if any(not creds.get(f) for f in required):
        return None
    return creds
