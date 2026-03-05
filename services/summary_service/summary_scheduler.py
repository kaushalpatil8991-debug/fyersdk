"""Scheduler that triggers summary generation at 16:30 IST."""
import asyncio
from datetime import datetime
from shared.logger import get_logger
from shared.constants import IST, SUMMARY_SEND_TIME
from .summary_generator import SummaryGenerator

log = get_logger("summary_scheduler")


class SummaryScheduler:
    """Runs summary generators at the configured time (16:30 IST)."""

    def __init__(self, generators: list[SummaryGenerator]):
        self.generators = generators
        self._last_sent_date: str | None = None

    async def run(self):
        """Main loop — checks time every 30s, sends once per day at SUMMARY_SEND_TIME."""
        log.info(f"Summary scheduler started (send time: {SUMMARY_SEND_TIME} IST)")

        while True:
            try:
                now = datetime.now(IST)
                current_date = now.strftime("%d-%m-%Y")
                current_time = now.strftime("%H:%M")

                # Reset for new day
                if self._last_sent_date and self._last_sent_date != current_date:
                    self._last_sent_date = None

                # Send at configured time, once per day
                if current_time == SUMMARY_SEND_TIME and self._last_sent_date != current_date:
                    log.info(f"Triggering summaries at {current_time}")
                    for gen in self.generators:
                        await gen.send_summary()
                    self._last_sent_date = current_date

                await asyncio.sleep(30)

            except Exception as e:
                log.error(f"Summary scheduler error: {e}")
                await asyncio.sleep(60)
