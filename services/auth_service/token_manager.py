"""Token persistence via Supabase (PostgreSQL)."""
import time
import psycopg2
from datetime import datetime
from shared.logger import get_logger
from shared.config_loader import AppConfig
from shared.constants import TOKEN_VALIDITY_SECONDS, IST

log = get_logger("token_manager")


class TokenManager:
    def __init__(self, config: AppConfig):
        self.dsn = config.supabase.dsn
        self._ensure_table()

    def _get_conn(self):
        return psycopg2.connect(self.dsn)

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

    def is_token_valid_by_time(self) -> tuple[bool, str]:
        """Check token validity based on timestamp."""
        token, ts, _, _ = self.load_token()
        if not token:
            return False, "No token available"
        if time.time() - ts < TOKEN_VALIDITY_SECONDS:
            return True, "Token is valid"
        return False, "Token expired"
