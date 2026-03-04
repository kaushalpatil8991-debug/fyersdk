"""Orchestrates both detectors with market-hour scheduling."""
import asyncio
from shared.logger import get_logger
from shared.config_loader import AppConfig
from services.auth_service.authenticator import FyersAuthenticator
from services.auth_service.token_manager import TokenManager
from services.auth_service.models import AuthState
from services.telegram_service import TelegramSender
from services.summary_service import SummaryScheduler
from services.sector_service import SymbolManager, init_sector_mapping
from services.fyers_service import FyersService, FyersSummaryService
from services.penny_service import PennyService, PennySummaryService
from .schedular import is_market_hours

log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, config: AppConfig, auth_state: AuthState):
        self.config = config
        self.auth_state = auth_state

        # Create 5 TelegramSender instances (one per bot+chat pair)
        self.login_sender = TelegramSender(config.telegram.login)
        self.fyers_trade_sender = TelegramSender(config.telegram.fyers_trade)
        self.fyers_summary_sender = TelegramSender(config.telegram.fyers_summary)
        self.penny_trade_sender = TelegramSender(config.telegram.penny_trade)
        self.penny_summary_sender = TelegramSender(config.telegram.penny_summary)

        self.token_manager = TokenManager(config)
        self.authenticator = FyersAuthenticator(
            config, self.token_manager,
            self.login_sender,
            auth_state,
        )

        # Isolated services — each manages its own detector + controller
        self.fyers = FyersService(
            config.fyers.client_id, config.google.fyers_sheet_id,
            config.google.credentials,
            self.fyers_trade_sender, self.fyers_summary_sender,
        )
        self.penny = PennyService(
            config.fyers.client_id, config.google.penny_sheet_id,
            config.google.credentials,
            self.penny_trade_sender, self.penny_summary_sender,
        )

        self.on_hold = False
        self.restart_requested = False

        # Isolated summary services
        self.fyers_summary = FyersSummaryService(
            config.google.credentials,
            config.google.fyers_sheet_id, self.fyers_summary_sender,
        )
        self.penny_summary = PennySummaryService(
            config.google.credentials,
            config.google.penny_sheet_id, self.penny_summary_sender,
        )

        # Symbol/sector manager (Supabase, auto-seeds from JSON on first run)
        self.symbol_manager = SymbolManager(config.supabase.dsn)

    def hold(self):
        """Stop all detectors and enter hold mode."""
        self.on_hold = True
        self.fyers.stop()
        self.penny.stop()
        log.info("Detectors on hold")

    def request_restart(self):
        """Signal the orchestrator loop to restart and re-authenticate."""
        self.restart_requested = True
        self.on_hold = False
        log.info("Restart requested")

    def _any_token_expired(self) -> bool:
        return self.fyers.token_expired or self.penny.token_expired

    async def _re_authenticate(self):
        """Stop detectors, get fresh token, update both services."""
        log.info("Token rejected by Fyers — re-authenticating...")
        self.fyers.stop()
        self.penny.stop()

        await self.authenticator.authenticate()

        token = self.authenticator.access_token
        self.fyers.update_token(token)
        self.penny.update_token(token)

    async def run(self):
        """Main supervisor loop."""
        await self.authenticator.authenticate()

        # Start summary scheduler (runs independently at 16:30 IST)
        generators = [self.fyers_summary.generator, self.penny_summary.generator]
        summary_scheduler = SummaryScheduler(generators)
        asyncio.create_task(summary_scheduler.run())

        # Load symbols and sectors from Supabase
        fyers_symbols = self.symbol_manager.load_symbols("fyers")
        penny_symbols = self.symbol_manager.load_symbols("penny")
        sectors = self.symbol_manager.load_sector_mapping()
        init_sector_mapping(sectors)

        # Build each service with its own symbols
        token = self.authenticator.access_token
        self.fyers.build(token, fyers_symbols, sectors)
        self.penny.build(token, penny_symbols, sectors)

        while True:
            try:
                if self.restart_requested:
                    self.restart_requested = False
                    self.fyers.stop()
                    self.penny.stop()
                    await self._re_authenticate()

                if self.on_hold:
                    await asyncio.sleep(5)
                    continue

                if self.config.scheduling_enabled and not is_market_hours():
                    if self.fyers.is_running or self.penny.is_running:
                        log.info("Outside market hours, stopping detectors")
                        self.fyers.stop()
                        self.penny.stop()
                    await asyncio.sleep(60)
                    continue

                if self._any_token_expired():
                    await self._re_authenticate()

                self.fyers.start()
                self.penny.start()

                await asyncio.sleep(5)

            except Exception as e:
                log.error(f"Supervisor error: {e}")
                await asyncio.sleep(10)
