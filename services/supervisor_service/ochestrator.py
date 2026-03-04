"""Orchestrates both detectors with market-hour scheduling."""
import asyncio
from shared.logger import get_logger
from shared.config_loader import AppConfig
from shared.models import DetectorConfig
from shared.constants import FYERS_TRADE_THRESHOLD, PENNY_TRADE_THRESHOLD
from services.auth_service.authenticator import FyersAuthenticator
from services.auth_service.token_manager import TokenManager
from services.auth_service.models import AuthState
from services.detector_service.detector import VolumeSpikeDetector
from services.sheets_service import GoogleSheetsManager
from services.telegram_service import TelegramSender
from services.summary_service import SummaryGenerator, SummaryScheduler
from services.sector_service import SymbolManager, init_sector_mapping
from .run_controller import RunController
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
        self.fyers_controller: RunController | None = None
        self.penny_controller: RunController | None = None
        self._fyers_det: VolumeSpikeDetector | None = None
        self._penny_det: VolumeSpikeDetector | None = None
        self.on_hold = False          # True when /hld is active
        self.restart_requested = False  # True when /rst is received

        # Summary generators (available for /snd and /sdt commands)
        self.summary_generators = [
            SummaryGenerator("fyers", config.google.credentials,
                             config.google.fyers_sheet_id, self.fyers_summary_sender),
            SummaryGenerator("penny", config.google.credentials,
                             config.google.penny_sheet_id, self.penny_summary_sender),
        ]

        # Symbol/sector manager (Supabase, auto-seeds from JSON on first run)
        self.symbol_manager = SymbolManager(config.supabase.dsn)

    def _build_detector(self, name: str, threshold: int, sheet_id: str,
                        trade_sender: TelegramSender,
                        summary_sender: TelegramSender,
                        symbol_list: list, sector_map: dict
                        ) -> VolumeSpikeDetector:
        dc = DetectorConfig(
            name=name, threshold=threshold,
            google_sheet_id=sheet_id,
            symbols=symbol_list,
            sector_mapping=sector_map,
        )
        sheets = GoogleSheetsManager(
            self.config.google.credentials, sheet_id
        )
        return VolumeSpikeDetector(
            dc, self.authenticator.access_token,
            self.config.fyers.client_id,
            sheets, trade_sender, summary_sender,
        )

    def hold(self):
        """Stop all detectors and enter hold mode."""
        self.on_hold = True
        if self.fyers_controller and self.fyers_controller.is_running:
            self.fyers_controller.stop()
        if self.penny_controller and self.penny_controller.is_running:
            self.penny_controller.stop()
        log.info("Detectors on hold")

    def request_restart(self):
        """Signal the orchestrator loop to restart and re-authenticate."""
        self.restart_requested = True
        self.on_hold = False
        log.info("Restart requested")

    def _any_token_expired(self) -> bool:
        """Check if either detector flagged a token expiry."""
        if self._fyers_det and self._fyers_det.token_expired:
            return True
        if self._penny_det and self._penny_det.token_expired:
            return True
        return False

    async def _re_authenticate(self):
        """Stop detectors, get fresh token, update detector tokens."""
        log.info("Token rejected by Fyers — re-authenticating...")
        self.fyers_controller.stop()
        self.penny_controller.stop()

        await self.authenticator.authenticate()

        # Update tokens so detectors use fresh token on restart
        self._fyers_det.access_token = self.authenticator.access_token
        self._fyers_det.token_expired = False
        self._penny_det.access_token = self.authenticator.access_token
        self._penny_det.token_expired = False
        # Detectors restart automatically via is_running check in loop

    async def run(self):
        """Main supervisor loop."""
        # Authenticate once (shared token)
        await self.authenticator.authenticate()

        # Start summary scheduler (runs independently at 16:30 IST)
        summary_scheduler = SummaryScheduler(self.summary_generators)
        asyncio.create_task(summary_scheduler.run())

        # Load symbols and sectors from Supabase
        fyers_symbols = self.symbol_manager.load_symbols("fyers")
        penny_symbols = self.symbol_manager.load_symbols("penny")
        sectors = self.symbol_manager.load_sector_mapping()
        init_sector_mapping(sectors)

        cfg = self.config

        self._fyers_det = self._build_detector(
            "fyers", FYERS_TRADE_THRESHOLD,
            cfg.google.fyers_sheet_id,
            self.fyers_trade_sender,
            self.fyers_summary_sender,
            fyers_symbols, sectors,
        )
        self._penny_det = self._build_detector(
            "penny", PENNY_TRADE_THRESHOLD,
            cfg.google.penny_sheet_id,
            self.penny_trade_sender,
            self.penny_summary_sender,
            penny_symbols, sectors,
        )

        self.fyers_controller = RunController(self._fyers_det)
        self.penny_controller = RunController(self._penny_det)

        while True:
            try:
                # /rst command: stop, re-auth, restart
                if self.restart_requested:
                    self.restart_requested = False
                    if self.fyers_controller.is_running:
                        self.fyers_controller.stop()
                    if self.penny_controller.is_running:
                        self.penny_controller.stop()
                    await self._re_authenticate()
                    # fall through to start detectors below

                # /hld command: stay stopped
                if self.on_hold:
                    await asyncio.sleep(5)
                    continue

                in_market = is_market_hours()

                if self.config.scheduling_enabled and not in_market:
                    if self.fyers_controller.is_running:
                        log.info("Outside market hours, stopping detectors")
                        self.fyers_controller.stop()
                        self.penny_controller.stop()
                    await asyncio.sleep(60)
                    continue

                # Re-auth only when Fyers actually rejects the token
                if self._any_token_expired():
                    await self._re_authenticate()

                # Start both if not running
                if not self.fyers_controller.is_running:
                    self.fyers_controller.start()
                if not self.penny_controller.is_running:
                    self.penny_controller.start()

                await asyncio.sleep(5)

            except Exception as e:
                log.error(f"Supervisor error: {e}")
                await asyncio.sleep(10)
