"""Market-hours scheduling."""
from datetime import datetime
from shared.constants import MARKET_START_TIME, MARKET_END_TIME, IST
from shared.logger import get_logger

log = get_logger("scheduler")


def is_market_hours() -> bool:
    """Check if current IST time is within configured market hours."""
    now = datetime.now(IST)
    current = now.strftime("%H:%M")
    return MARKET_START_TIME <= current < MARKET_END_TIME
