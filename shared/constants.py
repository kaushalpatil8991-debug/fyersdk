"""Application-wide constants."""
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Market schedule
MARKET_START_TIME = "09:13"
MARKET_END_TIME = "16:00"

# Detection thresholds
FYERS_TRADE_THRESHOLD = 30_000_000    # Rs 3 crore
PENNY_TRADE_THRESHOLD = 5_500_000     # Rs 52 lakh
MIN_VOLUME_SPIKE = 1000

# Timing
TELEGRAM_AUTH_TIMEOUT = 300
LOGIN_URL_RETRY_INTERVAL = 300        # 5 min
TOKEN_VALIDITY_SECONDS = 28800        # 8 hours

# Webhook
WEBHOOK_PATH = "/webhook/telegram"
BASE_URL = "https://fyers-volume-spike-detector.onrender.com"
HEALTH_CHECK_URL = f"{BASE_URL}/health"
SELF_PING_INTERVAL = 420              # 7 min
SUMMARY_SEND_TIME = "16:30"           # 4:30 PM IST

# Google Sheets headers
SHEET_HEADERS = ['Date', 'Time', 'Symbol', 'LTP', 'Volume_Spike',
                 'Trd_Val_Cr', 'Spike_Type', 'Sector']
