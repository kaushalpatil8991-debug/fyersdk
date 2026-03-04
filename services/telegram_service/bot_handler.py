"""Telegram bot for sending messages."""
import asyncio
import requests
from shared.logger import get_logger
from shared.config_loader import TelegramChannel

log = get_logger("telegram")


class TelegramSender:
    """Send messages to a specific Telegram channel (bot_token + chat_id pair)."""

    def __init__(self, channel: TelegramChannel):
        self.bot_token = channel.bot_token
        self.chat_id = channel.chat_id
        self.base_url = f"https://api.telegram.org/bot{channel.bot_token}"

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to this channel."""
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                data={"chat_id": self.chat_id, "text": text,
                      "parse_mode": parse_mode},
                timeout=10
            )
            return resp.status_code == 200
        except Exception as e:
            log.error(f"Send failed to {self.chat_id}: {e}")
            return False

    async def send_async(self, text: str, parse_mode: str = "HTML") -> bool:
        """Async wrapper (runs sync send in thread)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send, text, parse_mode)
